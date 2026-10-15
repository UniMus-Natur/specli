"""Resolve Specify connection settings from the local repo or CI env."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

import requests

from specli import secrets
from specli.domain import open_session
from specli.workspace import require_workspace, workspace_connection


@dataclass(frozen=True)
class Connection:
    """Resolved connection for a Specify *instance*."""

    url: str
    user: str
    password: str
    login_collection: str | None = None
    workspace: str | None = None

    def session(
        self,
        *,
        discipline: str | None = None,
        login_collection: str | None = None,
        log_prefix: str = "specli",
    ) -> tuple[requests.Session, str, int, dict[str, Any] | None]:
        """Open a session, optionally switched into ``discipline``."""
        return open_session(
            url=self.url,
            user=self.user,
            password=self.password,
            login_collection=login_collection or self.login_collection,
            discipline=discipline,
            log_prefix=log_prefix,
        )


def _env(*names: str) -> str | None:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def _connection_from_env(
    *,
    login_collection_override: str | None = None,
) -> Connection | None:
    """CI escape hatch when SPECLI_URL + USER + PASSWORD are all set."""
    url = _env("SPECLI_URL")
    user = _env("SPECLI_USER")
    password = _env("SPECLI_PASSWORD")
    if not (url and user and password):
        return None
    login_collection = login_collection_override or _env("SPECLI_LOGIN_COLLECTION")
    return Connection(
        url=url.rstrip("/"),
        user=user,
        password=password,
        login_collection=login_collection,
        workspace=_env("SPECLI_WORKSPACE"),
    )


def resolve_connection(
    *,
    login_collection_override: str | None = None,
) -> Connection:
    """Resolve connection for the repository discovered from cwd / env.

    Precedence:
    1. SPECLI_URL + SPECLI_USER + SPECLI_PASSWORD (CI)
    2. ``workspace.yaml`` (instance) + ``.specli.local.yaml`` (user, password)
    """
    env_conn = _connection_from_env(
        login_collection_override=login_collection_override,
    )
    if env_conn is not None:
        return env_conn

    workspace = require_workspace()
    conn = workspace_connection(workspace)
    user = secrets.get_user(workspace) or conn.get("legacyUser") or ""
    password = secrets.get_password(workspace)
    if not user or not password:
        sys.exit(
            f"No credentials in {workspace / '.specli.local.yaml'}.\n"
            "Run: specli auth login"
        )

    login_collection = (
        (login_collection_override or "").strip() or conn["loginCollection"] or None
    )
    return Connection(
        url=conn["instance"],
        user=user,
        password=password,
        login_collection=login_collection,
        workspace=str(workspace),
    )
