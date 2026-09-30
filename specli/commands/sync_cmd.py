"""``specli pull`` / ``specli push`` / ``specli status`` — full workspace sync."""

from __future__ import annotations

import argparse
import shutil
import sys

from specli.client import disable_insecure_request_warnings
from specli.commands import app_cmd, forms_cmd, schema_cmd
from specli.workspace import enter_workspace, ensure_layout, require_workspace


def _add_common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--scope",
        action="append",
        default=None,
        help="Limit to scope (repeatable): backstop | common | discipline/X | …",
    )
    p.add_argument("--discipline", default=None, help="Filter discipline-rooted scopes")
    p.add_argument(
        "--include-personal",
        action="store_true",
        help="Include by/user/* (Personal) scopes",
    )
    p.add_argument("--login-collection", default=None)


def cmd_pull(args: argparse.Namespace) -> int:
    disable_insecure_request_warnings()
    workspace = enter_workspace()
    ensure_layout(workspace)
    print(f"[pull] workspace={workspace}", file=sys.stderr)

    if args.clean:
        for rel in ("backstop/forms", "backstop/app", "base/forms", "base/app"):
            target = workspace / rel
            if target.exists():
                shutil.rmtree(target)
                target.mkdir(parents=True, exist_ok=True)
        by = workspace / "by"
        if by.exists():
            shutil.rmtree(by)
            for rel in ("discipline", "collection", "usertype", "user"):
                (by / rel).mkdir(parents=True, exist_ok=True)

    print("[pull] forms …", file=sys.stderr)
    forms_cmd.cmd_pull(
        argparse.Namespace(
            scope=args.scope,
            discipline=args.discipline,
            include_personal=args.include_personal,
            login_collection=args.login_collection,
            clean=False,
        )
    )

    print("[pull] schema …", file=sys.stderr)
    schema_cmd.cmd_pull(
        argparse.Namespace(
            discipline=args.discipline,
            lang="en",
            clean=False,
            only_tables=None,
            login_collection=args.login_collection,
        )
    )

    print("[pull] app resources …", file=sys.stderr)
    app_cmd.cmd_pull(
        argparse.Namespace(
            scope=args.scope,
            discipline=args.discipline,
            include_personal=args.include_personal,
            login_collection=args.login_collection,
            only=None,
        )
    )

    print(f"[pull] done → {workspace}", file=sys.stderr)
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    disable_insecure_request_warnings()
    workspace = require_workspace()
    enter_workspace()
    dry = bool(getattr(args, "dry_run", False))
    print(f"[push] workspace={workspace} dry_run={dry}", file=sys.stderr)

    forms_cmd.cmd_push(
        argparse.Namespace(
            scope=args.scope,
            discipline=args.discipline,
            include_personal=args.include_personal,
            login_collection=args.login_collection,
            dry_run=dry,
        )
    )
    schema_cmd.cmd_push(
        argparse.Namespace(
            discipline=args.discipline,
            only_tables=None,
            create_missing=False,
            verbose_missing=False,
            login_collection=args.login_collection,
            dry_run=dry,
        )
    )
    app_cmd.cmd_push(
        argparse.Namespace(
            scope=args.scope,
            discipline=args.discipline,
            include_personal=args.include_personal,
            login_collection=args.login_collection,
            only=None,
            dry_run=dry,
        )
    )
    return 0


def build_parser(sub: argparse._SubParsersAction) -> None:
    pull = sub.add_parser(
        "pull",
        help="Pull forms, schema, and app resources into the workspace",
    )
    _add_common_flags(pull)
    pull.add_argument(
        "--clean",
        action="store_true",
        help="Wipe base/ and by/ before writing",
    )
    pull.set_defaults(pull_handler=cmd_pull)

    push = sub.add_parser(
        "push",
        help="Push workspace forms, schema, and app resources to Specify",
    )
    _add_common_flags(push)
    push.add_argument("--dry-run", action="store_true")
    push.set_defaults(push_handler=cmd_push)

    status = sub.add_parser("status", help="Dry-run push for the whole workspace")
    _add_common_flags(status)

    def _status(a: argparse.Namespace) -> int:
        a.dry_run = True
        return cmd_push(a)

    status.set_defaults(push_handler=_status)
