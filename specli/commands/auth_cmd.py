"""Interactive auth against the repository discovered from cwd."""

from __future__ import annotations

import argparse
import getpass
import sys

from specli import secrets
from specli.client import disable_insecure_request_warnings, login
from specli.connection import resolve_connection
from specli.workspace import (
    LOCAL_SECRETS_FILE,
    load_workspace_meta,
    require_workspace,
    write_workspace_connection,
)


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


def _prompt_password() -> str:
    while True:
        try:
            password = getpass.getpass("Password: ")
        except EOFError:
            sys.exit("\nAborted.")
        if password:
            return password
        print("Password required.", file=sys.stderr)


def _pick_login_collection(collections: dict[str, int], preferred: str | None) -> str:
    names = sorted(collections)
    if preferred:
        needle = preferred.lower()
        for name in names:
            if name.lower() == needle:
                return name
    if len(names) == 1:
        return names[0]
    print(
        "Specify requires a collection at login. This is only a default door into the\n"
        "instance — specli manages the whole server."
    )
    print("Available collections:")
    for i, name in enumerate(names, 1):
        print(f"  {i}. {name}")
    while True:
        raw = _prompt(
            "Default login collection (number or name)",
            default=preferred or names[0],
        )
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(names):
                return names[idx - 1]
        for name in names:
            if name.lower() == raw.lower():
                return name
        print("Invalid choice.", file=sys.stderr)


def cmd_login(args: argparse.Namespace) -> int:
    disable_insecure_request_warnings()
    workspace = require_workspace()
    meta = load_workspace_meta(workspace)

    print(f"Logging in for repository {workspace}")
    url = (args.url or "").strip() or _prompt(
        "Specify URL",
        default=str(meta.get("instance") or "").strip() or None,
    )
    url = url.rstrip("/")
    user_default = (
        secrets.get_user(workspace)
        or str(meta.get("user") or "").strip()
        or None
    )
    user = (args.user or "").strip() or _prompt("Username", default=user_default)
    password = (args.password or "").strip() or _prompt_password()

    import requests

    session = requests.Session()
    session.verify = False
    try:
        res = session.get(f"{url}/context/login/", timeout=20)
    except requests.RequestException as exc:
        print(f"Could not reach {url}: {exc}", file=sys.stderr)
        return 1
    if res.status_code != 200:
        print(
            f"GET /context/login/ failed ({res.status_code}): {res.text[:400]}",
            file=sys.stderr,
        )
        return 1
    collections = res.json().get("collections") or {}
    if not collections:
        print("No collections returned by Specify.", file=sys.stderr)
        return 1

    preferred = (args.login_collection or "").strip() or (
        str(meta.get("loginCollection") or "").strip() or None
    )
    if args.login_collection:
        login_collection = args.login_collection.strip()
    else:
        login_collection = _pick_login_collection(collections, preferred)

    try:
        login(url, user, password, login_collection, log_prefix="auth")
    except SystemExit as exc:
        code = exc.code
        if code not in (0, None):
            if isinstance(code, str):
                print(code, file=sys.stderr)
            return 1 if not isinstance(code, int) else code
        raise

    write_workspace_connection(
        workspace,
        instance=url,
        login_collection=login_collection,
        strip_user=True,
    )
    secret_path = secrets.set_credentials(workspace, user=user, password=password)
    print(f"Instance saved in {workspace / 'workspace.yaml'}")
    print(f"Credentials stored in {secret_path} (gitignored)")
    print(f"login-collection: {login_collection} (default API door, not sync scope)")
    print("Next: specli pull")
    return 0


def cmd_logout(_args: argparse.Namespace) -> int:
    workspace = require_workspace()
    secrets.clear_credentials(workspace)
    print(f"Cleared credentials from {workspace / LOCAL_SECRETS_FILE}")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    disable_insecure_request_warnings()
    workspace = require_workspace()
    try:
        conn = resolve_connection()
    except SystemExit as exc:
        code = exc.code
        if code in (0, None):
            raise
        if isinstance(code, str):
            print(code, file=sys.stderr)
            return 1
        return int(code)
    print(f"workspace: {workspace}")
    print(f"url: {conn.url}")
    print(f"user: {conn.user}")
    print(f"login-collection: {conn.login_collection or '(auto)'}")
    print(f"secrets: {workspace / LOCAL_SECRETS_FILE}")
    if args.check:
        try:
            session, selected, _col_id, disc = conn.session(log_prefix="whoami")
            session.close()
            disc_label = ""
            if disc:
                from specli.domain import discipline_slug

                disc_label = f", discipline={discipline_slug(disc)}"
            print(f"login: ok ({selected}{disc_label})")
        except SystemExit as exc:
            code = exc.code
            if isinstance(code, str):
                print(code, file=sys.stderr)
            print("login: failed", file=sys.stderr)
            return 1
    return 0


def build_parser(sub: argparse._SubParsersAction) -> None:
    auth = sub.add_parser("auth", help="Log in, store secrets, inspect identity")
    cmd = auth.add_subparsers(dest="auth_cmd", required=True)

    login_p = cmd.add_parser("login", help="Interactive login into this repository")
    login_p.add_argument("--url", default=None, help="Specify base URL")
    login_p.add_argument("--user", default=None, help="Username")
    login_p.add_argument("--password", default=None, help="Password (prefer prompt)")
    login_p.add_argument(
        "--login-collection",
        default=None,
        help="Default login collection (API door into the instance)",
    )
    login_p.set_defaults(auth_handler=cmd_login)

    logout_p = cmd.add_parser("logout", help="Remove local credentials")
    logout_p.set_defaults(auth_handler=cmd_logout)

    whoami = cmd.add_parser("whoami", help="Show the repository connection")
    whoami.add_argument(
        "--check",
        action="store_true",
        help="Also attempt a live login against Specify",
    )
    whoami.set_defaults(auth_handler=cmd_whoami)
