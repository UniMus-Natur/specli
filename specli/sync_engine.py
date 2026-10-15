"""Pure status, repository validation, resolver simulation, and staged replace."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from specli.form_family import canonical_sha256, parse_xml
from specli.repository_v3 import (
    API_VERSION,
    INVENTORY_FILE,
    STATE_DIR,
    WORKSPACE_FILE,
    ContextState,
    Inventory,
    ScopeSelector,
    compile_schema,
    compile_viewset,
)

ChangeKind = Literal[
    "clean",
    "local-update",
    "remote-drift",
    "conflict",
    "create",
    "delete",
    "already-applied",
]


def _exit(message: str) -> "None":
    sys.exit(message)


@dataclass(frozen=True)
class ResourceState:
    uid: str
    canonical_sha256: str
    payload: str | None = None
    version: int | None = None
    object_id: int | None = None
    data_id: int | None = None


@dataclass(frozen=True)
class StatusEntry:
    uid: str
    kind: ChangeKind
    baseline: str | None
    local: str | None
    remote: str | None


@dataclass(frozen=True)
class ResolverContext:
    discipline: str
    discipline_type: str
    collection: str
    user_type: str
    username: str


@dataclass(frozen=True)
class ViewProvider:
    view_name: str
    source: str
    scope: ScopeSelector
    viewset_name: str
    manifest_path: str
    canonical_sha256: str
    registry_order: int


def compare_three_way(
    baseline: dict[str, ResourceState],
    local: dict[str, ResourceState],
    remote: dict[str, ResourceState],
    *,
    explicit_deletions: set[str] | None = None,
) -> list[StatusEntry]:
    explicit_deletions = explicit_deletions or set()
    out: list[StatusEntry] = []
    for uid in sorted(set(baseline) | set(local) | set(remote)):
        before = baseline.get(uid)
        desired = local.get(uid)
        current = remote.get(uid)
        b = before.canonical_sha256 if before else None
        l = desired.canonical_sha256 if desired else None
        r = current.canonical_sha256 if current else None

        if desired is None and uid in explicit_deletions:
            if before is None:
                _exit(f"{uid}: deletion has no observed baseline")
            kind: ChangeKind = "clean" if current is None else "delete"
        elif desired is None:
            if before is not None or current is not None:
                _exit(f"{uid}: resource absence is not an explicit deletion")
            continue
        elif before is None and current is None:
            kind = "create"
        elif before is None and current is not None:
            kind = "already-applied" if l == r else "conflict"
        elif l == b and r == b:
            kind = "clean"
        elif l != b and r == b:
            kind = "local-update"
        elif l == b and r != b:
            kind = "remote-drift"
        elif l == r:
            kind = "already-applied"
        else:
            kind = "conflict"
        out.append(StatusEntry(uid, kind, b, l, r))
    return out


def _hash_json(data: Any) -> str:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _read_exact_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def local_resource_states(root: Path) -> dict[str, ResourceState]:
    """Compile every present inventory resource and return desired hashes."""
    inventory = Inventory.load(root)
    states: dict[str, ResourceState] = {}
    for entry in inventory.entries:
        if entry.status != "present":
            continue
        if not entry.path:
            _exit(f"{entry.uid}: present inventory entry has no path")
        path = root / entry.path
        if entry.kind == "Viewset":
            _, payload = compile_viewset(root, path)
            digest = canonical_sha256(payload)
        elif entry.kind == "SchemaLocalization":
            payload_obj = compile_schema(root, path)
            payload = json.dumps(payload_obj, sort_keys=True)
            digest = _hash_json(payload_obj)
        elif entry.kind == "AppResource":
            import yaml

            metadata = yaml.safe_load(path.read_text(encoding="utf-8"))
            data_path = root / str(metadata.get("data") or "")
            if not data_path.is_file():
                _exit(f"{path}: missing app resource data {data_path}")
            payload = _read_exact_text(data_path)
            if data_path.suffix.lower() == ".json":
                digest = _hash_json(json.loads(payload))
            else:
                digest = f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"
        elif entry.kind == "Picklist":
            document = json.loads(path.read_text(encoding="utf-8"))
            payload_obj = {
                "definition": document.get("definition"),
                "items": document.get("items"),
            }
            payload = json.dumps(payload_obj, sort_keys=True)
            digest = _hash_json(payload_obj)
        else:
            _exit(f"{entry.uid}: unsupported inventory kind {entry.kind!r}")
        states[entry.uid] = ResourceState(
            uid=entry.uid,
            canonical_sha256=digest,
            payload=payload,
        )
    return states


def baseline_states(state: ContextState) -> dict[str, ResourceState]:
    out: dict[str, ResourceState] = {}
    for uid, raw in state.resources.items():
        if not isinstance(raw, dict):
            _exit(f"{uid}: observed state must be a mapping")
        digest = str(raw.get("canonicalSha256") or "")
        if not digest.startswith("sha256:"):
            _exit(f"{uid}: observed state has invalid canonicalSha256")
        out[uid] = ResourceState(
            uid=uid,
            canonical_sha256=digest,
            version=int(raw["version"]) if raw.get("version") is not None else None,
            object_id=int(raw["objectId"]) if raw.get("objectId") is not None else None,
            data_id=int(raw["dataId"]) if raw.get("dataId") is not None else None,
        )
    return out


def explicit_deletion_uids(root: Path) -> set[str]:
    inventory = Inventory.load(root)
    return {entry.uid for entry in inventory.entries if entry.status == "absent"}


def validate_repository(root: Path) -> None:
    """Reject unknown or multiply-owned contract files."""
    workspace = root / WORKSPACE_FILE
    if not workspace.is_file():
        _exit(f"missing {workspace}")
    import yaml

    config = yaml.safe_load(workspace.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("apiVersion") != API_VERSION:
        _exit(f"{workspace}: expected {API_VERSION}")
    inventory = Inventory.load(root)

    owned: set[Path] = {
        workspace.resolve(),
        (root / INVENTORY_FILE).resolve(),
    }
    for entry in inventory.entries:
        entry.scope.validate(source=entry.source)
        if entry.status == "excluded":
            if not entry.reason:
                _exit(f"{entry.uid}: excluded resource requires a reason")
            continue
        if entry.status == "absent":
            if entry.path is not None:
                _exit(f"{entry.uid}: absent resource must not have a path")
            continue
        if not entry.path:
            _exit(f"{entry.uid}: present resource has no path")
        path = (root / entry.path).resolve()
        if path in owned:
            _exit(f"{entry.uid}: path is owned by multiple inventory entries: {path}")
        if not path.is_file():
            _exit(f"{entry.uid}: inventory path does not exist: {path}")
        owned.add(path)
        # Compile now so manifests claim their supporting/member files.
        if entry.kind == "Viewset":
            manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
            compile_viewset(root, path)
            for member in manifest.get("members") or []:
                owned.add((root / str(member["path"])).resolve())
            for support_path in (manifest.get("support") or {}).values():
                owned.add((root / str(support_path)).resolve())
        elif entry.kind == "SchemaLocalization":
            manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
            compile_schema(root, path)
            for table in manifest.get("tables") or []:
                owned.add((path.parent / f"{_safe(str(table))}.json").resolve())
        elif entry.kind == "AppResource":
            manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
            data_path = (root / str(manifest.get("data") or "")).resolve()
            if not data_path.is_file():
                _exit(f"{path}: missing app resource data")
            owned.add(data_path)
        elif entry.kind == "Picklist":
            json.loads(path.read_text(encoding="utf-8"))
        else:
            _exit(f"{entry.uid}: unsupported inventory kind {entry.kind!r}")

    state_root = root / STATE_DIR
    if state_root.is_dir():
        owned.update(path.resolve() for path in state_root.glob("*.lock.yaml"))

    contract_roots = ("forms", "viewsets", "schema", "resources", "records", STATE_DIR)
    unknown: list[str] = []
    for name in contract_roots:
        base = root / name
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.resolve() not in owned and not path.name.startswith("."):
                unknown.append(path.relative_to(root).as_posix())
    if unknown:
        _exit(f"unlisted repository files: {sorted(unknown)}")


def _safe(value: str) -> str:
    from specli.client import safe_slug

    return safe_slug(value)


def _scope_applies(scope: ScopeSelector, context: ResolverContext) -> bool:
    if scope.level in ("common", "backstop"):
        return True
    if scope.level == "discipline-type":
        return _safe(scope.discipline_type or "") == _safe(context.discipline_type)
    if scope.level == "discipline":
        return _safe(scope.discipline or "") == _safe(context.discipline)
    if scope.level == "collection":
        return (
            _safe(scope.discipline or "") == _safe(context.discipline)
            and _safe(scope.collection or "") == _safe(context.collection)
        )
    if scope.level == "usertype":
        discipline_match = _safe(
            scope.discipline or scope.discipline_type or ""
        ) in {_safe(context.discipline), _safe(context.discipline_type)}
        collection_match = (
            scope.collection is None
            or _safe(scope.collection) == _safe(context.collection)
        )
        return (
            discipline_match
            and collection_match
            and _safe(scope.user_type or "") == _safe(context.user_type)
        )
    if scope.level == "personal":
        return (
            _safe(scope.discipline or "") == _safe(context.discipline)
            and _safe(scope.collection or "") == _safe(context.collection)
            and _safe(scope.username or "") == _safe(context.username)
        )
    return False


_LEVEL_RANK = {
    "personal": 0,
    "usertype": 1,
    "collection": 2,
    "discipline": 3,
    "discipline-type": 3,
    "common": 4,
    "backstop": 5,
}


def resolve_effective_views(
    providers: Iterable[ViewProvider],
    *,
    context: ResolverContext,
) -> dict[str, ViewProvider]:
    """Simulate current Specify: all DB scopes, then all disk scopes."""
    candidates = [p for p in providers if _scope_applies(p.scope, context)]
    grouped: dict[str, list[ViewProvider]] = {}
    for provider in candidates:
        grouped.setdefault(provider.view_name, []).append(provider)
    effective: dict[str, ViewProvider] = {}
    for view_name, matches in grouped.items():
        matches.sort(
            key=lambda p: (
                0 if p.source == "managed" else 1,
                _LEVEL_RANK[p.scope.level],
                p.registry_order,
                p.manifest_path,
            )
        )
        winner = matches[0]
        same_rank = [
            item
            for item in matches
            if (
                item.source,
                _LEVEL_RANK[item.scope.level],
                item.registry_order if item.source == "vendor" else 0,
            )
            == (
                winner.source,
                _LEVEL_RANK[winner.scope.level],
                winner.registry_order if winner.source == "vendor" else 0,
            )
        ]
        if len(same_rank) > 1:
            locations = sorted(item.manifest_path for item in same_rank)
            _exit(
                f"view {view_name!r} has nondeterministic providers at the same "
                f"precedence: {locations}"
            )
        effective[view_name] = winner
    return effective


def repository_view_providers(root: Path) -> list[ViewProvider]:
    import yaml

    inventory = Inventory.load(root)
    out: list[ViewProvider] = []
    for entry in inventory.entries:
        if entry.status != "present" or entry.kind != "Viewset" or not entry.path:
            continue
        manifest_path = root / entry.path
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        metadata = manifest.get("metadata") or {}
        _, xml = compile_viewset(root, manifest_path)
        parsed = parse_xml(xml, where=str(manifest_path))
        views = parsed.find("views")
        if views is None:
            _exit(f"{manifest_path}: compiled viewset has no views")
        registry_order = int(metadata.get("registryOrder") or 0)
        for view in list(views):
            if not isinstance(view.tag, str):
                continue
            out.append(
                ViewProvider(
                    view_name=str(view.attrib.get("name") or ""),
                    source=entry.source,
                    scope=entry.scope,
                    viewset_name=str(metadata.get("name") or ""),
                    manifest_path=entry.path,
                    canonical_sha256=canonical_sha256(
                        ET_to_text(view)
                    ),
                    registry_order=registry_order,
                )
            )
    return out


def ET_to_text(element: Any) -> str:
    from xml.etree import ElementTree as ET

    return ET.tostring(element, encoding="unicode")


def staged_repository(
    destination: Path,
    builder: Callable[[Path], None],
) -> None:
    """Build/validate elsewhere, then replace v3 roots with rollback."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.stage-",
        dir=destination.parent,
    ) as tmp:
        stage = Path(tmp)
        builder(stage)
        validate_repository(stage)

        names = (
            "forms",
            "viewsets",
            "schema",
            "resources",
            "records",
            STATE_DIR,
            WORKSPACE_FILE,
            INVENTORY_FILE,
        )
        backup = Path(tmp + "-backup")
        backup.mkdir()
        moved_old: list[str] = []
        moved_new: list[str] = []
        try:
            destination.mkdir(parents=True, exist_ok=True)
            for name in names:
                old = destination / name
                if old.exists():
                    os.replace(old, backup / name)
                    moved_old.append(name)
            for name in names:
                new = stage / name
                if new.exists():
                    os.replace(new, destination / name)
                    moved_new.append(name)
        except Exception:
            for name in reversed(moved_new):
                new_destination = destination / name
                if new_destination.exists():
                    if new_destination.is_dir():
                        shutil.rmtree(new_destination)
                    else:
                        new_destination.unlink()
            for name in reversed(moved_old):
                os.replace(backup / name, destination / name)
            raise
        finally:
            if backup.exists():
                shutil.rmtree(backup)
