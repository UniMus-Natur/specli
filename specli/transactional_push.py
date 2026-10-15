"""Compile a v3 repository into one audited transactional CAS deployment."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from specli.client import post_json
from specli.connection import resolve_connection
from specli.form_family import canonical_sha256
from specli.remote_v3 import pull_remote_repository
from specli.repository_v3 import (
    ContextState,
    Inventory,
    InventoryEntry,
    compile_schema,
    compile_viewset,
)
from specli.sync_engine import (
    _read_exact_text,
    baseline_states,
    compare_three_way,
    explicit_deletion_uids,
    local_resource_states,
    validate_repository,
)
from specli.workspace import require_workspace


def _context_name(conn) -> str:
    from specli.commands.gitops_v3_cmd import _context_name

    return _context_name(conn)


def _state_path(root: Path, context_name: str) -> Path:
    from specli.commands.gitops_v3_cmd import _state_path

    return _state_path(root, context_name)


def _yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        sys.exit(f"{path}: expected a mapping")
    return value


def _directory_for_scope(
    inventory: Inventory, state: ContextState, entry: InventoryEntry
) -> int:
    direct = state.directories.get(entry.scope.identity())
    if direct is not None:
        return int(direct)
    matches = {
        int(state.resources[candidate.uid]["directoryId"])
        for candidate in inventory.entries
        if candidate.source == "managed"
        and candidate.scope.identity() == entry.scope.identity()
        and state.resources.get(candidate.uid, {}).get("directoryId") is not None
    }
    if len(matches) != 1:
        sys.exit(
            f"{entry.uid}: scope {entry.scope.label()} does not resolve to exactly "
            f"one SpAppResourceDir (found {sorted(matches)})"
        )
    return next(iter(matches))


def _collection_for_scope(
    inventory: Inventory, state: ContextState, entry: InventoryEntry
) -> int:
    direct = state.collections.get(entry.scope.identity())
    if direct is not None:
        return int(direct)
    matches = {
        int(state.resources[candidate.uid]["collectionId"])
        for candidate in inventory.entries
        if candidate.scope.identity() == entry.scope.identity()
        and state.resources.get(candidate.uid, {}).get("collectionId") is not None
    }
    if len(matches) != 1:
        sys.exit(
            f"{entry.uid}: scope {entry.scope.label()} does not resolve to exactly "
            f"one collection (found {sorted(matches)})"
        )
    return next(iter(matches))


def _expected(observed: dict[str, Any], *, kind: str) -> dict[str, Any]:
    result = {
        "objectId": observed.get("objectId"),
        "objectVersion": observed.get("objectVersion", observed.get("version")),
        "sha256": observed.get("canonicalSha256"),
    }
    if kind in ("Viewset", "AppResource"):
        result.update(
            {
                "dataId": observed.get("dataId"),
                "dataVersion": observed.get("version"),
                "directoryId": observed.get("directoryId"),
            }
        )
    if kind == "Picklist":
        result["itemVersions"] = observed.get("itemVersions")
    return result


def _desired(
    root: Path,
    entry: InventoryEntry,
    *,
    remote_inventory: Inventory,
    remote_state: ContextState,
) -> dict[str, Any]:
    if not entry.path:
        return {}
    path = root / entry.path
    if entry.kind == "Viewset":
        manifest = _yaml(path)
        metadata = manifest["metadata"]
        name, payload = compile_viewset(root, path)
        return {
            "directoryId": _directory_for_scope(
                remote_inventory, remote_state, entry
            ),
            "name": str(metadata.get("remoteName") or name),
            "filename": metadata.get("filename"),
            "description": metadata.get("description"),
            "metadata": metadata.get("remoteMetadata"),
            "level": int(metadata.get("level") or 0),
            "payload": payload,
        }
    if entry.kind == "AppResource":
        manifest = _yaml(path)
        metadata = manifest["metadata"]
        data_path = root / str(manifest["data"])
        return {
            "directoryId": _directory_for_scope(
                remote_inventory, remote_state, entry
            ),
            "name": str(metadata["name"]),
            "mimetype": str(metadata["mimetype"]),
            "description": metadata.get("remoteDescription"),
            "metadata": metadata.get("remoteMetadata"),
            "level": int(metadata.get("level") or 0),
            "allPermissionLevel": metadata.get("allPermissionLevel"),
            "groupPermissionLevel": metadata.get("groupPermissionLevel"),
            "ownerPermissionLevel": metadata.get("ownerPermissionLevel"),
            "payload": _read_exact_text(data_path),
        }
    if entry.kind == "Picklist":
        document = json.loads(path.read_text(encoding="utf-8"))
        return {
            "collectionId": _collection_for_scope(
                remote_inventory, remote_state, entry
            ),
            "definition": document["definition"],
            "items": document["items"],
        }
    sys.exit(f"{entry.uid}: cannot compile {entry.kind!r} as a record operation")


def build_deployment(
    local_root: Path,
    remote_root: Path,
    *,
    baseline: ContextState,
) -> tuple[dict[str, Any], list[str]]:
    local_inventory = Inventory.load(local_root)
    remote_inventory = Inventory.load(remote_root)
    remote_state_path = next((remote_root / "state").glob("*.lock.yaml"))
    remote_state = ContextState.load(remote_state_path)
    local_states = local_resource_states(local_root)
    remote_states = local_resource_states(remote_root)
    statuses = compare_three_way(
        baseline_states(baseline),
        local_states,
        remote_states,
        explicit_deletions=explicit_deletion_uids(local_root),
    )
    actionable = {
        status.uid: status
        for status in statuses
        if status.kind not in ("clean", "already-applied")
    }
    invalid = [
        status
        for status in actionable.values()
        if status.kind in ("conflict", "remote-drift")
    ]
    if invalid:
        sys.exit(
            "push rejected by three-way status: "
            + ", ".join(f"{item.kind}:{item.uid}" for item in invalid)
        )

    entries = {entry.uid: entry for entry in local_inventory.entries}
    operations: list[dict[str, Any]] = []
    operation_uids: list[str] = []
    for uid, status in sorted(actionable.items()):
        entry = entries.get(uid)
        if entry is None:
            sys.exit(f"{uid}: desired inventory entry is missing")
        if entry.source == "vendor":
            sys.exit(f"{uid}: vendor resources are immutable")
        observed = remote_state.resources.get(uid, baseline.resources.get(uid, {}))
        action = "update" if status.kind == "local-update" else status.kind
        if action not in ("create", "update", "delete"):
            sys.exit(f"{uid}: status {action!r} is not deployable")

        if entry.kind == "SchemaLocalization":
            if action != "update" or not entry.path:
                sys.exit(f"{uid}: schema localization supports updates only")
            local_schema = compile_schema(local_root, local_root / entry.path)
            remote_entry = next(
                (
                    item
                    for item in remote_inventory.entries
                    if item.uid == uid and item.path
                ),
                None,
            )
            if remote_entry is None:
                sys.exit(f"{uid}: remote schema manifest is missing")
            remote_schema = compile_schema(
                remote_root, remote_root / str(remote_entry.path)
            )
            collection_id = int(observed["collectionId"])
            manifest = _yaml(local_root / entry.path)
            language = str(manifest["language"])
            for table_name in sorted(set(local_schema) | set(remote_schema)):
                if table_name not in local_schema or table_name not in remote_schema:
                    sys.exit(
                        f"{uid}: schema table creation/deletion is not supported: "
                        f"{table_name}"
                    )
                if local_schema[table_name] == remote_schema[table_name]:
                    continue
                expected_hash = (
                    "sha256:"
                    + hashlib.sha256(
                        json.dumps(
                            remote_schema[table_name],
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ).encode()
                    ).hexdigest()
                )
                operations.append(
                    {
                        "kind": "SchemaTable",
                        "action": "update",
                        "expected": {
                            "collectionId": collection_id,
                            "tableName": table_name,
                            "language": language,
                            "sha256": expected_hash,
                        },
                        "desired": {"table": local_schema[table_name]},
                    }
                )
                operation_uids.append(uid)
            continue

        operations.append(
            {
                "kind": entry.kind,
                "action": action,
                "expected": {} if action == "create" else _expected(observed, kind=entry.kind),
                "desired": (
                    {}
                    if action == "delete"
                    else _desired(
                        local_root,
                        entry,
                        remote_inventory=remote_inventory,
                        remote_state=remote_state,
                    )
                ),
            }
        )
        operation_uids.append(uid)

    material = json.dumps(operations, sort_keys=True, separators=(",", ":"))
    request = {
        "apiVersion": "specli/deployment-v1",
        "idempotencyKey": f"specli-{hashlib.sha256(material.encode()).hexdigest()}",
        "operations": operations,
    }
    return request, operation_uids


def cmd_push(args) -> int:
    root = require_workspace()
    validate_repository(root)
    conn = resolve_connection(login_collection_override=args.login_collection)
    context_name = _context_name(conn)
    baseline = ContextState.load(_state_path(root, context_name))

    with tempfile.TemporaryDirectory(prefix=".specli-push-") as tmp:
        remote_root = Path(tmp)
        pull_remote_repository(
            remote_root,
            conn=conn,
            context_name=context_name,
            include_personal=args.include_personal,
            language=args.lang,
        )
        request, operation_uids = build_deployment(
            root, remote_root, baseline=baseline
        )
        if not request["operations"]:
            print("[push] no changes")
            return 0
        if args.dry_run:
            print(json.dumps(request, indent=2, sort_keys=True))
            return 2

        session, _, _, _ = conn.session(log_prefix="push")
        response = post_json(
            session, conn.url, "/api/gitops/deploy/", request
        )
        session.close()

        remote_state = ContextState.load(
            next((remote_root / "state").glob("*.lock.yaml"))
        )
        local_states = local_resource_states(root)
        for uid, result in zip(operation_uids, response["results"]):
            if result["action"] == "delete":
                remote_state.resources.pop(uid, None)
                continue
            prior = remote_state.resources.get(uid, {})
            if result["kind"] == "SchemaTable":
                remote_state.resources[uid] = {
                    **prior,
                    "canonicalSha256": local_states[uid].canonical_sha256,
                }
            else:
                remote_state.resources[uid] = {
                    **prior,
                    **{
                        key: value
                        for key, value in result.items()
                        if key
                        in {
                            "objectId",
                            "dataId",
                            "objectVersion",
                            "dataVersion",
                            "itemVersions",
                        }
                    },
                    "version": result.get(
                        "dataVersion", result.get("objectVersion")
                    ),
                    "canonicalSha256": result["sha256"],
                }
        remote_state.write(root)
    print(
        f"[push] committed {len(request['operations'])} operations "
        f"as {request['idempotencyKey']}"
    )
    return 0
