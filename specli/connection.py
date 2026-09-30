"""Resolve Specify connection settings from context, env overrides, or CLI."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

import requests

from specli import configstore, secrets
from specli.configstore import Context
from specli.domain import open_session


@dataclass(frozen=True)
class Connection:
    """Resolved connection for a Specify *instance*."""

    url: str
    user: str
    password: str
    login_collection: str | None = None
    context_name: str | None = None

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
        context_name=None,
    )


def resolve_connection(
    *,
    context_name: str | None = None,
    login_collection_override: str | None = None,
) -> Connection:
    """Resolve connection for the active (or named) context.

    Precedence:
    1. SPECLI_URL + SPECLI_USER + SPECLI_PASSWORD (CI)
    2. Named/current context from ``~/.specli/config.yaml`` + secret store
    """
    env_conn = _connection_from_env(
        login_collection_override=login_collection_override,
    )
    if env_conn is not None:
        return env_conn

    ctx: Context = configstore.get_context(context_name)
    password = secrets.get_password(ctx.name)
    if not password:
        sys.exit(
            f"No password stored for context {ctx.name!r}.\n"
            f"Run: specli auth login --context {ctx.name}"
        )

    login_collection = (login_collection_override or "").strip() or ctx.login_collection
    return Connection(
        url=ctx.url,
        user=ctx.user,
        password=password,
        login_collection=login_collection,
        context_name=ctx.name,
    )
