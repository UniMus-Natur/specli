"""Repo-local secret storage (``.specli.local.yaml``, gitignored)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required. Reinstall specli: pip install -e .")

from specli.workspace import LOCAL_SECRETS_FILE, ensure_secrets_gitignore


def local_secrets_path(workspace: Path) -> Path:
    return workspace / LOCAL_SECRETS_FILE


def _restrict_permissions(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_local_secrets(workspace: Path) -> dict[str, Any]:
    path = local_secrets_path(workspace)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        sys.exit(f"{path}: expected a mapping")
    return data


def save_local_secrets(workspace: Path, data: dict[str, Any]) -> Path:
    ensure_secrets_gitignore(workspace)
    path = local_secrets_path(workspace)
    payload = {
        "apiVersion": "specli/v3",
        "kind": "LocalSecrets",
        **data,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)
    _restrict_permissions(path)
    return path


def set_credentials(workspace: Path, *, user: str, password: str) -> Path:
    data = load_local_secrets(workspace)
    data["user"] = user
    data["password"] = password
    return save_local_secrets(workspace, data)


def set_password(workspace: Path, password: str) -> Path:
    """Update password only (keeps existing user if any)."""
    data = load_local_secrets(workspace)
    data["password"] = password
    return save_local_secrets(workspace, data)


def get_user(workspace: Path) -> str | None:
    user = str(load_local_secrets(workspace).get("user") or "").strip()
    return user or None


def get_password(workspace: Path) -> str | None:
    password = str(load_local_secrets(workspace).get("password") or "").strip()
    return password or None


def clear_credentials(workspace: Path) -> None:
    path = local_secrets_path(workspace)
    if path.exists():
        path.unlink()


def delete_password(workspace: Path) -> None:
    """Remove password; drop the file if nothing else remains."""
    path = local_secrets_path(workspace)
    if not path.exists():
        return
    data = load_local_secrets(workspace)
    data.pop("password", None)
    remaining = {
        key: value
        for key, value in data.items()
        if key not in {"apiVersion", "kind"} and value not in (None, "")
    }
    if not remaining:
        path.unlink()
        return
    save_local_secrets(workspace, remaining)
