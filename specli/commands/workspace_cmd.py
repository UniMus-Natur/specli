"""``specli workspace`` — create or inspect a self-contained config repository."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from specli import secrets, workspace as ws


def _prompt(label: str, *, default: str | None = None, required: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            value = input(f"{label}{suffix}: ").strip()
        except EOFError:
            sys.exit("\nAborted.")
        if not value and default is not None:
            return default
        if value or not required:
            return value
        print("Value required.", file=sys.stderr)


def cmd_get(_args: argparse.Namespace) -> int:
    path = ws.resolve_workspace()
    if path is None:
        print("No specli/v3 repository found from cwd / SPECLI_WORKSPACE.", file=sys.stderr)
        return 1
    print(path)
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    root = ws.require_workspace()
    meta = ws.load_workspace_meta(root)
    print(f"workspace: {root}")
    print(f"apiVersion: {meta['apiVersion']}")
    print(f"instance: {meta.get('instance') or '(unset)'}")
    print(f"loginCollection: {meta.get('loginCollection') or '(unset)'}")
    local_user = secrets.get_user(root)
    has_password = bool(secrets.get_password(root))
    secret = root / ws.LOCAL_SECRETS_FILE
    if local_user and has_password:
        creds = f"user={local_user}"
    elif local_user or has_password:
        creds = "incomplete"
    else:
        creds = "missing"
    print(f"credentials: {creds} ({secret})")
    for rel in ws.LAYOUT_DIRS:
        mark = "ok" if (root / rel).is_dir() else "missing"
        print(f"  [{mark}] {rel}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)

    existing = (path / ws.SPECLI_YAML).is_file()
    meta = ws.load_workspace_meta(path) if existing else {}
    url = (args.url or "").strip() or _prompt(
        "Specify URL",
        default=str(meta.get("instance") or "").strip() or None,
    )
    login_collection = (args.login_collection or "").strip() or _prompt(
        "Login collection",
        default=str(meta.get("loginCollection") or "").strip() or None,
        required=False,
    ) or None

    created = ws.ensure_layout(
        path,
        instance=url.rstrip("/"),
        login_collection=login_collection,
    )
    if existing and not created:
        ws.write_workspace_connection(
            path,
            instance=url.rstrip("/"),
            login_collection=login_collection,
        )
        ws.ensure_secrets_gitignore(path)

    print(f"Repository → {path}")
    if created:
        print("Created:")
        for item in created:
            try:
                print(f"  {item.relative_to(path)}")
            except ValueError:
                print(f"  {item}")
    else:
        print("Updated workspace.yaml / .gitignore")
    print()
    print("Next: cd into the repo and run: specli auth login")
    return 0


def build_parser(sub: argparse._SubParsersAction) -> None:
    workspace = sub.add_parser(
        "workspace",
        help="Create or inspect a self-contained specli/v3 config repository",
    )
    cmd = workspace.add_subparsers(dest="workspace_cmd", required=True)

    get_p = cmd.add_parser("get", help="Print the discovered repository path")
    get_p.set_defaults(workspace_handler=cmd_get)

    status = cmd.add_parser("status", help="Show repository connection and layout")
    status.set_defaults(workspace_handler=cmd_status)

    init = cmd.add_parser(
        "init",
        help="Create or update a specli/v3 repository in a directory",
    )
    init.add_argument("path", nargs="?", default=".", help="Directory (default: .)")
    init.add_argument("--url", default=None, help="Specify base URL")
    init.add_argument(
        "--login-collection",
        default=None,
        help="Default login collection (API door)",
    )
    init.set_defaults(workspace_handler=cmd_init)
