"""Schema config sync — lives only under by/discipline/<slug>/schema/."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from specli.client import (
    disable_insecure_request_warnings,
    get_json,
    iter_list_endpoint,
    post_json,
    put_json,
    resource_pk,
    safe_slug,
    strip_meta_for_put,
)
from specli.connection import resolve_connection
from specli.domain import discipline_slug, list_disciplines, match_discipline
from specli.workspace import enter_workspace, ensure_layout, require_workspace

_ITEM_SYNC_KEYS = ("ishidden", "isrequired", "picklistname", "format", "weblinkname")
_CONTAINER_SYNC_KEYS = ("ishidden", "picklistname", "format", "aggregator", "defaultui")


def _schema_dir_for(workspace: Path, disc_slug: str) -> Path:
    return workspace / "by" / "discipline" / disc_slug / "schema"


def _count_fields(schema: dict[str, Any]) -> tuple[int, int, int]:
    tables = fields = visible = 0
    for _table_name, table_cfg in schema.items():
        if not isinstance(table_cfg, dict):
            continue
        tables += 1
        items = table_cfg.get("items") or {}
        if not isinstance(items, dict):
            continue
        for _fname, field_cfg in items.items():
            if not isinstance(field_cfg, dict):
                continue
            fields += 1
            if not field_cfg.get("ishidden"):
                visible += 1
    return tables, fields, visible


def _export_one(
    *,
    conn,
    workspace: Path,
    lang: str,
    only_tables: set[str] | None,
    discipline_needle: str,
) -> dict[str, Any]:
    session, collection_name, collection_id, disc = conn.session(
        discipline=discipline_needle,
        log_prefix="schema-pull",
    )
    if disc is None:
        sys.exit(f"Could not resolve discipline for {discipline_needle!r}")
    slug = discipline_slug(disc)
    schema = get_json(
        session,
        conn.url,
        f"/context/schema_localization.json?lang={quote(lang)}",
    )
    if not isinstance(schema, dict):
        sys.exit("Unexpected schema_localization.json payload")

    if only_tables is not None:
        schema = {k: v for k, v in schema.items() if k.lower() in only_tables}

    out_dir = _schema_dir_for(workspace, slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    tables_n, fields_n, visible_n = _count_fields(schema)
    meta = {
        "discipline_id": int(disc["id"]),
        "discipline_name": str(disc.get("name") or slug),
        "discipline_type": str(disc.get("type") or "unknown"),
        "discipline_slug": slug,
        "collection_id": collection_id,
        "collection_name": collection_name,
        "language": lang,
        "source": conn.url,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "table_count": tables_n,
        "field_count": fields_n,
        "visible_field_count": visible_n,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    schema_path = out_dir / f"schema.{lang}.json"
    schema_path.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"[schema-pull] {slug} → {out_dir.relative_to(workspace)} "
        f"(tables={tables_n})",
        file=sys.stderr,
    )
    session.close()
    return meta


def pull_schema(
    *,
    workspace: Path,
    discipline_filter: str | None = None,
    lang: str = "en",
    only_tables: set[str] | None = None,
    clean: bool = False,
    login_collection: str | None = None,
) -> int:
    disable_insecure_request_warnings()
    conn = resolve_connection(login_collection_override=login_collection)
    boot, _, _, _ = conn.session(log_prefix="schema-pull")
    all_discs = list_disciplines(boot, conn.url)
    boot.close()
    if not all_discs:
        sys.exit("No disciplines found")

    if discipline_filter:
        targets = [d for d in all_discs if match_discipline(d, discipline_filter)]
        if not targets:
            sys.exit(f"Discipline {discipline_filter!r} not found")
    else:
        targets = all_discs

    if clean:
        root = workspace / "by" / "discipline"
        if root.exists():
            for d in root.iterdir():
                schema_dir = d / "schema"
                if schema_dir.exists():
                    shutil.rmtree(schema_dir)

    for disc in targets:
        _export_one(
            conn=conn,
            workspace=workspace,
            lang=lang,
            only_tables=only_tables,
            discipline_needle=discipline_slug(disc),
        )
    return 0


def _discover_bundles(workspace: Path) -> list[Path]:
    root = workspace / "by" / "discipline"
    if not root.is_dir():
        return []
    return sorted({p.parent for p in root.glob("*/schema/meta.json")})


def _load_bundle(bundle_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    meta = json.loads((bundle_dir / "meta.json").read_text(encoding="utf-8"))
    lang = str(meta.get("language") or "en").strip().lower()
    schema_path = bundle_dir / f"schema.{lang}.json"
    if not schema_path.exists():
        found = sorted(bundle_dir.glob("schema.*.json"))
        if not found:
            sys.exit(f"No schema.<lang>.json in {bundle_dir}")
        schema_path = found[0]
        lang = schema_path.stem.split(".", 1)[-1]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    meta = {**meta, "language": lang}
    return meta, schema


def _norm_bool(value: Any) -> bool:
    return bool(value)


def _sync_bundle(
    *,
    conn,
    meta: dict[str, Any],
    schema: dict[str, Any],
    apply: bool,
    create_missing: bool,
    only_tables: set[str] | None,
    verbose_missing: bool,
) -> None:
    """Port of prior import_schema sync logic against one discipline bundle."""
    slug = str(meta.get("discipline_slug") or "")
    session, _col, _cid, disc = conn.session(discipline=slug, log_prefix="schema-push")
    if disc is None:
        sys.exit(f"Could not resolve discipline {slug!r}")
    base = conn.url
    discipline_id = int(disc["id"])

    # Fetch existing containers for this discipline
    containers = iter_list_endpoint(
        session,
        base,
        f"/api/specify/splocalecontainer/?discipline={discipline_id}&schematype=0&limit=500",
    )
    by_name = {
        str(c.get("name") or "").strip().lower(): c
        for c in containers
        if c.get("name")
    }

    changed = 0
    missing = 0
    for table_name, table_cfg in schema.items():
        if not isinstance(table_cfg, dict):
            continue
        if only_tables and table_name.lower() not in only_tables:
            continue
        key = table_name.lower()
        remote = by_name.get(key)
        if remote is None:
            missing += 1
            if verbose_missing:
                print(f"[schema-push] missing container {table_name}", file=sys.stderr)
            if create_missing and apply:
                remote = post_json(
                    session,
                    base,
                    "/api/specify/splocalecontainer/",
                    {
                        "name": table_name,
                        "discipline": f"/api/specify/discipline/{discipline_id}/",
                        "schematype": 0,
                        "ishidden": bool(table_cfg.get("ishidden")),
                    },
                )
                by_name[key] = remote
            else:
                continue

        body = strip_meta_for_put(remote)
        dirty = False
        for k in _CONTAINER_SYNC_KEYS:
            if k not in table_cfg:
                continue
            new_v = table_cfg[k]
            old_v = body.get(k)
            if k == "ishidden":
                new_v, old_v = _norm_bool(new_v), _norm_bool(old_v)
            if new_v != old_v:
                body[k] = table_cfg[k]
                dirty = True
        if dirty:
            changed += 1
            if apply:
                cid = remote.get("id") or resource_pk(remote.get("resource_uri"))
                put_json(session, base, f"/api/specify/splocalecontainer/{int(cid)}/", body)

        # Items
        items = table_cfg.get("items") or {}
        if not isinstance(items, dict):
            continue
        container_id = remote.get("id") or resource_pk(remote.get("resource_uri"))
        remote_items = iter_list_endpoint(
            session,
            base,
            f"/api/specify/splocalecontaineritem/?container={int(container_id)}&limit=500",
        )
        items_by_name = {
            str(i.get("name") or "").strip().lower(): i for i in remote_items if i.get("name")
        }
        for fname, fcfg in items.items():
            if not isinstance(fcfg, dict):
                continue
            item = items_by_name.get(fname.lower())
            if item is None:
                if verbose_missing:
                    print(
                        f"[schema-push] missing item {table_name}.{fname}",
                        file=sys.stderr,
                    )
                if create_missing and apply:
                    item = post_json(
                        session,
                        base,
                        "/api/specify/splocalecontaineritem/",
                        {
                            "name": fname,
                            "container": f"/api/specify/splocalecontainer/{int(container_id)}/",
                            "ishidden": bool(fcfg.get("ishidden")),
                            "isrequired": bool(fcfg.get("isrequired")),
                        },
                    )
                else:
                    continue
            ibody = strip_meta_for_put(item)
            idirty = False
            for k in _ITEM_SYNC_KEYS:
                if k not in fcfg:
                    continue
                new_v = fcfg[k]
                old_v = ibody.get(k)
                if k in ("ishidden", "isrequired"):
                    new_v, old_v = _norm_bool(new_v), _norm_bool(old_v)
                if new_v != old_v:
                    ibody[k] = fcfg[k]
                    idirty = True
            if idirty:
                changed += 1
                if apply:
                    iid = item.get("id") or resource_pk(item.get("resource_uri"))
                    put_json(
                        session,
                        base,
                        f"/api/specify/splocalecontaineritem/{int(iid)}/",
                        ibody,
                    )

    print(
        f"[schema-push] {slug}: containers_missing={missing}, field_updates={changed}"
        + ("" if apply else " (dry-run)"),
        file=sys.stderr,
    )
    session.close()


def push_schema(
    *,
    workspace: Path,
    discipline_filter: str | None = None,
    apply: bool = False,
    create_missing: bool = False,
    only_tables: set[str] | None = None,
    verbose_missing: bool = False,
    login_collection: str | None = None,
) -> int:
    disable_insecure_request_warnings()
    conn = resolve_connection(login_collection_override=login_collection)
    bundles = _discover_bundles(workspace)
    if not bundles:
        print("[schema-push] no schema bundles under by/discipline/*/schema/", file=sys.stderr)
        return 0
    for bundle in bundles:
        meta, schema = _load_bundle(bundle)
        slug = str(meta.get("discipline_slug") or bundle.parent.name)
        if discipline_filter and not (
            slug == safe_slug(discipline_filter)
            or match_discipline({"name": slug, "type": slug, "id": meta.get("discipline_id")}, discipline_filter)
            or slug == discipline_filter
        ):
            # Also allow meta name match
            name = str(meta.get("discipline_name") or "")
            dtype = str(meta.get("discipline_type") or "")
            if discipline_filter.lower() not in {slug, name.lower(), dtype.lower(), safe_slug(dtype)}:
                continue
        _sync_bundle(
            conn=conn,
            meta=meta,
            schema=schema,
            apply=apply,
            create_missing=create_missing,
            only_tables=only_tables,
            verbose_missing=verbose_missing,
        )
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    workspace = enter_workspace()
    ensure_layout(workspace)
    only: set[str] | None = None
    if args.only_tables:
        only = {t.strip().lower() for t in args.only_tables.split(",") if t.strip()}
    return pull_schema(
        workspace=workspace,
        discipline_filter=args.discipline,
        lang=args.lang,
        only_tables=only,
        clean=args.clean,
        login_collection=args.login_collection,
    )


def cmd_push(args: argparse.Namespace) -> int:
    workspace = require_workspace()
    enter_workspace()
    only: set[str] | None = None
    if args.only_tables:
        only = {t.strip().lower() for t in args.only_tables.split(",") if t.strip()}
    return push_schema(
        workspace=workspace,
        discipline_filter=args.discipline,
        apply=not args.dry_run,
        create_missing=args.create_missing,
        only_tables=only,
        verbose_missing=args.verbose_missing,
        login_collection=args.login_collection,
    )


def build_parser(sub: argparse._SubParsersAction) -> None:
    schema = sub.add_parser(
        "schema",
        help="Schema config sync (by/discipline/<slug>/schema/)",
    )
    cmd = schema.add_subparsers(dest="cmd", required=True)

    pull = cmd.add_parser("pull", help="Pull schema JSON from Specify")
    pull.add_argument("--discipline", default=None)
    pull.add_argument("--lang", default="en")
    pull.add_argument("--clean", action="store_true")
    pull.add_argument("--only-tables", default=None)
    pull.add_argument("--login-collection", default=None)
    pull.set_defaults(schema_handler=cmd_pull)

    def _add_push(p: argparse.ArgumentParser) -> None:
        p.add_argument("--discipline", default=None)
        p.add_argument("--only-tables", default=None)
        p.add_argument("--create-missing", action="store_true")
        p.add_argument("--verbose-missing", action="store_true")
        p.add_argument("--login-collection", default=None)

    push = cmd.add_parser("push", help="Push schema JSON to Specify")
    _add_push(push)
    push.add_argument("--dry-run", action="store_true")
    push.set_defaults(schema_handler=cmd_push)

    status = cmd.add_parser("status", help="Dry-run push")
    _add_push(status)

    def _status(a: argparse.Namespace) -> int:
        a.dry_run = True
        return cmd_push(a)

    status.set_defaults(schema_handler=_status)
