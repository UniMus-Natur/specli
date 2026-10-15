"""Pull a complete remote Specify snapshot into a staged specli/v3 repository."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

from specli.client import (
    iter_list_endpoint,
    resource_pk,
)
from specli.connection import Connection
from specli.dir_ops import iter_dirs_as_scopes
from specli.disk_config import (
    fetch_static_config,
    list_disk_viewset_paths,
    try_fetch_static_config,
)
from specli.domain import (
    collection_display_name,
    collections_for_discipline,
    list_collections,
    list_disciplines,
    unique_discipline_slug,
)
from specli.form_family import canonical_sha256, split_viewset
from specli.repository_v3 import (
    ContextState,
    Inventory,
    InventoryEntry,
    ScopeSelector,
    init_repository,
    split_schema,
    write_picklist,
    write_resource,
    write_viewset,
)
from specli.scopes import Scope


def _exit(message: str) -> "None":
    sys.exit(message)


def _managed_scope(scope: Scope) -> ScopeSelector:
    if scope.level == "common":
        return ScopeSelector(level="common")
    if scope.level == "discipline":
        return ScopeSelector(
            level="discipline",
            discipline=scope.discipline_slug,
        )
    if scope.level == "collection":
        return ScopeSelector(
            level="collection",
            discipline=scope.discipline_slug,
            collection=scope.collection_slug,
        )
    if scope.level == "usertype":
        return ScopeSelector(
            level="usertype",
            discipline=scope.discipline_slug,
            collection=scope.collection_slug,
            user_type=scope.usertype,
        )
    if scope.level == "personal":
        return ScopeSelector(
            level="personal",
            discipline=scope.discipline_slug,
            collection=scope.collection_slug,
            username=scope.username,
        )
    _exit(f"cannot map managed scope {scope.label()}")


def _vendor_scope(config_dir: str) -> ScopeSelector:
    parts = [part for part in config_dir.strip("/").split("/") if part]
    if parts == ["backstop"]:
        return ScopeSelector(level="backstop")
    if parts == ["common"]:
        return ScopeSelector(level="common")
    if len(parts) == 1:
        return ScopeSelector(level="discipline-type", discipline_type=parts[0])
    if len(parts) == 2:
        return ScopeSelector(
            level="usertype",
            discipline_type=parts[0],
            user_type=parts[1],
        )
    _exit(f"unsupported config directory scope: {config_dir!r}")


def _one_data_row(
    session,
    base: str,
    *,
    relation: str,
    object_id: int,
    label: str,
) -> dict[str, Any]:
    rows = iter_list_endpoint(
        session,
        base,
        f"/api/specify/spappresourcedata/?{relation}={object_id}&limit=5",
    )
    if len(rows) != 1:
        _exit(f"{label}: expected exactly one SpAppResourceData row, found {len(rows)}")
    data = rows[0].get("data")
    if data is None or not str(data).strip():
        _exit(f"{label}: SpAppResourceData is empty")
    return rows[0]


def _registry(
    session,
    base: str,
    *,
    config_dir: str,
    filename: str,
) -> ET.Element | None:
    payload = try_fetch_static_config(session, base, f"{config_dir}/{filename}")
    if payload is None:
        return None
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        _exit(f"/static/config/{config_dir}/{filename}: invalid XML: {exc}")
    if root.tag != "files":
        _exit(f"/static/config/{config_dir}/{filename}: expected <files>")
    return root


def _config_dirs(session, base: str) -> list[str]:
    paths = list_disk_viewset_paths(session, base)
    dirs = {str(Path(path).parent).replace("\\", "/") for path in paths}
    dirs.update({"backstop", "common"})
    return sorted(dirs)


def _pull_vendor_viewsets(
    root: Path,
    *,
    session,
    base: str,
    inventory: Inventory,
    observed: dict[str, dict[str, Any]],
) -> None:
    for config_dir in _config_dirs(session, base):
        registry = _registry(
            session,
            base,
            config_dir=config_dir,
            filename="viewset_registry.xml",
        )
        if registry is None:
            _exit(f"/static/config/{config_dir}: missing viewset_registry.xml")
        seen_files: set[str] = set()
        for registry_order, node in enumerate(registry.findall("file")):
            filename = (node.attrib.get("file") or "").strip()
            name = (node.attrib.get("name") or "").strip()
            if not filename or not name:
                _exit(
                    f"/static/config/{config_dir}/viewset_registry.xml: "
                    "<file> requires name and file"
                )
            if filename in seen_files:
                _exit(f"{config_dir}: duplicate viewset registry file {filename!r}")
            seen_files.add(filename)
            payload = fetch_static_config(session, base, f"{config_dir}/{filename}")
            split = split_viewset(payload, where=f"disk:{config_dir}/{filename}")
            manifest, entry = write_viewset(
                root,
                xml=payload,
                source="vendor",
                scope=_vendor_scope(config_dir),
                known_unresolved=split.unresolved_viewdefs,
            )
            # Registry order is runtime-significant for disk ties.
            import yaml

            raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            raw["metadata"]["registryName"] = name
            raw["metadata"]["registryOrder"] = registry_order
            raw["metadata"]["filename"] = filename
            manifest.write_text(
                yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            inventory.add(entry)
            observed[entry.uid] = {
                "canonicalSha256": canonical_sha256(payload),
                "url": f"/static/config/{config_dir}/{filename}",
            }


def _pull_managed_viewsets(
    root: Path,
    *,
    session,
    base: str,
    inventory: Inventory,
    observed: dict[str, dict[str, Any]],
    include_personal: bool,
) -> None:
    for scope, directory in iter_dirs_as_scopes(session, base):
        selector = _managed_scope(scope)
        dir_id = directory.get("id") or resource_pk(directory.get("resource_uri"))
        if dir_id is None:
            _exit(f"{scope.label()}: SpAppResourceDir has no id")
        rows = iter_list_endpoint(
            session,
            base,
            f"/api/specify/spviewsetobj/?spappresourcedir={int(dir_id)}&limit=500",
        )
        names: set[str] = set()
        for row in rows:
            object_id = row.get("id") or resource_pk(row.get("resource_uri"))
            name = str(row.get("name") or "").strip()
            if object_id is None or not name:
                _exit(f"dir={dir_id}: malformed SpViewSetObj")
            folded = name.casefold()
            if folded in names:
                _exit(f"{scope.label()}: duplicate DB viewset name {name!r}")
            names.add(folded)
            if scope.level == "personal" and not include_personal:
                material = json.dumps(
                    {"kind": "viewset", "scope": selector.as_dict(), "name": name},
                    sort_keys=True,
                )
                import hashlib

                inventory.add(
                    InventoryEntry(
                        uid=f"viewset-excluded:{hashlib.sha256(material.encode()).hexdigest()[:16]}",
                        kind="Viewset",
                        source="managed",
                        status="excluded",
                        path=None,
                        scope=selector,
                        reason="personal resources disabled by workspace policy",
                    )
                )
                continue
            data_row = _one_data_row(
                session,
                base,
                relation="spviewsetobj",
                object_id=int(object_id),
                label=f"{scope.label()}/{name}",
            )
            payload = str(data_row["data"])
            split = split_viewset(payload, where=f"db:{scope.label()}/{name}")
            manifest, entry = write_viewset(
                root,
                xml=payload,
                source="managed",
                scope=selector,
                known_unresolved=split.unresolved_viewdefs,
            )
            import yaml

            raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            raw["metadata"]["remoteName"] = name
            raw["metadata"]["filename"] = row.get("filename")
            raw["metadata"]["description"] = row.get("description")
            raw["metadata"]["remoteMetadata"] = row.get("metadata")
            raw["metadata"]["level"] = int(row.get("level") or 0)
            manifest.write_text(
                yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            inventory.add(entry)
            observed[entry.uid] = {
                "canonicalSha256": canonical_sha256(payload),
                "objectId": int(object_id),
                "dataId": int(data_row["id"]),
                "version": int(data_row.get("version") or 0),
                "objectVersion": int(row.get("version") or 0),
                "directoryId": int(dir_id),
            }


def _pull_vendor_resources(
    root: Path,
    *,
    session,
    base: str,
    inventory: Inventory,
    observed: dict[str, dict[str, Any]],
) -> None:
    for config_dir in _config_dirs(session, base):
        registry = _registry(
            session,
            base,
            config_dir=config_dir,
            filename="app_resources.xml",
        )
        if registry is None:
            continue
        names: set[str] = set()
        for order, node in enumerate(registry.findall("file")):
            name = (node.attrib.get("name") or "").strip()
            filename = (node.attrib.get("file") or "").strip()
            mimetype = (node.attrib.get("mimetype") or "text/plain").strip()
            if not name or not filename:
                _exit(f"{config_dir}/app_resources.xml: file requires name and file")
            if name.casefold() in names:
                _exit(f"{config_dir}: duplicate app resource name {name!r}")
            names.add(name.casefold())
            payload = fetch_static_config(session, base, f"{config_dir}/{filename}")
            manifest, entry = write_resource(
                root,
                name=name,
                payload=payload,
                mimetype=mimetype,
                source="vendor",
                scope=_vendor_scope(config_dir),
                metadata={"filename": filename, "registryOrder": order},
            )
            inventory.add(entry)
            observed[entry.uid] = {
                "canonicalSha256": _resource_hash(
                    payload, mimetype, where=f"disk:{config_dir}/{filename}"
                ),
                "url": f"/static/config/{config_dir}/{filename}",
            }


def _resource_hash(payload: str, mimetype: str, *, where: str) -> str:
    try:
        if "json" in mimetype:
            data = json.loads(payload)
            raw = json.dumps(
                data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            import hashlib

            return f"sha256:{hashlib.sha256(raw.encode()).hexdigest()}"
    except json.JSONDecodeError:
        _exit("resource declares JSON mimetype but contains invalid JSON")
    import hashlib

    # App-resource XML is opaque application data. Jasper reports, for example,
    # legitimately contain DTDs and must not be parsed by the form compiler.
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


def _pull_managed_resources(
    root: Path,
    *,
    session,
    base: str,
    inventory: Inventory,
    observed: dict[str, dict[str, Any]],
    include_personal: bool,
) -> None:
    for scope, directory in iter_dirs_as_scopes(session, base):
        selector = _managed_scope(scope)
        dir_id = directory.get("id") or resource_pk(directory.get("resource_uri"))
        if dir_id is None:
            _exit(f"{scope.label()}: SpAppResourceDir has no id")
        rows = iter_list_endpoint(
            session,
            base,
            f"/api/specify/spappresource/?spappresourcedir={int(dir_id)}&limit=1000",
        )
        names: set[str] = set()
        for row in rows:
            resource_id = row.get("id") or resource_pk(row.get("resource_uri"))
            name = str(row.get("name") or "").strip()
            mimetype = str(row.get("mimetype") or "text/plain").strip()
            if resource_id is None or not name:
                _exit(f"dir={dir_id}: malformed SpAppResource")
            if name.casefold() in names:
                _exit(f"{scope.label()}: duplicate app resource name {name!r}")
            names.add(name.casefold())
            if scope.level == "personal" and not include_personal:
                import hashlib

                material = json.dumps(
                    {"kind": "resource", "scope": selector.as_dict(), "name": name},
                    sort_keys=True,
                )
                inventory.add(
                    InventoryEntry(
                        uid=f"resource-excluded:{hashlib.sha256(material.encode()).hexdigest()[:16]}",
                        kind="AppResource",
                        source="managed",
                        status="excluded",
                        path=None,
                        scope=selector,
                        reason="personal resources disabled by workspace policy",
                    )
                )
                continue
            data_row = _one_data_row(
                session,
                base,
                relation="spappresource",
                object_id=int(resource_id),
                label=f"{scope.label()}/{name}",
            )
            payload = str(data_row["data"])
            _, entry = write_resource(
                root,
                name=name,
                payload=payload,
                mimetype=mimetype,
                source="managed",
                scope=selector,
                metadata={
                    "remoteDescription": row.get("description"),
                    "remoteMetadata": row.get("metadata"),
                    "level": int(row.get("level") or 0),
                    "allPermissionLevel": row.get("allpermissionlevel"),
                    "groupPermissionLevel": row.get("grouppermissionlevel"),
                    "ownerPermissionLevel": row.get("ownerpermissionlevel"),
                },
            )
            inventory.add(entry)
            observed[entry.uid] = {
                "canonicalSha256": _resource_hash(
                    payload,
                    mimetype,
                    where=f"db:{scope.label()}/{name}",
                ),
                "objectId": int(resource_id),
                "dataId": int(data_row["id"]),
                "version": int(data_row.get("version") or 0),
                "objectVersion": int(row.get("version") or 0),
                "directoryId": int(dir_id),
            }


def _pull_schemas(
    root: Path,
    *,
    conn: Connection,
    disciplines: list[dict[str, Any]],
    collections: list[dict[str, Any]],
    inventory: Inventory,
    observed: dict[str, dict[str, Any]],
    language: str,
) -> None:
    collection_ids_by_disc: dict[int, list[int]] = defaultdict(list)
    for collection in collections:
        disc_id = resource_pk(collection.get("discipline"))
        if disc_id is not None and collection.get("id") is not None:
            collection_ids_by_disc[disc_id].append(int(collection["id"]))
    for discipline in disciplines:
        disc_id = int(discipline["id"])
        if not collection_ids_by_disc.get(disc_id):
            import hashlib

            slug = unique_discipline_slug(discipline, disciplines)
            scope = ScopeSelector(level="discipline", discipline=slug)
            inventory.add(
                InventoryEntry(
                    uid=f"schema-excluded:{hashlib.sha256(scope.identity().encode()).hexdigest()[:16]}",
                    kind="SchemaLocalization",
                    source="managed",
                    status="excluded",
                    path=None,
                    scope=scope,
                    reason="discipline has no collection session for schema localization",
                )
            )
            continue
        session, collection_name, collection_id, resolved = conn.session(
            discipline=str(disc_id),
            log_prefix="v3-schema-pull",
        )
        schema = session.get(
            f"{conn.url}/context/schema_localization.json?lang={quote(language)}",
            timeout=120,
        )
        if not schema.ok:
            _exit(
                f"schema discipline id={disc_id}: GET failed "
                f"({schema.status_code}): {schema.text[:400]}"
            )
        data = schema.json()
        if not isinstance(data, dict):
            _exit(f"schema discipline id={disc_id}: expected object")
        slug = unique_discipline_slug(resolved or discipline, disciplines)
        manifest, entry = split_schema(
            root,
            discipline=slug,
            language=language,
            schema=data,
            metadata={
                "disciplineName": discipline.get("name"),
                "disciplineType": discipline.get("type"),
                "collectionName": collection_name,
            },
        )
        inventory.add(entry)
        import hashlib

        raw = json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        observed[entry.uid] = {
            "canonicalSha256": f"sha256:{hashlib.sha256(raw.encode()).hexdigest()}",
            "disciplineId": disc_id,
            "collectionId": collection_id,
        }
        if not manifest.is_file():
            _exit(f"failed to write schema manifest for {slug}")
        session.close()


def _pull_picklists(
    root: Path,
    *,
    session,
    base: str,
    disciplines: list[dict[str, Any]],
    collections: list[dict[str, Any]],
    inventory: Inventory,
    observed: dict[str, dict[str, Any]],
) -> None:
    disciplines_by_id = {int(row["id"]): row for row in disciplines}
    collection_scope: dict[int, ScopeSelector] = {}
    for collection in collections:
        collection_id = int(collection["id"])
        disc_id = resource_pk(collection.get("discipline"))
        if disc_id is None or disc_id not in disciplines_by_id:
            _exit(f"collection id={collection_id}: missing discipline")
        discipline = disciplines_by_id[disc_id]
        collection_scope[collection_id] = ScopeSelector(
            level="collection",
            discipline=unique_discipline_slug(discipline, disciplines),
            collection=collection_display_name(collection),
        )

    items = iter_list_endpoint(session, base, "/api/specify/picklistitem/?limit=5000")
    items_by_picklist: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        picklist_id = resource_pk(item.get("picklist"))
        if picklist_id is not None:
            items_by_picklist[picklist_id].append(item)

    for row in iter_list_endpoint(session, base, "/api/specify/picklist/?limit=1000"):
        picklist_id = int(row["id"])
        collection_id = resource_pk(row.get("collection"))
        if collection_id is None or collection_id not in collection_scope:
            _exit(f"picklist id={picklist_id}: unsupported collection scope")
        definition = {
            key: value
            for key, value in row.items()
            if key
            not in {
                "id",
                "resource_uri",
                "picklistitems",
                "createdbyagent",
                "modifiedbyagent",
                "timestampcreated",
                "timestampmodified",
                "version",
                "collection",
            }
        }
        ordered = sorted(
            items_by_picklist.get(picklist_id, []),
            key=lambda item: (
                int(item.get("ordinal") or 0),
                int(item.get("id") or 0),
            ),
        )
        item_payload = [
            {
                key: value
                for key, value in item.items()
                if key
                not in {
                    "id",
                    "resource_uri",
                    "picklist",
                    "createdbyagent",
                    "modifiedbyagent",
                    "timestampcreated",
                    "timestampmodified",
                    "version",
                }
            }
            for item in ordered
        ]
        _, entry = write_picklist(
            root,
            scope=collection_scope[collection_id],
            name=str(row.get("name") or f"picklist-{picklist_id}"),
            definition=definition,
            items=item_payload,
        )
        inventory.add(entry)
        import hashlib

        raw = json.dumps(
            {"definition": definition, "items": item_payload},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        observed[entry.uid] = {
            "canonicalSha256": f"sha256:{hashlib.sha256(raw.encode()).hexdigest()}",
            "objectId": picklist_id,
            "version": int(row.get("version") or 0),
            "collectionId": collection_id,
            "itemVersions": {
                str(int(item["id"])): int(item.get("version") or 0)
                for item in ordered
            },
        }


def pull_remote_repository(
    root: Path,
    *,
    conn: Connection,
    context_name: str,
    include_personal: bool = False,
    language: str = "en",
) -> None:
    """Build a complete v3 repository at an empty staging path."""
    init_repository(root, instance=conn.url)
    inventory = Inventory()
    observed: dict[str, dict[str, Any]] = {}
    session, _, _, _ = conn.session(log_prefix="v3-pull")
    disciplines = list_disciplines(session, conn.url)
    collections = list_collections(session, conn.url)
    disciplines_by_id = {int(row["id"]): row for row in disciplines}
    collection_scopes: dict[str, int] = {}
    for collection in collections:
        collection_id = int(collection["id"])
        discipline_id = resource_pk(collection.get("discipline"))
        if discipline_id is None or discipline_id not in disciplines_by_id:
            _exit(f"collection id={collection_id}: missing discipline")
        discipline = disciplines_by_id[discipline_id]
        selector = ScopeSelector(
            level="collection",
            discipline=unique_discipline_slug(discipline, disciplines),
            collection=collection_display_name(collection),
        )
        collection_scopes[selector.identity()] = collection_id
    directories: dict[str, int] = {}
    for scope, directory in iter_dirs_as_scopes(session, conn.url):
        directory_id = directory.get("id") or resource_pk(
            directory.get("resource_uri")
        )
        if directory_id is None:
            _exit(f"{scope.label()}: SpAppResourceDir has no id")
        selector = _managed_scope(scope)
        identity = selector.identity()
        if identity in directories and directories[identity] != int(directory_id):
            _exit(
                f"{scope.label()}: multiple SpAppResourceDir rows at one scope"
            )
        directories[identity] = int(directory_id)

    _pull_vendor_viewsets(
        root,
        session=session,
        base=conn.url,
        inventory=inventory,
        observed=observed,
    )
    _pull_managed_viewsets(
        root,
        session=session,
        base=conn.url,
        inventory=inventory,
        observed=observed,
        include_personal=include_personal,
    )
    _pull_vendor_resources(
        root,
        session=session,
        base=conn.url,
        inventory=inventory,
        observed=observed,
    )
    _pull_managed_resources(
        root,
        session=session,
        base=conn.url,
        inventory=inventory,
        observed=observed,
        include_personal=include_personal,
    )
    _pull_picklists(
        root,
        session=session,
        base=conn.url,
        disciplines=disciplines,
        collections=collections,
        inventory=inventory,
        observed=observed,
    )
    session.close()
    _pull_schemas(
        root,
        conn=conn,
        disciplines=disciplines,
        collections=collections,
        inventory=inventory,
        observed=observed,
        language=language,
    )
    inventory.write(root)
    ContextState(
        context=context_name,
        instance=conn.url,
        resolver_contract="specify7/db-all-levels-then-disk-all-levels+app-per-level",
        resources=observed,
        directories=directories,
        collections=collection_scopes,
    ).write(root)
