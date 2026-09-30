"""specli entry point — scope-first Specify 7 GitOps CLI."""

from __future__ import annotations

import argparse
import os
import sys

from specli import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="specli",
        description=(
            "Specify 7 GitOps CLI — scope-first pull/push for forms, schema, "
            "and app resources (mirrors Specify Personal→Common hierarchy)"
        ),
        epilog=(
            "First time:\n"
            "  specli auth login\n"
            "  specli workspace init ./specify-config\n"
            "  specli pull --clean\n"
            "Config: ~/.specli/config.yaml\n"
            "CI: SPECLI_URL / SPECLI_USER / SPECLI_PASSWORD"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--context",
        default=None,
        help="Context name for this command (overrides current-context / SPECLI_CONTEXT)",
    )

    sub = parser.add_subparsers(dest="domain", required=True)

    from specli.commands import (
        app_cmd,
        auth_cmd,
        config_cmd,
        forms_cmd,
        schema_cmd,
        sync_cmd,
        workspace_cmd,
    )

    auth_cmd.build_parser(sub)
    config_cmd.build_parser(sub)
    workspace_cmd.build_parser(sub)
    sync_cmd.build_parser(sub)
    forms_cmd.build_parser(sub)
    schema_cmd.build_parser(sub)
    app_cmd.build_parser(sub)

    args = parser.parse_args(argv)

    if getattr(args, "context", None):
        os.environ["SPECLI_CONTEXT"] = args.context

    if args.domain == "config":
        return int(args.config_handler(args))
    if args.domain == "auth":
        return int(args.auth_handler(args))
    if args.domain == "workspace":
        return int(args.workspace_handler(args))
    if args.domain == "pull":
        return int(args.pull_handler(args))
    if args.domain in ("push", "status"):
        return int(args.push_handler(args))
    if args.domain == "form":
        return int(args.form_handler(args))
    if args.domain == "schema":
        return int(args.schema_handler(args))
    if args.domain == "app":
        return int(args.app_handler(args))

    parser.error(f"unknown domain: {args.domain}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
