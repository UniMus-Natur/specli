"""Discover and manage a self-contained specli/v3 repository."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from specli.repository_v3 import API_VERSION, WORKSPACE_FILE, init_repository

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required. Reinstall specli: pip install -e .")

SPECLI_YAML = WORKSPACE_FILE
LOCAL_SECRETS_FILE = ".specli.local.yaml"
GITIGNORE_FILE = ".gitignore"
LAYOUT_DIRS = ("forms", "viewsets", "schema", "resources", "records", "state")

# Single state-lock name for a repo-local instance binding.
STATE_CONTEXT = "local"

_GITIGNORE_ENTRIES = (
    ".specli.local.yaml",
    ".specli.local.yaml~",
)


def find_workspace(start: Path | None = None) -> Path | None:
    """Walk upward from *start* (default: cwd) looking for ``workspace.yaml``."""
    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / WORKSPACE_FILE).is_file():
            return candidate
    return None


def resolve_workspace(explicit: str | None = None) -> Path | None:
    if explicit and explicit.strip():
        return Path(explicit).expanduser().resolve()
    env = (os.getenv("SPECLI_WORKSPACE") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return find_workspace()


def require_workspace(explicit: str | None = None) -> Path:
    path = resolve_workspace(explicit)
    if path is None:
        sys.exit(
            "No specli/v3 repository found.\n"
            "Run from inside a config repo, or create one with:\n"
            "  specli workspace init .\n"
            "Or set SPECLI_WORKSPACE=/path/to/repo"
        )
    if not path.is_dir():
        sys.exit(f"Workspace is not a directory: {path}")
    load_workspace_meta(path)
    return path


def load_workspace_meta(workspace: Path) -> dict[str, Any]:
    path = workspace / SPECLI_YAML
    if not path.exists():
        sys.exit(f"{workspace}: not a specli/v3 repository (missing {SPECLI_YAML})")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        sys.exit(f"Invalid {SPECLI_YAML}: expected mapping")
    api = data.get("apiVersion")
    if api != API_VERSION:
        sys.exit(
            f"{path}: apiVersion {api!r} is not supported "
            f"(required {API_VERSION!r}); legacy layouts are not supported"
        )
    if data.get("kind") != "Workspace":
        sys.exit(f"{path}: kind must be 'Workspace'")
    return data


def workspace_connection(workspace: Path) -> dict[str, str]:
    """Return committed connection fields from ``workspace.yaml``.

    Username is *not* part of the committed workspace — it lives in
    ``.specli.local.yaml``. A legacy ``user`` key in ``workspace.yaml`` is
    returned only as a migration fallback.
    """
    meta = load_workspace_meta(workspace)
    instance = str(meta.get("instance") or "").strip().rstrip("/")
    login_collection = str(meta.get("loginCollection") or "").strip()
    legacy_user = str(meta.get("user") or "").strip()
    if not instance:
        sys.exit(
            f"{workspace / SPECLI_YAML}: missing instance URL.\n"
            "Set it with: specli workspace init --url …"
        )
    return {
        "instance": instance,
        "loginCollection": login_collection,
        "legacyUser": legacy_user,
    }


def write_workspace_connection(
    workspace: Path,
    *,
    instance: str,
    login_collection: str | None,
    strip_user: bool = True,
) -> None:
    """Write committed connection fields. Never stores username here."""
    meta = load_workspace_meta(workspace)
    meta["instance"] = instance.rstrip("/")
    if login_collection:
        meta["loginCollection"] = login_collection
    else:
        meta.pop("loginCollection", None)
    if strip_user:
        meta.pop("user", None)
    path = workspace / SPECLI_YAML
    path.write_text(
        yaml.safe_dump(meta, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def ensure_secrets_gitignore(workspace: Path) -> Path:
    path = workspace / GITIGNORE_FILE
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.splitlines()
    changed = False
    for entry in _GITIGNORE_ENTRIES:
        if entry not in lines:
            lines.append(entry)
            changed = True
    if not path.exists() or changed:
        text = "\n".join(lines).rstrip() + "\n"
        path.write_text(text, encoding="utf-8")
    return path


def ensure_layout(
    workspace: Path,
    *,
    instance: str | None = None,
    login_collection: str | None = None,
) -> list[Path]:
    created: list[Path] = []
    if not (workspace / WORKSPACE_FILE).exists():
        init_repository(
            workspace,
            instance=instance,
            login_collection=login_collection,
        )
        created.extend([workspace / WORKSPACE_FILE, workspace / "inventory.yaml"])
    else:
        if instance or login_collection is not None:
            meta = load_workspace_meta(workspace)
            write_workspace_connection(
                workspace,
                instance=instance or str(meta.get("instance") or ""),
                login_collection=login_collection
                if login_collection is not None
                else (str(meta.get("loginCollection") or "") or None),
            )
    ignore = ensure_secrets_gitignore(workspace)
    if ignore not in created and ignore.exists():
        # Only report when we just created an empty repo; still ensure file exists.
        if created:
            created.append(ignore)
    return created
