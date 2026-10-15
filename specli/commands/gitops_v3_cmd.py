"""specli/v3 staged pull, validation, and three-way status."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from specli.connection import resolve_connection
from specli.remote_v3 import pull_remote_repository
from specli.repository_v3 import ContextState, Inventory
from specli.sync_engine import (
    baseline_states,
    compare_three_way,
    explicit_deletion_uids,
    local_resource_states,
    staged_repository,
    validate_repository,
)
from specli.workspace import STATE_CONTEXT, require_workspace


def _context_name(_conn=None) -> str:
    return STATE_CONTEXT


def _state_path(workspace: Path, context_name: str) -> Path:
    from specli.client import safe_slug

    return workspace / "state" / f"{safe_slug(context_name)}.lock.yaml"


def _assert_no_local_edits(workspace: Path, context_name: str) -> None:
    if not (workspace / "workspace.yaml").is_file():
        return
    validate_repository(workspace)
    state_path = _state_path(workspace, context_name)
    if not state_path.is_file():
        inventory = Inventory.load(workspace)
        if not inventory.entries:
            # `workspace init` deliberately creates an empty repository with no
            # observed state. Its first pull is the operation that establishes
            # the baseline.
            return
        sys.exit(
            f"{workspace}: v3 repository has no observed state for "
            f"context {context_name!r}; only an empty initialized repository "
            "may be pulled without a state lock"
        )
    baseline = baseline_states(ContextState.load(state_path))
    local = local_resource_states(workspace)
    changes = compare_three_way(baseline, local, baseline)
    dirty = [entry for entry in changes if entry.kind not in ("clean", "already-applied")]
    if dirty:
        names = ", ".join(entry.uid for entry in dirty[:10])
        more = f" (+{len(dirty) - 10} more)" if len(dirty) > 10 else ""
        sys.exit(
            f"local desired state has uncommitted/unpushed changes: {names}{more}. "
            "Run status; pull never discards local edits."
        )


def pull(
    *,
    workspace: Path,
    login_collection: str | None,
    include_personal: bool,
    language: str,
) -> int:
    conn = resolve_connection(login_collection_override=login_collection)
    context_name = _context_name(conn)
    _assert_no_local_edits(workspace, context_name)

    def builder(stage: Path) -> None:
        pull_remote_repository(
            stage,
            conn=conn,
            context_name=context_name,
            include_personal=include_personal,
            language=language,
        )

    staged_repository(workspace, builder)
    print(f"[pull] v3 snapshot published → {workspace}", file=sys.stderr)
    return 0


def status(
    *,
    workspace: Path,
    login_collection: str | None,
    include_personal: bool,
    language: str,
) -> int:
    validate_repository(workspace)
    conn = resolve_connection(login_collection_override=login_collection)
    context_name = _context_name(conn)
    state_path = _state_path(workspace, context_name)
    baseline = baseline_states(ContextState.load(state_path))
    local = local_resource_states(workspace)

    with tempfile.TemporaryDirectory(prefix=".specli-status-") as tmp:
        remote_root = Path(tmp)
        pull_remote_repository(
            remote_root,
            conn=conn,
            context_name=context_name,
            include_personal=include_personal,
            language=language,
        )
        remote = local_resource_states(remote_root)

    entries = compare_three_way(
        baseline,
        local,
        remote,
        explicit_deletions=explicit_deletion_uids(workspace),
    )
    changed = 0
    invalid = False
    for entry in entries:
        if entry.kind in ("clean", "already-applied"):
            continue
        print(
            f"{entry.kind:16} {entry.uid} "
            f"baseline={entry.baseline or '-'} "
            f"local={entry.local or '-'} remote={entry.remote or '-'}"
        )
        if entry.kind in ("conflict", "remote-drift"):
            invalid = True
        else:
            changed += 1
    print(
        f"[status] {len(entries)} resources checked; "
        f"{changed} local changes; {'conflicts present' if invalid else 'remote clean'}",
        file=sys.stderr,
    )
    if invalid:
        return 3
    return 2 if changed else 0


def cmd_pull(args: argparse.Namespace) -> int:
    workspace = require_workspace()
    return pull(
        workspace=workspace,
        login_collection=args.login_collection,
        include_personal=args.include_personal,
        language=args.lang,
    )


def cmd_status(args: argparse.Namespace) -> int:
    workspace = require_workspace()
    return status(
        workspace=workspace,
        login_collection=args.login_collection,
        include_personal=args.include_personal,
        language=args.lang,
    )


def cmd_validate(_args: argparse.Namespace) -> int:
    workspace = require_workspace()
    validate_repository(workspace)
    print(f"[validate] OK → {workspace}", file=sys.stderr)
    return 0


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--login-collection", default=None)
    parser.add_argument("--include-personal", action="store_true")
    parser.add_argument("--lang", default="en")


def build_parser(sub: argparse._SubParsersAction) -> None:
    command = sub.add_parser("gitops", help="specli/v3 repository operations")
    actions = command.add_subparsers(dest="gitops_command", required=True)

    pull_parser = actions.add_parser("pull", help="Stage and publish a complete v3 snapshot")
    add_common_flags(pull_parser)
    pull_parser.set_defaults(gitops_handler=cmd_pull)

    status_parser = actions.add_parser("status", help="Three-way local/remote status")
    add_common_flags(status_parser)
    status_parser.set_defaults(gitops_handler=cmd_status)

    validate_parser = actions.add_parser("validate", help="Validate the v3 repository")
    validate_parser.set_defaults(gitops_handler=cmd_validate)
