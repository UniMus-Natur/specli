"""Cross-platform specli config (kubectl-style contexts).

Default location (Windows + Unix)::

    ~/.specli/config.yaml

A context is an *instance* (url + user). ``login-collection`` is only the default
collection Specify requires at login — not the scope of what specli manages.
"""

from __future__ import annotations

import os
import stat
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required. Reinstall specli: pip install -e .")

API_VERSION = "specli/v1"
CONFIG_DIR_NAME = ".specli"
CONFIG_FILE_NAME = "config.yaml"
CREDENTIALS_FILE_NAME = "credentials.yaml"


def config_dir() -> Path:
    explicit = (os.getenv("SPECLI_CONFIG_DIR") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path.home() / CONFIG_DIR_NAME


def config_path() -> Path:
    explicit = (os.getenv("SPECLI_CONFIG") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return config_dir() / CONFIG_FILE_NAME


def credentials_path() -> Path:
    return config_dir() / CREDENTIALS_FILE_NAME


@dataclass
class Context:
    name: str
    url: str
    user: str
    login_collection: str | None = None
    workspace: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "url": self.url.rstrip("/"),
            "user": self.user,
        }
        if self.login_collection:
            out["login-collection"] = self.login_collection
        if self.workspace:
            out["workspace"] = self.workspace
        return out

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> Context:
        url = (data.get("url") or "").strip()
        user = (data.get("user") or "").strip()
        if not url or not user:
            raise ValueError(f"context {name!r} requires url and user")
        login_collection = (data.get("login-collection") or "").strip() or None
        workspace = (data.get("workspace") or "").strip() or None
        return cls(
            name=name,
            url=url.rstrip("/"),
            user=user,
            login_collection=login_collection,
            workspace=workspace,
        )


@dataclass
class Config:
    current_context: str | None = None
    contexts: dict[str, Context] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "apiVersion": API_VERSION,
            "current-context": self.current_context,
            "contexts": {name: ctx.to_dict() for name, ctx in sorted(self.contexts.items())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Config:
        data = data or {}
        contexts_raw = data.get("contexts") or {}
        contexts: dict[str, Context] = {}
        if not isinstance(contexts_raw, dict):
            raise ValueError("config.contexts must be a mapping")
        for name, raw in contexts_raw.items():
            if not isinstance(raw, dict):
                raise ValueError(f"context {name!r} must be a mapping")
            contexts[str(name)] = Context.from_dict(str(name), raw)
        current = data.get("current-context")
        if current is not None:
            current = str(current).strip() or None
        return cls(current_context=current, contexts=contexts)


def _empty_config() -> Config:
    return Config()


def load_config() -> Config:
    path = config_path()
    if not path.exists():
        return _empty_config()
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return _empty_config()
    if not isinstance(data, dict):
        sys.exit(f"Invalid specli config (not a mapping): {path}")
    try:
        return Config.from_dict(data)
    except ValueError as exc:
        sys.exit(f"Invalid specli config ({path}): {exc}")


def save_config(config: Config) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.to_dict()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)
    _restrict_permissions(path)
    return path


def load_credentials() -> dict[str, dict[str, str]]:
    """Return ``{context_name: {"password": ...}}`` from the credentials file."""
    path = credentials_path()
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return {}
    contexts = data.get("contexts") or {}
    if not isinstance(contexts, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for name, raw in contexts.items():
        if isinstance(raw, dict) and raw.get("password"):
            out[str(name)] = {"password": str(raw["password"])}
    return out


def save_credentials(credentials: dict[str, dict[str, str]]) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"contexts": deepcopy(credentials)}
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)
    _restrict_permissions(path)
    return path


def set_context_password_file(context_name: str, password: str) -> None:
    creds = load_credentials()
    creds[context_name] = {"password": password}
    save_credentials(creds)


def delete_context_password_file(context_name: str) -> None:
    creds = load_credentials()
    if context_name in creds:
        del creds[context_name]
        save_credentials(creds)


def _restrict_permissions(path: Path) -> None:
    """Best-effort owner-only mode (no-op / ignored on some Windows setups)."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def resolve_context_name(explicit: str | None = None) -> str | None:
    """Pick context name: CLI/env override, else current-context."""
    if explicit and explicit.strip():
        return explicit.strip()
    env = (os.getenv("SPECLI_CONTEXT") or "").strip()
    if env:
        return env
    return load_config().current_context


def get_context(name: str | None = None) -> Context:
    config = load_config()
    resolved = resolve_context_name(name)
    if not resolved:
        sys.exit(
            "No specli context selected. Run:\n"
            "  specli auth login\n"
            "or:\n"
            "  specli config use-context <name>"
        )
    ctx = config.contexts.get(resolved)
    if ctx is None:
        available = ", ".join(sorted(config.contexts)) or "(none)"
        sys.exit(
            f"Unknown context {resolved!r}. Available: {available}\n"
            "List with: specli config get-contexts"
        )
    return ctx
