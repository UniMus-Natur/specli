"""Filesystem contract for a specli/v3 Specify GitOps repository."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml

from specli.client import safe_slug
from specli.form_family import (
    assemble_fragments,
    canonical_sha256,
    family_xml,
    orphan_xml,
    root_misc_xml,
    split_viewset,
)

API_VERSION = "specli/v3"
WORKSPACE_FILE = "workspace.yaml"
INVENTORY_FILE = "inventory.yaml"
STATE_DIR = "state"

Source = Literal["vendor", "managed"]
ResourceStatus = Literal["present", "excluded", "absent"]

_LEVELS = frozenset(
    {
        "personal",
        "usertype",
        "collection",
        "discipline",
        "discipline-type",
        "common",
        "backstop",
    }
)


def _exit(message: str) -> "None":
    sys.exit(message)


def _slug(value: str, *, label: str) -> str:
    text = (value or "").strip()
    if not text:
        _exit(f"{label} must not be empty")
    slug = safe_slug(text)
    if slug == "unknown":
        _exit(f"{label} cannot produce the reserved slug 'unknown'")
    return slug


def _yaml_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _yaml_read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        _exit(f"missing required file: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        _exit(f"{path}: expected a YAML mapping")
    return data


def _json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True)
class ScopeSelector:
    level: str
    discipline: str | None = None
    discipline_type: str | None = None
    collection: str | None = None
    user_type: str | None = None
    username: str | None = None

    def validate(self, *, source: Source) -> None:
        if self.level not in _LEVELS:
            _exit(f"unsupported scope level {self.level!r}")
        required: dict[str, tuple[str, ...]] = {
            "discipline": ("discipline",),
            "discipline-type": ("discipline_type",),
            "collection": ("discipline", "collection"),
            "personal": ("discipline", "collection", "username"),
        }
        if self.level == "usertype":
            if source == "vendor":
                if not self.discipline_type or not self.user_type:
                    _exit("vendor usertype scope requires discipline_type and user_type")
            elif not self.discipline or not self.collection or not self.user_type:
                _exit(
                    "managed usertype scope requires discipline, collection, and user_type"
                )
        for field_name in required.get(self.level, ()):
            if not getattr(self, field_name):
                _exit(f"{self.level} scope requires {field_name}")
        if source == "managed" and self.level == "backstop":
            _exit("backstop is vendor-only")
        if source == "managed" and self.level == "discipline-type":
            _exit("discipline-type is a vendor disk scope, not a DB scope")
        if source == "vendor" and self.level in ("personal", "collection"):
            _exit(f"{self.level} has no Specify filesystem source")

    def as_dict(self) -> dict[str, str]:
        values = {
            "level": self.level,
            "discipline": self.discipline,
            "disciplineType": self.discipline_type,
            "collection": self.collection,
            "userType": self.user_type,
            "username": self.username,
        }
        return {key: value for key, value in values.items() if value is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScopeSelector":
        allowed = {
            "level",
            "discipline",
            "disciplineType",
            "collection",
            "userType",
            "username",
        }
        extras = sorted(set(data) - allowed)
        if extras:
            _exit(f"scope selector has unknown keys: {extras}")
        return cls(
            level=str(data.get("level") or ""),
            discipline=data.get("discipline"),
            discipline_type=data.get("disciplineType"),
            collection=data.get("collection"),
            user_type=data.get("userType"),
            username=data.get("username"),
        )

    def path(self) -> Path:
        if self.level in ("common", "backstop"):
            return Path(self.level)
        if self.level == "discipline-type":
            return Path("discipline-type") / _slug(
                self.discipline_type or "", label="discipline type"
            )
        if self.level == "discipline":
            return Path("discipline") / _slug(
                self.discipline or "", label="discipline"
            )
        if self.level == "collection":
            return (
                Path("collection")
                / _slug(self.discipline or "", label="discipline")
                / _slug(self.collection or "", label="collection")
            )
        if self.level == "usertype":
            base = Path("usertype")
            if self.discipline_type:
                base = base / "discipline-type" / _slug(
                    self.discipline_type, label="discipline type"
                )
            else:
                base = (
                    base
                    / "discipline"
                    / _slug(self.discipline or "", label="discipline")
                    / "collection"
                    / _slug(self.collection or "", label="collection")
                )
            return base / _slug(self.user_type or "", label="user type")
        if self.level == "personal":
            return (
                Path("personal")
                / _slug(self.discipline or "", label="discipline")
                / _slug(self.collection or "", label="collection")
                / _slug(self.username or "", label="username")
            )
        _exit(f"unsupported scope level {self.level!r}")

    def identity(self) -> str:
        body = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
        return f"scope:{self.level}:{digest}"


@dataclass
class InventoryEntry:
    uid: str
    kind: str
    source: Source
    status: ResourceStatus
    path: str | None
    scope: ScopeSelector
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "uid": self.uid,
            "kind": self.kind,
            "source": self.source,
            "status": self.status,
            "scope": self.scope.as_dict(),
        }
        if self.path is not None:
            out["path"] = self.path
        if self.reason is not None:
            out["reason"] = self.reason
        return out


@dataclass
class Inventory:
    entries: list[InventoryEntry] = field(default_factory=list)

    def add(self, entry: InventoryEntry) -> None:
        if any(item.uid == entry.uid for item in self.entries):
            _exit(f"duplicate inventory uid {entry.uid!r}")
        self.entries.append(entry)

    def write(self, root: Path) -> None:
        _yaml_write(
            root / INVENTORY_FILE,
            {
                "apiVersion": API_VERSION,
                "kind": "Inventory",
                "resources": [
                    entry.as_dict() for entry in sorted(self.entries, key=lambda e: e.uid)
                ],
            },
        )

    @classmethod
    def load(cls, root: Path) -> "Inventory":
        data = _yaml_read(root / INVENTORY_FILE)
        if data.get("apiVersion") != API_VERSION or data.get("kind") != "Inventory":
            _exit(f"{root / INVENTORY_FILE}: unsupported inventory contract")
        resources = data.get("resources")
        if not isinstance(resources, list):
            _exit(f"{root / INVENTORY_FILE}: resources must be a list")
        inventory = cls()
        for raw in resources:
            if not isinstance(raw, dict) or not isinstance(raw.get("scope"), dict):
                _exit(f"{root / INVENTORY_FILE}: malformed resource entry")
            inventory.add(
                InventoryEntry(
                    uid=str(raw.get("uid") or ""),
                    kind=str(raw.get("kind") or ""),
                    source=str(raw.get("source") or ""),  # type: ignore[arg-type]
                    status=str(raw.get("status") or ""),  # type: ignore[arg-type]
                    path=raw.get("path"),
                    scope=ScopeSelector.from_dict(raw["scope"]),
                    reason=raw.get("reason"),
                )
            )
        return inventory


@dataclass
class ContextState:
    context: str
    instance: str
    resolver_contract: str
    resources: dict[str, dict[str, Any]] = field(default_factory=dict)
    directories: dict[str, int] = field(default_factory=dict)
    collections: dict[str, int] = field(default_factory=dict)

    def write(self, root: Path) -> Path:
        path = root / STATE_DIR / f"{_slug(self.context, label='context')}.lock.yaml"
        _yaml_write(
            path,
            {
                "apiVersion": API_VERSION,
                "kind": "ObservedState",
                "context": self.context,
                "instance": self.instance,
                "resolverContract": self.resolver_contract,
                "resources": dict(sorted(self.resources.items())),
                "directories": dict(sorted(self.directories.items())),
                "collections": dict(sorted(self.collections.items())),
            },
        )
        return path

    @classmethod
    def load(cls, path: Path) -> "ContextState":
        data = _yaml_read(path)
        if data.get("apiVersion") != API_VERSION or data.get("kind") != "ObservedState":
            _exit(f"{path}: unsupported state contract")
        resources = data.get("resources")
        if not isinstance(resources, dict):
            _exit(f"{path}: resources must be a mapping")
        directories = data.get("directories") or {}
        if not isinstance(directories, dict):
            _exit(f"{path}: directories must be a mapping")
        collections = data.get("collections") or {}
        if not isinstance(collections, dict):
            _exit(f"{path}: collections must be a mapping")
        return cls(
            context=str(data.get("context") or ""),
            instance=str(data.get("instance") or ""),
            resolver_contract=str(data.get("resolverContract") or ""),
            resources=resources,
            directories={
                str(key): int(value)
                for key, value in directories.items()
            },
            collections={
                str(key): int(value)
                for key, value in collections.items()
            },
        )


def init_repository(
    root: Path,
    *,
    instance: str | None = None,
    user: str | None = None,
    login_collection: str | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "apiVersion": API_VERSION,
        "kind": "Workspace",
        "instance": instance,
        "resolverContract": "specify7/current",
        "policies": {
            "vendorMutable": False,
            "implicitDeletion": False,
            "personalResources": "excluded",
        },
    }
    if user:
        payload["user"] = user
    if login_collection:
        payload["loginCollection"] = login_collection
    _yaml_write(root / WORKSPACE_FILE, payload)
    Inventory().write(root)


def viewset_uid(source: Source, scope: ScopeSelector, name: str) -> str:
    material = json.dumps(
        {
            "source": source,
            "scope": scope.as_dict(),
            "name": name,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"viewset:{hashlib.sha256(material.encode()).hexdigest()[:16]}"


def _viewset_manifest_path(
    root: Path,
    *,
    source: Source,
    scope: ScopeSelector,
    name: str,
) -> Path:
    return (
        root
        / "viewsets"
        / source
        / scope.path()
        / f"{_slug(name, label='viewset name')}.yaml"
    )


def _family_path(
    root: Path,
    *,
    table: str,
    family: str,
    source: Source,
    scope: ScopeSelector,
    viewset_name: str,
) -> Path:
    return (
        root
        / "forms"
        / table
        / family
        / source
        / scope.path()
        / f"{_slug(viewset_name, label='viewset name')}.xml"
    )


def write_viewset(
    root: Path,
    *,
    xml: str,
    source: Source,
    scope: ScopeSelector,
    known_unresolved: Iterable[str] | None = None,
) -> tuple[Path, InventoryEntry]:
    """Split and write one exact viewset plus its authoritative manifest."""
    scope.validate(source=source)
    split = split_viewset(xml, where=f"{source}:{scope.as_dict()}")
    name = (split.root_attributes.get("name") or "").strip()
    uid = viewset_uid(source, scope, name)
    members: list[dict[str, str]] = []

    for family in split.families:
        path = _family_path(
            root,
            table=family.table,
            family=family.name,
            source=source,
            scope=scope,
            viewset_name=name,
        )
        payload = family_xml(family, split.root_attributes)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        members.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": canonical_sha256(payload),
            }
        )

    manifest_path = _viewset_manifest_path(
        root, source=source, scope=scope, name=name
    )
    support: dict[str, str] = {}
    orphan_payload = orphan_xml(split.orphan_viewdefs, split.root_attributes)
    if orphan_payload is not None:
        orphan_path = manifest_path.with_suffix("") / "orphans.xml"
        orphan_path.parent.mkdir(parents=True, exist_ok=True)
        orphan_path.write_text(orphan_payload, encoding="utf-8")
        support["orphans"] = orphan_path.relative_to(root).as_posix()
    misc_payload = root_misc_xml(split.root_misc)
    if misc_payload is not None:
        misc_path = manifest_path.with_suffix("") / "viewset-misc.xml"
        misc_path.parent.mkdir(parents=True, exist_ok=True)
        misc_path.write_text(misc_payload, encoding="utf-8")
        support["rootMisc"] = misc_path.relative_to(root).as_posix()

    accepted = sorted(set(known_unresolved or ()))
    actual = split.unresolved_viewdefs
    if actual != accepted:
        _exit(
            f"{name!r}: unresolved viewdefs {actual}; accepted baseline is {accepted}. "
            "Pass the exact baseline explicitly."
        )

    manifest = {
        "apiVersion": API_VERSION,
        "kind": "Viewset",
        "metadata": {
            "uid": uid,
            "name": name,
            "source": source,
        },
        "scope": scope.as_dict(),
        "rootAttributes": split.root_attributes,
        "members": members,
        "order": {
            "views": split.view_order,
            "viewdefs": split.viewdef_order,
        },
        "support": support,
        "knownIssues": {"unresolvedViewdefs": actual},
        "deletions": [],
        "canonicalSha256": canonical_sha256(xml),
    }
    _yaml_write(manifest_path, manifest)
    return manifest_path, InventoryEntry(
        uid=uid,
        kind="Viewset",
        source=source,
        status="present",
        path=manifest_path.relative_to(root).as_posix(),
        scope=scope,
    )


def compile_viewset(root: Path, manifest_path: Path) -> tuple[str, str]:
    data = _yaml_read(manifest_path)
    if data.get("apiVersion") != API_VERSION or data.get("kind") != "Viewset":
        _exit(f"{manifest_path}: unsupported viewset manifest")
    metadata = data.get("metadata")
    order = data.get("order")
    members = data.get("members")
    support = data.get("support") or {}
    known = data.get("knownIssues") or {}
    if not all(
        isinstance(value, expected)
        for value, expected in (
            (metadata, dict),
            (order, dict),
            (members, list),
            (support, dict),
            (known, dict),
        )
    ):
        _exit(f"{manifest_path}: malformed viewset manifest")
    root_attributes = data.get("rootAttributes")
    if not isinstance(root_attributes, dict):
        _exit(f"{manifest_path}: rootAttributes must be a mapping")

    fragments: list[tuple[str, str]] = []
    for member in members:
        if not isinstance(member, dict) or not member.get("path"):
            _exit(f"{manifest_path}: malformed member")
        path = root / str(member["path"])
        if not path.is_file():
            _exit(f"{manifest_path}: missing member {path}")
        payload = path.read_text(encoding="utf-8")
        expected = member.get("sha256")
        # The pull hash is evidence, not an edit lock. It is intentionally not
        # required to match after a user edits a family.
        if expected is not None and not str(expected).startswith("sha256:"):
            _exit(f"{manifest_path}: invalid member hash for {path}")
        fragments.append((path.as_posix(), payload))

    def optional_payload(key: str) -> str | None:
        rel = support.get(key)
        if rel is None:
            return None
        path = root / str(rel)
        if not path.is_file():
            _exit(f"{manifest_path}: missing support file {path}")
        return path.read_text(encoding="utf-8")

    assembled = assemble_fragments(
        root_attributes={str(k): str(v) for k, v in root_attributes.items()},
        family_fragments=fragments,
        view_order=[str(v) for v in order.get("views") or []],
        viewdef_order=[str(v) for v in order.get("viewdefs") or []],
        orphans=optional_payload("orphans"),
        root_misc=optional_payload("rootMisc"),
    )
    split = split_viewset(assembled, where=str(manifest_path))
    accepted = sorted(str(v) for v in known.get("unresolvedViewdefs") or [])
    if split.unresolved_viewdefs != accepted:
        _exit(
            f"{manifest_path}: unresolved viewdefs changed: "
            f"{split.unresolved_viewdefs} != {accepted}"
        )
    return str(metadata.get("name") or ""), assembled


def split_schema(
    root: Path,
    *,
    discipline: str,
    language: str,
    schema: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[Path, InventoryEntry]:
    base = (
        root
        / "schema"
        / "managed"
        / "discipline"
        / _slug(discipline, label="discipline")
        / _slug(language, label="language")
    )
    tables: list[str] = []
    for name, value in sorted(schema.items()):
        if not isinstance(value, dict):
            _exit(f"schema table {name!r} must be a mapping")
        path = base / f"{_slug(name, label='schema table')}.json"
        _json_write(path, value)
        tables.append(name)
    manifest = base / "schema.yaml"
    _yaml_write(
        manifest,
        {
            "apiVersion": API_VERSION,
            "kind": "SchemaLocalization",
            "discipline": discipline,
            "language": language,
            "tables": tables,
            "metadata": metadata,
        },
    )
    scope = ScopeSelector(level="discipline", discipline=discipline)
    uid = f"schema:{scope.identity()}:{_slug(language, label='language')}"
    return manifest, InventoryEntry(
        uid=uid,
        kind="SchemaLocalization",
        source="managed",
        status="present",
        path=manifest.relative_to(root).as_posix(),
        scope=scope,
    )


def compile_schema(root: Path, manifest_path: Path) -> dict[str, Any]:
    data = _yaml_read(manifest_path)
    if data.get("apiVersion") != API_VERSION or data.get("kind") != "SchemaLocalization":
        _exit(f"{manifest_path}: unsupported schema manifest")
    tables = data.get("tables")
    if not isinstance(tables, list):
        _exit(f"{manifest_path}: tables must be a list")
    result: dict[str, Any] = {}
    for name in tables:
        slug = _slug(str(name), label="schema table")
        path = manifest_path.parent / f"{slug}.json"
        if not path.is_file():
            _exit(f"{manifest_path}: missing table file {path}")
        result[str(name)] = json.loads(path.read_text(encoding="utf-8"))
    extras = sorted(
        path.name
        for path in manifest_path.parent.glob("*.json")
        if path.stem not in {_slug(str(name), label="schema table") for name in tables}
    )
    if extras:
        _exit(f"{manifest_path}: unlisted schema table files: {extras}")
    return result


def _extension(mimetype: str, name: str) -> str:
    if "xml" in mimetype:
        return ".xml"
    if "json" in mimetype:
        return ".json"
    guessed = mimetypes.guess_extension(mimetype, strict=False)
    return guessed or ".txt"


def write_resource(
    root: Path,
    *,
    name: str,
    payload: str,
    mimetype: str,
    source: Source,
    scope: ScopeSelector,
    metadata: dict[str, Any] | None = None,
) -> tuple[Path, InventoryEntry]:
    scope.validate(source=source)
    base = (
        root
        / "resources"
        / _slug(name, label="resource name")
        / source
        / scope.path()
    )
    data_path = base / ("resource" + _extension(mimetype, name))
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(payload, encoding="utf-8")
    meta_path = base / "resource.yaml"
    uid_material = json.dumps(
        {"name": name, "source": source, "scope": scope.as_dict()},
        sort_keys=True,
    )
    uid = f"resource:{hashlib.sha256(uid_material.encode()).hexdigest()[:16]}"
    _yaml_write(
        meta_path,
        {
            "apiVersion": API_VERSION,
            "kind": "AppResource",
            "metadata": {
                "uid": uid,
                "name": name,
                "source": source,
                "mimetype": mimetype,
                **(metadata or {}),
            },
            "scope": scope.as_dict(),
            "data": data_path.relative_to(root).as_posix(),
            "sha256": f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}",
        },
    )
    return meta_path, InventoryEntry(
        uid=uid,
        kind="AppResource",
        source=source,
        status="present",
        path=meta_path.relative_to(root).as_posix(),
        scope=scope,
    )


def write_picklist(
    root: Path,
    *,
    scope: ScopeSelector,
    name: str,
    definition: dict[str, Any],
    items: list[dict[str, Any]],
) -> tuple[Path, InventoryEntry]:
    scope.validate(source="managed")
    path = (
        root
        / "records"
        / "picklists"
        / scope.path()
        / f"{_slug(name, label='picklist name')}.json"
    )
    _json_write(
        path,
        {
            "apiVersion": API_VERSION,
            "kind": "Picklist",
            "name": name,
            "scope": scope.as_dict(),
            "definition": definition,
            "items": items,
        },
    )
    material = json.dumps(
        {"name": name, "scope": scope.as_dict()}, sort_keys=True
    )
    uid = f"picklist:{hashlib.sha256(material.encode()).hexdigest()[:16]}"
    return path, InventoryEntry(
        uid=uid,
        kind="Picklist",
        source="managed",
        status="present",
        path=path.relative_to(root).as_posix(),
        scope=scope,
    )


def remove_repository_tree(root: Path) -> None:
    """Used only by staged pull after validation, never as an implicit delete."""
    for name in (
        "forms",
        "viewsets",
        "schema",
        "resources",
        "records",
        STATE_DIR,
        WORKSPACE_FILE,
        INVENTORY_FILE,
    ):
        path = root / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
