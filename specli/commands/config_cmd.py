"""kubectl-style ``specli config`` commands."""

from __future__ import annotations

import argparse
import sys

from specli import configstore


def _print_contexts(config: configstore.Config, *, show_current: bool = True) -> None:
    current = config.current_context
    if not config.contexts:
        print("No contexts defined. Run: specli auth login")
        return
    for name in sorted(config.contexts):
        mark = "*" if show_current and name == current else " "
        ctx = config.contexts[name]
        login_col = ctx.login_collection or "-"
        ws = ctx.workspace or "-"
        print(
            f"{mark} {name}\t{ctx.user}@{ctx.url}\t"
            f"login-collection={login_col}\tworkspace={ws}"
        )


def cmd_view(_args: argparse.Namespace) -> int:
    path = configstore.config_path()
    if not path.exists():
        print(f"No config at {path}", file=sys.stderr)
        print("Run: specli auth login", file=sys.stderr)
        return 1
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def cmd_get_contexts(_args: argparse.Namespace) -> int:
    _print_contexts(configstore.load_config())
    return 0


def cmd_current_context(_args: argparse.Namespace) -> int:
    config = configstore.load_config()
    if not config.current_context:
        print("No current context is set", file=sys.stderr)
        return 1
    print(config.current_context)
    return 0


def cmd_use_context(args: argparse.Namespace) -> int:
    name = args.name.strip()
    config = configstore.load_config()
    if name not in config.contexts:
        available = ", ".join(sorted(config.contexts)) or "(none)"
        print(f"Unknown context {name!r}. Available: {available}", file=sys.stderr)
        return 1
    config.current_context = name
    path = configstore.save_config(config)
    print(f"Switched to context {name!r} ({path})")
    return 0


def cmd_set_context(args: argparse.Namespace) -> int:
    name = args.name.strip()
    config = configstore.load_config()
    existing = config.contexts.get(name)
    url = (args.url or (existing.url if existing else "")).strip()
    user = (args.user or (existing.user if existing else "")).strip()
    if not url or not user:
        print(
            "Context requires --url and --user (or an existing context to patch).",
            file=sys.stderr,
        )
        return 1

    if args.login_collection is None and existing:
        login_collection = existing.login_collection
    elif args.login_collection is not None:
        login_collection = args.login_collection.strip() or None
    else:
        login_collection = None

    if args.workspace is None and existing:
        workspace = existing.workspace
    elif args.workspace is not None:
        workspace = args.workspace.strip() or None
        if workspace:
            from pathlib import Path

            workspace = str(Path(workspace).expanduser().resolve())
    else:
        workspace = None

    config.contexts[name] = configstore.Context(
        name=name,
        url=url.rstrip("/"),
        user=user,
        login_collection=login_collection,
        workspace=workspace,
    )
    if args.current or not config.current_context:
        config.current_context = name
    path = configstore.save_config(config)
    print(f"Context {name!r} saved ({path})")
    return 0


def cmd_delete_context(args: argparse.Namespace) -> int:
    name = args.name.strip()
    config = configstore.load_config()
    if name not in config.contexts:
        print(f"Unknown context {name!r}", file=sys.stderr)
        return 1
    del config.contexts[name]
    if config.current_context == name:
        config.current_context = next(iter(sorted(config.contexts)), None)
    configstore.save_config(config)

    from specli import secrets

    secrets.delete_password(name)
    print(f"Deleted context {name!r}")
    return 0


def build_parser(sub: argparse._SubParsersAction) -> None:
    config = sub.add_parser(
        "config",
        help="View and switch specli contexts (kubectl-style)",
    )
    cmd = config.add_subparsers(dest="config_cmd", required=True)

    view = cmd.add_parser("view", help="Show the config file")
    view.set_defaults(config_handler=cmd_view)

    get_contexts = cmd.add_parser(
        "get-contexts",
        help="List contexts (* = current)",
    )
    get_contexts.set_defaults(config_handler=cmd_get_contexts)

    current = cmd.add_parser("current-context", help="Print the current context name")
    current.set_defaults(config_handler=cmd_current_context)

    use = cmd.add_parser("use-context", help="Switch the current context")
    use.add_argument("name", help="Context name")
    use.set_defaults(config_handler=cmd_use_context)

    set_ctx = cmd.add_parser(
        "set-context",
        help="Create or update a context (non-secret fields)",
    )
    set_ctx.add_argument("name", help="Context name")
    set_ctx.add_argument("--url", default=None, help="Specify base URL")
    set_ctx.add_argument("--user", default=None, help="Specify username")
    set_ctx.add_argument(
        "--login-collection",
        default=None,
        help="Default login collection (API door; not sync scope)",
    )
    set_ctx.add_argument(
        "--workspace",
        default=None,
        help="Config-repo directory bound to this context",
    )
    set_ctx.add_argument(
        "--current",
        action="store_true",
        help="Also set as current-context",
    )
    set_ctx.set_defaults(config_handler=cmd_set_context)

    delete = cmd.add_parser("delete-context", help="Delete a context and its stored password")
    delete.add_argument("name", help="Context name")
    delete.set_defaults(config_handler=cmd_delete_context)
