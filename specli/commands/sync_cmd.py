"""Top-level specli/v3 pull, status, and transactional push."""

from __future__ import annotations

import argparse
from specli.client import disable_insecure_request_warnings
from specli.commands import gitops_v3_cmd


def _add_common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--include-personal",
        action="store_true",
        help="Manage personal resources rather than recording them as excluded",
    )
    p.add_argument("--login-collection", default=None)
    p.add_argument("--lang", default="en")


def cmd_pull(args: argparse.Namespace) -> int:
    disable_insecure_request_warnings()
    return gitops_v3_cmd.cmd_pull(args)


def cmd_push(args: argparse.Namespace) -> int:
    disable_insecure_request_warnings()
    from specli.transactional_push import cmd_push as transactional_push

    return transactional_push(args)


def build_parser(sub: argparse._SubParsersAction) -> None:
    pull = sub.add_parser(
        "pull",
        help="Pull forms, schema, and app resources into the workspace",
    )
    _add_common_flags(pull)
    pull.set_defaults(pull_handler=cmd_pull)

    push = sub.add_parser(
        "push",
        help="Push workspace forms, schema, and app resources to Specify",
    )
    _add_common_flags(push)
    push.add_argument("--dry-run", action="store_true")
    push.set_defaults(push_handler=cmd_push)

    status = sub.add_parser("status", help="Three-way local/baseline/remote status")
    _add_common_flags(status)

    def _status(a: argparse.Namespace) -> int:
        disable_insecure_request_warnings()
        return gitops_v3_cmd.cmd_status(a)

    status.set_defaults(push_handler=_status)
