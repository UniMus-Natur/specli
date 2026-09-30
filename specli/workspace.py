"""Config-repo workspace bound to a specli context (scope-first layout)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from specli import configstore

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required. Reinstall specli: pip install -e .")

SPECLI_YAML = "specli.yaml"
API_VERSION = "specli/v1"

LAYOUT_DIRS = (
    "backstop/forms",
    "backstop/app",
    "base/forms",
    "base/app",
    "by/discipline",
    "by/collection",
    "by/usertype",
    "by/user",
)

DEFAULT_WORKSPACE_META: dict[str, Any] = {
    "apiVersion": API_VERSION,
    "layout": "scope-first",
    "defaultScopes": ["backstop", "common", "discipline", "collection", "usertype"],
}


def resolve_workspace(explicit: str | None = None) -> Path | None:
    if explicit and explicit.strip():
        return Path(explicit).expanduser().resolve()
    env = (os.getenv("SPECLI_WORKSPACE") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    try:
        ctx = configstore.get_context()
    except SystemExit:
        return None
    if not ctx.workspace:
        return None
    return Path(ctx.workspace).expanduser().resolve()


def require_workspace(explicit: str | None = None) -> Path:
    path = resolve_workspace(explicit)
    if path is None:
        sys.exit(
            "No workspace configured for this context.\n"
            "Bind a config repo with:\n"
            "  specli workspace init ./specify-config\n"
            "or:\n"
            "  specli workspace set /path/to/existing-repo"
        )
    return path


def ensure_layout(workspace: Path) -> list[Path]:
    created: list[Path] = []
    for rel in LAYOUT_DIRS:
        target = workspace / rel
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
            created.append(target)
    meta_path = workspace / SPECLI_YAML
    if not meta_path.exists():
        with meta_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(DEFAULT_WORKSPACE_META, fh, sort_keys=False)
        created.append(meta_path)
    return created


def load_workspace_meta(workspace: Path) -> dict[str, Any]:
    path = workspace / SPECLI_YAML
    if not path.exists():
        return dict(DEFAULT_WORKSPACE_META)
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        sys.exit(f"Invalid {SPECLI_YAML}: expected mapping")
    return data


def set_workspace_on_current_context(workspace: Path) -> str:
    config = configstore.load_config()
    name = config.current_context
    if not name or name not in config.contexts:
        sys.exit("No current context. Run: specli auth login")
    ctx = config.contexts[name]
    ctx.workspace = str(workspace.resolve())
    configstore.save_config(config)
    return name


def enter_workspace(explicit: str | None = None) -> Path:
    workspace = require_workspace(explicit)
    if not workspace.is_dir():
        sys.exit(f"Workspace is not a directory: {workspace}")
    os.chdir(workspace)
    return workspace
