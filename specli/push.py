"""Push a specli/v3 repository to Specify using the stock HTTP APIs."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from specli.client import (
    delete_json,
    get_json,
    iter_list_endpoint,
    post_json,
    put_json,
    strip_meta_for_put,
)
from specli.connection import Connection, resolve_connection
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


def _context_name(conn: Connection) -> str:
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


def _sha256_text(payload: str) -> str:
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


def _sha256_json(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(raw.encode()).hexdigest()}"


def _uri(table: str, pk: int) -> str:
    return f"/api/specify/{table}/{int(pk)}/"


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
        result["collectionId"] = observed.get("collectionId")
    if kind == "SchemaLocalization":
        result["collectionId"] = observed.get("collectionId")
        result["disciplineId"] = observed.get("disciplineId")
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
            "name": str(document.get("name") or entry.uid),
            "definition": document["definition"],
            "items": document["items"],
        }
    if entry.kind == "SchemaLocalization":
        manifest = _yaml(path)
        return {
            "language": str(manifest["language"]),
            "schema": compile_schema(root, path),
        }
    sys.exit(f"{entry.uid}: cannot compile {entry.kind!r} as a record operation")


def build_push_plan(
    local_root: Path,
    remote_root: Path,
    *,
    baseline: ContextState,
) -> tuple[dict[str, Any], list[str]]:
    """Build a push plan from three-way status (local vs baseline vs remote)."""
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

        if entry.kind == "SchemaLocalization" and action != "update":
            sys.exit(f"{uid}: schema localization supports updates only")

        operations.append(
            {
                "kind": entry.kind,
                "action": action,
                "expected": (
                    {} if action == "create" else _expected(observed, kind=entry.kind)
                ),
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

    return {"operations": operations}, operation_uids


def _require_version(actual: Any, expected: Any, *, label: str) -> None:
    if int(actual or 0) != int(expected or 0):
        sys.exit(
            f"{label}: version conflict (remote={actual!r}, expected={expected!r}). "
            "Run specli pull / status and retry."
        )


def _current_user_uri(session, base: str) -> str:
    user = get_json(session, base, "/context/user.json")
    user_id = user.get("id")
    if user_id is None:
        sys.exit("GET /context/user.json did not return an id")
    return _uri("specifyuser", int(user_id))


def _apply_app_or_viewset(
    session,
    base: str,
    op: dict[str, Any],
    *,
    table: str,
    user_uri: str,
) -> dict[str, Any]:
    action = op["action"]
    desired = op.get("desired") or {}
    expected = op.get("expected") or {}

    if action == "delete":
        object_id = int(expected["objectId"])
        data_id = int(expected["dataId"])
        delete_json(session, base, _uri("spappresourcedata", data_id))
        delete_json(session, base, _uri(table, object_id))
        return {"action": "delete", "kind": op["kind"]}

    if action == "create":
        directory_id = int(desired["directoryId"])
        body: dict[str, Any] = {
            "name": desired["name"],
            "level": int(desired.get("level") or 0),
            "spappresourcedir": _uri("spappresourcedir", directory_id),
            "description": desired.get("description"),
            "metadata": desired.get("metadata"),
            "filename": desired.get("filename"),
        }
        if table == "spappresource":
            body.update(
                {
                    "mimetype": desired.get("mimetype"),
                    "specifyuser": user_uri,
                    "allpermissionlevel": desired.get("allPermissionLevel"),
                    "grouppermissionlevel": desired.get("groupPermissionLevel"),
                    "ownerpermissionlevel": desired.get("ownerPermissionLevel"),
                }
            )
        created = post_json(session, base, f"/api/specify/{table}/", body)
        object_id = int(created["id"])
        data_body = {
            "data": desired["payload"],
            "spappresource": created["resource_uri"] if table == "spappresource" else None,
            "spviewsetobj": created["resource_uri"] if table == "spviewsetobj" else None,
        }
        data = post_json(session, base, "/api/specify/spappresourcedata/", data_body)
        return {
            "action": "create",
            "kind": op["kind"],
            "objectId": object_id,
            "dataId": int(data["id"]),
            "objectVersion": int(created.get("version") or 0),
            "dataVersion": int(data.get("version") or 0),
            "directoryId": directory_id,
            "sha256": _sha256_text(str(desired["payload"])),
        }

    # update
    object_id = int(expected["objectId"])
    data_id = int(expected["dataId"])
    obj = get_json(session, base, _uri(table, object_id))
    _require_version(
        obj.get("version"), expected.get("objectVersion"), label=_uri(table, object_id)
    )
    body = strip_meta_for_put(obj)
    body["name"] = desired["name"]
    body["level"] = int(desired.get("level") or 0)
    body["description"] = desired.get("description")
    body["metadata"] = desired.get("metadata")
    body["filename"] = desired.get("filename")
    body["version"] = int(expected["objectVersion"])
    if table == "spappresource":
        body["mimetype"] = desired.get("mimetype")
        body["allpermissionlevel"] = desired.get("allPermissionLevel")
        body["grouppermissionlevel"] = desired.get("groupPermissionLevel")
        body["ownerpermissionlevel"] = desired.get("ownerPermissionLevel")
    updated = put_json(session, base, _uri(table, object_id), body)

    data = get_json(session, base, _uri("spappresourcedata", data_id))
    _require_version(
        data.get("version"),
        expected.get("dataVersion"),
        label=_uri("spappresourcedata", data_id),
    )
    data_body = strip_meta_for_put(data)
    data_body["data"] = desired["payload"]
    data_body["version"] = int(expected["dataVersion"])
    data_updated = put_json(session, base, _uri("spappresourcedata", data_id), data_body)
    return {
        "action": "update",
        "kind": op["kind"],
        "objectId": object_id,
        "dataId": data_id,
        "objectVersion": int(updated.get("version") or 0),
        "dataVersion": int(data_updated.get("version") or 0),
        "directoryId": expected.get("directoryId") or desired.get("directoryId"),
        "sha256": _sha256_text(str(desired["payload"])),
    }


def _apply_picklist(session, base: str, op: dict[str, Any]) -> dict[str, Any]:
    action = op["action"]
    desired = op.get("desired") or {}
    expected = op.get("expected") or {}
    definition = dict(desired.get("definition") or {})
    items = list(desired.get("items") or [])

    if action == "delete":
        object_id = int(expected["objectId"])
        remote_items = iter_list_endpoint(
            session, base, f"/api/specify/picklistitem/?picklist={object_id}"
        )
        for item in remote_items:
            delete_json(session, base, _uri("picklistitem", int(item["id"])))
        delete_json(session, base, _uri("picklist", object_id))
        return {"action": "delete", "kind": "Picklist"}

    if action == "create":
        collection_id = int(desired["collectionId"])
        body = {
            **definition,
            "name": desired.get("name") or definition.get("name"),
            "collection": _uri("collection", collection_id),
        }
        created = post_json(session, base, "/api/specify/picklist/", body)
        object_id = int(created["id"])
        item_versions: dict[str, int] = {}
        for ordinal, item in enumerate(items):
            item_body = {
                **item,
                "ordinal": item.get("ordinal", ordinal),
                "picklist": created["resource_uri"],
            }
            saved = post_json(session, base, "/api/specify/picklistitem/", item_body)
            item_versions[str(int(saved["id"]))] = int(saved.get("version") or 0)
        return {
            "action": "create",
            "kind": "Picklist",
            "objectId": object_id,
            "objectVersion": int(created.get("version") or 0),
            "collectionId": collection_id,
            "itemVersions": item_versions,
            "sha256": _sha256_json({"definition": definition, "items": items}),
        }

    object_id = int(expected["objectId"])
    obj = get_json(session, base, _uri("picklist", object_id))
    _require_version(
        obj.get("version"),
        expected.get("objectVersion"),
        label=_uri("picklist", object_id),
    )
    body = strip_meta_for_put(obj)
    for key, value in definition.items():
        body[key] = value
    body["name"] = desired.get("name") or body.get("name")
    body["version"] = int(expected["objectVersion"])
    updated = put_json(session, base, _uri("picklist", object_id), body)

    remote_items = iter_list_endpoint(
        session, base, f"/api/specify/picklistitem/?picklist={object_id}"
    )
    remote_by_value = {
        str(item.get("value") or item.get("title") or item.get("id")): item
        for item in remote_items
    }
    desired_keys: set[str] = set()
    item_versions: dict[str, int] = {}
    for ordinal, item in enumerate(items):
        key = str(item.get("value") or item.get("title") or ordinal)
        desired_keys.add(key)
        if key in remote_by_value:
            remote = remote_by_value[key]
            item_id = int(remote["id"])
            expected_item_version = (expected.get("itemVersions") or {}).get(str(item_id))
            if expected_item_version is not None:
                _require_version(
                    remote.get("version"),
                    expected_item_version,
                    label=_uri("picklistitem", item_id),
                )
            item_body = strip_meta_for_put(remote)
            item_body.update(item)
            item_body["ordinal"] = item.get("ordinal", ordinal)
            item_body["version"] = int(remote.get("version") or 0)
            saved = put_json(session, base, _uri("picklistitem", item_id), item_body)
        else:
            item_body = {
                **item,
                "ordinal": item.get("ordinal", ordinal),
                "picklist": _uri("picklist", object_id),
            }
            saved = post_json(session, base, "/api/specify/picklistitem/", item_body)
        item_versions[str(int(saved["id"]))] = int(saved.get("version") or 0)

    for key, remote in remote_by_value.items():
        if key not in desired_keys:
            delete_json(session, base, _uri("picklistitem", int(remote["id"])))

    return {
        "action": "update",
        "kind": "Picklist",
        "objectId": object_id,
        "objectVersion": int(updated.get("version") or 0),
        "collectionId": expected.get("collectionId") or desired.get("collectionId"),
        "itemVersions": item_versions,
        "sha256": _sha256_json({"definition": definition, "items": items}),
    }


def _apply_schema(
    conn: Connection,
    session,
    base: str,
    op: dict[str, Any],
) -> dict[str, Any]:
    if op["action"] != "update":
        sys.exit("schema localization supports updates only")
    desired = op["desired"]
    expected = op["expected"]
    collection_id = int(desired.get("collectionId") or expected["collectionId"])
    language = str(desired["language"])
    schema = desired["schema"]

    # Schema import is scoped to the logged-in collection.
    collection = get_json(session, base, _uri("collection", collection_id))
    collection_name = str(collection.get("collectionname") or collection.get("name") or "")
    scoped, _, _, _ = conn.session(
        login_collection=collection_name,
        log_prefix="push-schema",
    )
    try:
        url = f"{conn.url}/context/schema_localization_import.json?lang={quote(language)}"
        res = scoped.post(
            url,
            json={"language": language, "schema": schema},
            headers={
                "X-CSRFToken": scoped.cookies.get("csrftoken", ""),
                "Referer": conn.url,
                "Content-Type": "application/json",
            },
            timeout=300,
        )
        if not res.ok:
            sys.exit(
                f"POST /context/schema_localization_import.json failed "
                f"({res.status_code}): {res.text[:800]}"
            )
    finally:
        scoped.close()

    return {
        "action": "update",
        "kind": "SchemaLocalization",
        "collectionId": collection_id,
        "sha256": _sha256_json(schema),
    }


def apply_push_plan(
    conn: Connection,
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply push operations through Specify's public REST / context APIs."""
    session, _, _, _ = conn.session(log_prefix="push")
    base = conn.url.rstrip("/")
    user_uri = _current_user_uri(session, base)
    results: list[dict[str, Any]] = []
    try:
        for index, op in enumerate(plan["operations"]):
            kind = op["kind"]
            action = op["action"]
            print(
                f"[push] {index + 1}/{len(plan['operations'])} "
                f"{action} {kind}",
                file=sys.stderr,
            )
            if kind == "AppResource":
                results.append(
                    _apply_app_or_viewset(
                        session,
                        base,
                        op,
                        table="spappresource",
                        user_uri=user_uri,
                    )
                )
            elif kind == "Viewset":
                results.append(
                    _apply_app_or_viewset(
                        session,
                        base,
                        op,
                        table="spviewsetobj",
                        user_uri=user_uri,
                    )
                )
            elif kind == "Picklist":
                results.append(_apply_picklist(session, base, op))
            elif kind == "SchemaLocalization":
                results.append(_apply_schema(conn, session, base, op))
            else:
                sys.exit(f"unsupported push kind {kind!r}")
    finally:
        session.close()
    return results


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
        plan, operation_uids = build_push_plan(
            root, remote_root, baseline=baseline
        )
        if not plan["operations"]:
            print("[push] no changes")
            return 0
        if args.dry_run:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 2

        results = apply_push_plan(conn, plan)

        remote_state = ContextState.load(
            next((remote_root / "state").glob("*.lock.yaml"))
        )
        local_states = local_resource_states(root)
        for uid, result in zip(operation_uids, results):
            if result["action"] == "delete":
                remote_state.resources.pop(uid, None)
                continue
            prior = remote_state.resources.get(uid, {})
            if result["kind"] == "SchemaLocalization":
                remote_state.resources[uid] = {
                    **prior,
                    "canonicalSha256": local_states[uid].canonical_sha256,
                    "collectionId": result.get("collectionId", prior.get("collectionId")),
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
                            "directoryId",
                            "collectionId",
                        }
                    },
                    "version": result.get(
                        "dataVersion", result.get("objectVersion")
                    ),
                    "canonicalSha256": result.get(
                        "sha256", local_states[uid].canonical_sha256
                    ),
                }
        remote_state.write(root)

    print(f"[push] applied {len(operation_uids)} operations via Specify API")
    return 0
