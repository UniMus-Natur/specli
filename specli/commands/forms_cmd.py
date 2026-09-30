"""Whole-document form (viewset) sync — scope-first."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

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
from specli.disk_config import pull_disk_viewsets
from specli.scopes import (
    Scope,
    discover_scopes,
    filter_scopes,
    scopes_from_workspace,
    viewset_filename,
)
from specli.workspace import enter_workspace, ensure_layout, require_workspace


def _write_xml(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = body if body.startswith("<?xml") else '<?xml version="1.0" encoding="UTF-8"?>\n' + body
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def _viewset_name_from_xml(xml: str, fallback: str) -> str:
    try:
        root = ET.fromstring(xml)
        name = (root.attrib.get("name") or "").strip()
        if name:
            return name
    except ET.ParseError:
        pass
    return fallback


def _load_viewset_data(session, base: str, viewset_id: int) -> str | None:
    rows = iter_list_endpoint(
        session,
        base,
        f"/api/specify/spappresourcedata/?spviewsetobj={int(viewset_id)}&limit=5",
    )
    if not rows:
        return None
    return str(rows[0].get("data") or "")


def _list_viewsets_in_dir(session, base: str, dir_id: int) -> list[dict[str, Any]]:
    return iter_list_endpoint(
        session,
        base,
        f"/api/specify/spviewsetobj/?spappresourcedir={int(dir_id)}&limit=200",
    )


def pull_forms(
    *,
    workspace: Path,
    scopes: list[Scope],
    login_collection: str | None = None,
    clean: bool = False,
) -> int:
    disable_insecure_request_warnings()
    conn = resolve_connection(login_collection_override=login_collection)
    session, _, _, _ = conn.session(log_prefix="form-pull")
    base = conn.url

    if clean:
        for scope in scopes:
            forms_dir = workspace / scope.forms_dir()
            if forms_dir.exists():
                for p in forms_dir.glob("*.views.xml"):
                    p.unlink()

    # Disk layers first (Backstop / Common / discipline FS) via /static/config/
    disk_n = pull_disk_viewsets(
        workspace=workspace,
        session=session,
        base=base,
        scopes=scopes,
    )

    written = 0
    for scope in scopes:
        if scope.is_filesystem_only():
            continue
        dirs = find_dirs_for_scope(session, base, scope)
        if not dirs:
            print(f"[form-pull] {scope.label()}: no SpAppResourceDir — skip", file=sys.stderr)
            continue
        seen_names: set[str] = set()
        for drow in dirs:
            dir_id = drow.get("id") or resource_pk(drow.get("resource_uri"))
            if dir_id is None:
                continue
            for vs in _list_viewsets_in_dir(session, base, int(dir_id)):
                vs_id = vs.get("id")
                if vs_id is None:
                    continue
                name = str(vs.get("name") or f"viewset-{vs_id}").strip()
                key = safe_slug(name)
                if key in seen_names:
                    continue
                xml = _load_viewset_data(session, base, int(vs_id))
                if not xml or not xml.strip():
                    print(
                        f"[form-pull] {scope.label()}: empty viewset {name!r} — skip",
                        file=sys.stderr,
                    )
                    continue
                out = workspace / scope.forms_dir() / viewset_filename(name)
                _write_xml(out, xml)
                seen_names.add(key)
                written += 1
                print(
                    f"[form-pull] {scope.label()} → {out.relative_to(workspace)} "
                    f"({len(xml)} bytes)",
                    file=sys.stderr,
                )

    session.close()
    print(
        f"[form-pull] disk={disk_n}, db={written} viewset(s)",
        file=sys.stderr,
    )
    return 0


def _put_or_create_viewset(
    session,
    base: str,
    *,
    dir_id: int,
    name: str,
    xml: str,
    specify_user_id: int,
    apply: bool,
) -> str:
    existing = _list_viewsets_in_dir(session, base, dir_id)
    match = None
    for vs in existing:
        if str(vs.get("name") or "").strip().lower() == name.lower():
            match = vs
            break

    if match is None:
        if not apply:
            return f"would create viewset {name!r} in dir={dir_id}"
        vs_obj = post_json(
            session,
            base,
            "/api/specify/spviewsetobj/",
            {
                "name": name,
                "level": 0,
                "filename": f"{safe_slug(name)}.views.xml",
                "metadata": None,
                "description": None,
                "spappresourcedir": f"/api/specify/spappresourcedir/{dir_id}/",
            },
        )
        vs_id = vs_obj.get("id") or resource_pk(vs_obj.get("resource_uri"))
        if vs_id is None:
            sys.exit(f"Failed to create spviewsetobj {name!r}")
        post_json(
            session,
            base,
            "/api/specify/spappresourcedata/",
            {
                "data": xml,
                "spviewsetobj": f"/api/specify/spviewsetobj/{int(vs_id)}/",
            },
        )
        return f"created viewset {name!r} id={vs_id}"

    vs_id = int(match["id"])
    data_rows = iter_list_endpoint(
        session,
        base,
        f"/api/specify/spappresourcedata/?spviewsetobj={vs_id}&limit=5",
    )
    if not data_rows:
        if not apply:
            return f"would create spappresourcedata for viewset {name!r}"
        post_json(
            session,
            base,
            "/api/specify/spappresourcedata/",
            {
                "data": xml,
                "spviewsetobj": f"/api/specify/spviewsetobj/{vs_id}/",
            },
        )
        return f"created data for viewset {name!r}"

    remote = str(data_rows[0].get("data") or "")
    if remote == xml:
        return f"unchanged {name!r}"
    if not apply:
        return f"would update viewset {name!r} ({len(remote)} → {len(xml)} bytes)"
    body = strip_meta_for_put(data_rows[0])
    body["data"] = xml
    data_id = data_rows[0].get("id") or resource_pk(data_rows[0].get("resource_uri"))
    put_json(session, base, f"/api/specify/spappresourcedata/{int(data_id)}/", body)
    return f"updated viewset {name!r}"


def push_forms(
    *,
    workspace: Path,
    scopes: list[Scope],
    login_collection: str | None = None,
    apply: bool = False,
) -> int:
    disable_insecure_request_warnings()
    conn = resolve_connection(login_collection_override=login_collection)
    session, _, _, _ = conn.session(log_prefix="form-push")
    base = conn.url
    user_id = resolve_specify_user_id(session, base, conn.user)

    for scope in scopes:
        if scope.is_filesystem_only():
            print(
                f"[form-push] {scope.label()}: skipped — Backstop is not pushed to Specify. "
                "Promote edits into base/ or by/* and push those scopes.",
                file=sys.stderr,
            )
            continue
        forms_dir = workspace / scope.forms_dir()
        files = sorted(forms_dir.glob("*.views.xml")) if forms_dir.is_dir() else []
        if not files:
            continue
        dir_id, created = ensure_dir(session, base, scope)
        if created:
            print(
                f"[form-push] {scope.label()}: created SpAppResourceDir id={dir_id}",
                file=sys.stderr,
            )
        for path in files:
            xml = path.read_text(encoding="utf-8")
            fallback = path.name.replace(".views.xml", "")
            name = _viewset_name_from_xml(xml, fallback)
            msg = _put_or_create_viewset(
                session,
                base,
                dir_id=dir_id,
                name=name,
                xml=xml,
                specify_user_id=user_id,
                apply=apply,
            )
            print(f"[form-push] {scope.label()}: {msg}", file=sys.stderr)

    session.close()
    if not apply:
        print("[form-push] dry-run — pass --apply / use push (not status) to write", file=sys.stderr)
    return 0


def _resolve_scopes_for_pull(args: argparse.Namespace) -> tuple[Path, list[Scope]]:
    workspace = enter_workspace()
    ensure_layout(workspace)
    conn = resolve_connection(login_collection_override=args.login_collection)
    session, _, _, _ = conn.session(log_prefix="form-scopes")
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


def cmd_pull(args: argparse.Namespace) -> int:
    workspace, scopes = _resolve_scopes_for_pull(args)
    return pull_forms(
        workspace=workspace,
        scopes=scopes,
        login_collection=args.login_collection,
        clean=args.clean,
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
            if s.level == "common" or s.discipline_slug == needle
        ]
    return push_forms(
        workspace=workspace,
        scopes=scopes,
        login_collection=args.login_collection,
        apply=not args.dry_run,
    )


def add_scope_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--scope",
        action="append",
        default=None,
        help="Limit to scope path (repeatable): backstop, common, discipline/botany, …",
    )
    p.add_argument(
        "--discipline",
        default=None,
        help="Filter to scopes under this discipline slug/name",
    )
    p.add_argument(
        "--include-personal",
        action="store_true",
        help="Include by/user/* (Personal) scopes",
    )
    p.add_argument("--login-collection", default=None)


def build_parser(sub: argparse._SubParsersAction) -> None:
    form = sub.add_parser("form", help="Whole viewset XML sync (scope-first)")
    cmd = form.add_subparsers(dest="cmd", required=True)

    pull = cmd.add_parser("pull", help="Pull viewsets from Specify → workspace")
    add_scope_flags(pull)
    pull.add_argument("--clean", action="store_true", help="Delete local *.views.xml before write")
    pull.set_defaults(form_handler=cmd_pull)

    push = cmd.add_parser("push", help="Push local viewsets → Specify")
    add_scope_flags(push)
    push.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    push.set_defaults(form_handler=cmd_push)

    status = cmd.add_parser("status", help="Dry-run push")
    add_scope_flags(status)

    def _status(a: argparse.Namespace) -> int:
        a.dry_run = True
        return cmd_push(a)

    status.set_defaults(form_handler=_status)
