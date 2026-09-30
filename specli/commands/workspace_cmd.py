"""``specli workspace`` — bind a scope-first config directory to the current context."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from specli import configstore, workspace as ws


def cmd_get(_args: argparse.Namespace) -> int:
    path = ws.resolve_workspace()
    if path is None:
        print("No workspace set for the current context.", file=sys.stderr)
        return 1
    print(path)
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    config = configstore.load_config()
    name = config.current_context
    if not name:
        print("No current context.", file=sys.stderr)
        return 1
    ctx = config.contexts.get(name)
    if ctx is None:
        print(f"Unknown context {name!r}", file=sys.stderr)
        return 1
    print(f"context: {name}")
    print(f"workspace: {ctx.workspace or '(not set)'}")
    if ctx.workspace:
        root = Path(ctx.workspace).expanduser()
        print(f"exists: {root.is_dir()}")
        if root.is_dir():
            meta = root / ws.SPECLI_YAML
            print(f"  [{'ok' if meta.exists() else 'missing'}] {ws.SPECLI_YAML}")
            for rel in ws.LAYOUT_DIRS:
                mark = "ok" if (root / rel).is_dir() else "missing"
                print(f"  [{mark}] {rel}")
            layout = ws.load_workspace_meta(root).get("layout", "?")
            print(f"layout: {layout}")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        sys.exit(f"Path does not exist: {path}")
    if not path.is_dir():
        sys.exit(f"Not a directory: {path}")
    name = ws.set_workspace_on_current_context(path)
    print(f"Context {name!r} workspace → {path}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    created = ws.ensure_layout(path)
    name = ws.set_workspace_on_current_context(path)

    print(f"Context {name!r} workspace → {path}")
    if created:
        print("Created:")
        for item in created:
            try:
                print(f"  {item.relative_to(path)}")
            except ValueError:
                print(f"  {item}")
    else:
        print("Layout already present.")
    print()
    print("Next: specli pull --clean")
    return 0


def build_parser(sub: argparse._SubParsersAction) -> None:
    workspace = sub.add_parser(
        "workspace",
        help="Bind a scope-first config directory to the current context",
    )
    cmd = workspace.add_subparsers(dest="workspace_cmd", required=True)

    get_p = cmd.add_parser("get", help="Print the workspace path")
    get_p.set_defaults(workspace_handler=cmd_get)

    status = cmd.add_parser("status", help="Show workspace binding and layout")
    status.set_defaults(workspace_handler=cmd_status)

    set_p = cmd.add_parser("set", help="Bind an existing directory as the workspace")
    set_p.add_argument("path", help="Path to the config directory")
    set_p.set_defaults(workspace_handler=cmd_set)

    init = cmd.add_parser(
        "init",
        help="Create scope-first layout dirs and bind as workspace",
    )
    init.add_argument("path", nargs="?", default=".", help="Directory (default: .)")
    init.set_defaults(workspace_handler=cmd_init)
