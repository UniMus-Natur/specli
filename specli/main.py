"""specli entry point — Specify GitOps v3."""

from __future__ import annotations

import argparse
import sys

from specli import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="specli",
        description=(
            "Specify 7 GitOps CLI — exact, staged, inventory-driven configuration"
        ),
        epilog=(
            "First time (inside the config repo):\n"
            "  specli workspace init .\n"
            "  specli auth login\n"
            "  specli pull\n"
            "Secrets: .specli.local.yaml (gitignored)\n"
            "CI: SPECLI_URL / SPECLI_USER / SPECLI_PASSWORD"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="domain", required=True)

    from specli.commands import (
        auth_cmd,
        gitops_v3_cmd,
        sync_cmd,
        workspace_cmd,
    )

    auth_cmd.build_parser(sub)
    workspace_cmd.build_parser(sub)
    sync_cmd.build_parser(sub)
    gitops_v3_cmd.build_parser(sub)

    args = parser.parse_args(argv)

    if args.domain == "auth":
        return int(args.auth_handler(args))
    if args.domain == "workspace":
        return int(args.workspace_handler(args))
    if args.domain == "pull":
        return int(args.pull_handler(args))
    if args.domain in ("push", "status"):
        return int(args.push_handler(args))
    if args.domain == "gitops":
        return int(args.gitops_handler(args))

    parser.error(f"unknown domain: {args.domain}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
