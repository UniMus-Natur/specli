"""Whole-document app-resource sync (UIFormatters / DataObjFormatters / WebLinks)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from specli.client import (
    disable_insecure_request_warnings,
    iter_list_endpoint,
    post_json,
    put_json,
    resource_pk,
    resolve_specify_user_id,
    safe_slug,
    strip_meta_for_put,
)
from specli.connection import resolve_connection
from specli.dir_ops import ensure_dir, find_dirs_for_scope
from specli.disk_config import pull_disk_app_resources
from specli.scopes import (
    APP_RESOURCE_NAMES,
    Scope,
    app_resource_filename,
    discover_scopes,
    filter_scopes,
    scopes_from_workspace,
)
from specli.workspace import enter_workspace, ensure_layout, require_workspace


def _write_xml(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = body if body.startswith("<?xml") else '<?xml version="1.0" encoding="UTF-8"?>\n' + body
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def _find_resource_in_dir(
    session, base: str, dir_id: int, name: str
) -> dict | None:
    rows = iter_list_endpoint(
        session,
        base,
        f"/api/specify/spappresource/?spappresourcedir={int(dir_id)}&name={name}&limit=20",
    )
    for row in rows:
        if str(row.get("name") or "").strip() == name:
            return row
    return None


def _load_resource_xml(session, base: str, resource_id: int) -> str | None:
    rows = iter_list_endpoint(
        session,
        base,
        f"/api/specify/spappresourcedata/?spappresource={int(resource_id)}&limit=5",
    )
    if not rows:
        return None
    return str(rows[0].get("data") or "")


def pull_app(
    *,
    workspace: Path,
    scopes: list[Scope],
    names: tuple[str, ...] = APP_RESOURCE_NAMES,
    login_collection: str | None = None,
) -> int:
    disable_insecure_request_warnings()
    conn = resolve_connection(login_collection_override=login_collection)
    session, _, _, _ = conn.session(log_prefix="app-pull")
    base = conn.url
    written = 0

    disk_n = pull_disk_app_resources(
        workspace=workspace,
        session=session,
        base=base,
        scopes=scopes,
        names=names,
    )

    for scope in scopes:
        if scope.is_filesystem_only():
            continue
        dirs = find_dirs_for_scope(session, base, scope)
        if not dirs:
            continue
        for drow in dirs:
            dir_id = drow.get("id") or resource_pk(drow.get("resource_uri"))
            if dir_id is None:
                continue
            for name in names:
                row = _find_resource_in_dir(session, base, int(dir_id), name)
                if row is None:
                    continue
                rid = row.get("id") or resource_pk(row.get("resource_uri"))
                if rid is None:
                    continue
                xml = _load_resource_xml(session, base, int(rid))
                if not xml or not xml.strip():
                    continue
                out = workspace / scope.app_dir() / app_resource_filename(name)
                if out.exists() and out.read_text(encoding="utf-8") == (
                    xml if xml.endswith("\n") else xml + "\n"
                ):
                    continue
                _write_xml(out, xml)
                written += 1
                print(
                    f"[app-pull] {scope.label()} → {out.relative_to(workspace)}",
                    file=sys.stderr,
                )
            break

    session.close()
    print(f"[app-pull] disk={disk_n}, db={written} resource(s)", file=sys.stderr)
    return 0


def _put_or_create_resource(
    session,
    base: str,
    *,
    dir_id: int,
    name: str,
    xml: str,
    specify_user_id: int,
    apply: bool,
) -> str:
    row = _find_resource_in_dir(session, base, dir_id, name)
    if row is None:
        if not apply:
            return f"would create {name} in dir={dir_id}"
        resource = post_json(
            session,
            base,
            "/api/specify/spappresource/",
            {
                "name": name,
                "mimetype": "text/xml",
                "metadata": name,
                "description": name,
                "level": 0,
                "spappresourcedir": f"/api/specify/spappresourcedir/{dir_id}/",
                "specifyuser": f"/api/specify/specifyuser/{int(specify_user_id)}/",
            },
        )
        rid = resource.get("id") or resource_pk(resource.get("resource_uri"))
        if rid is None:
            sys.exit(f"Failed to create spappresource {name}")
        post_json(
            session,
            base,
            "/api/specify/spappresourcedata/",
            {
                "data": xml,
                "spappresource": f"/api/specify/spappresource/{int(rid)}/",
            },
        )
        return f"created {name} id={rid}"

    rid = int(row["id"])
    data_rows = iter_list_endpoint(
        session,
        base,
        f"/api/specify/spappresourcedata/?spappresource={rid}&limit=5",
    )
    if not data_rows:
        if not apply:
            return f"would create data for {name}"
        post_json(
            session,
            base,
            "/api/specify/spappresourcedata/",
            {
                "data": xml,
                "spappresource": f"/api/specify/spappresource/{rid}/",
            },
        )
        return f"created data for {name}"

    remote = str(data_rows[0].get("data") or "")
    if remote == xml:
        return f"unchanged {name}"
    if not apply:
        return f"would update {name} ({len(remote)} → {len(xml)} bytes)"
    body = strip_meta_for_put(data_rows[0])
    body["data"] = xml
    data_id = data_rows[0].get("id") or resource_pk(data_rows[0].get("resource_uri"))
    put_json(session, base, f"/api/specify/spappresourcedata/{int(data_id)}/", body)
    return f"updated {name}"


def push_app(
    *,
    workspace: Path,
    scopes: list[Scope],
    names: tuple[str, ...] = APP_RESOURCE_NAMES,
    login_collection: str | None = None,
    apply: bool = False,
) -> int:
    disable_insecure_request_warnings()
    conn = resolve_connection(login_collection_override=login_collection)
    session, _, _, _ = conn.session(log_prefix="app-push")
    base = conn.url
    user_id = resolve_specify_user_id(session, base, conn.user)

    for scope in scopes:
        if scope.is_filesystem_only():
            print(
                f"[app-push] {scope.label()}: skipped — Backstop is not pushed. "
                "Promote into base/ or by/* first.",
                file=sys.stderr,
            )
            continue
        app_dir = workspace / scope.app_dir()
        if not app_dir.is_dir():
            continue
        files = []
        for name in names:
            path = app_dir / app_resource_filename(name)
            if path.is_file():
                files.append((name, path))
        if not files:
            continue
        dir_id, created = ensure_dir(session, base, scope)
        if created:
            print(
                f"[app-push] {scope.label()}: created SpAppResourceDir id={dir_id}",
                file=sys.stderr,
            )
        for name, path in files:
            xml = path.read_text(encoding="utf-8")
            msg = _put_or_create_resource(
                session,
                base,
                dir_id=dir_id,
                name=name,
                xml=xml,
                specify_user_id=user_id,
                apply=apply,
            )
            print(f"[app-push] {scope.label()}: {msg}", file=sys.stderr)

    session.close()
    if not apply:
        print("[app-push] dry-run — use push (not status) to write", file=sys.stderr)
    return 0


def _pull_scopes(args: argparse.Namespace) -> tuple[Path, list[Scope]]:
    workspace = enter_workspace()
    ensure_layout(workspace)
    conn = resolve_connection(login_collection_override=args.login_collection)
    session, _, _, _ = conn.session(log_prefix="app-scopes")
    scopes = discover_scopes(
        session,
        conn.url,
        include_personal=args.include_personal,
        discipline_filter=args.discipline,
    )
    session.close()
    scopes = filter_scopes(
        scopes,
        scope_refs=args.scope,
        discipline_filter=args.discipline,
        include_personal=args.include_personal,
    )
    return workspace, scopes


def _names_from_args(args: argparse.Namespace) -> tuple[str, ...]:
    only = getattr(args, "only", None)
    if only:
        return tuple(n.strip() for n in only.split(",") if n.strip())
    return APP_RESOURCE_NAMES


def cmd_pull(args: argparse.Namespace) -> int:
    workspace, scopes = _pull_scopes(args)
    return pull_app(
        workspace=workspace,
        scopes=scopes,
        names=_names_from_args(args),
        login_collection=args.login_collection,
    )


def cmd_push(args: argparse.Namespace) -> int:
    workspace = require_workspace()
    enter_workspace()
    scopes = scopes_from_workspace(
        workspace,
        include_personal=args.include_personal,
        scope_refs=args.scope,
    )
    if args.discipline:
        needle = safe_slug(args.discipline)
        scopes = [
            s
            for s in scopes
            if s.level == "common"
            or s.discipline_slug == needle
            or (s.level == "usertype" and s.discipline_slug == needle)
        ]
    return push_app(
        workspace=workspace,
        scopes=scopes,
        names=_names_from_args(args),
        login_collection=args.login_collection,
        apply=not args.dry_run,
    )


def add_scope_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--scope", action="append", default=None)
    p.add_argument("--discipline", default=None)
    p.add_argument("--include-personal", action="store_true")
    p.add_argument("--login-collection", default=None)
    p.add_argument(
        "--only",
        default=None,
        help="Comma-separated resource names (default: all three)",
    )


def build_parser(sub: argparse._SubParsersAction) -> None:
    app = sub.add_parser(
        "app",
        help="Whole app-resource XML sync (UIFormatters, DataObjFormatters, WebLinks)",
    )
    cmd = app.add_subparsers(dest="cmd", required=True)

    pull = cmd.add_parser("pull", help="Pull app resources → workspace")
    add_scope_flags(pull)
    pull.set_defaults(app_handler=cmd_pull)

    push = cmd.add_parser("push", help="Push app resources → Specify")
    add_scope_flags(push)
    push.add_argument("--dry-run", action="store_true")
    push.set_defaults(app_handler=cmd_push)

    status = cmd.add_parser("status", help="Dry-run push")
    add_scope_flags(status)

    def _status(a: argparse.Namespace) -> int:
        a.dry_run = True
        return cmd_push(a)

    status.set_defaults(app_handler=_status)
