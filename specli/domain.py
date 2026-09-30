"""Discipline / collection helpers for instance-wide specli operations.

A specli context targets a Specify *instance*. Specify's login API still needs a
collection, so contexts store a default ``login-collection`` only as a door into
the API. Config that is discipline-scoped should be addressed with ``--discipline``
(or processed for all disciplines), not by inventing one-collection contexts.
"""

from __future__ import annotations

import sys
from typing import Any

import requests

from specli.client import get_json, iter_list_endpoint, login, resource_pk, safe_slug


def list_disciplines(session: requests.Session, base: str) -> list[dict[str, Any]]:
    rows = iter_list_endpoint(session, base, "/api/specify/discipline/?orderby=name")
    return [r for r in rows if r.get("id") is not None]


def list_collections(session: requests.Session, base: str) -> list[dict[str, Any]]:
    rows = iter_list_endpoint(session, base, "/api/specify/collection/?limit=500")
    return [r for r in rows if r.get("id") is not None]


def discipline_slug(discipline: dict[str, Any]) -> str:
    dtype = str(discipline.get("type") or "").strip()
    name = str(discipline.get("name") or f"discipline-{discipline.get('id')}")
    if dtype and dtype.lower() != "unknown":
        return safe_slug(dtype)
    return safe_slug(name)


def match_discipline(discipline: dict[str, Any], needle: str) -> bool:
    needle_l = needle.strip().lower()
    if not needle_l:
        return False
    if str(discipline.get("id")) == needle_l:
        return True
    name = str(discipline.get("name") or "").strip().lower()
    dtype = str(discipline.get("type") or "").strip().lower()
    slug = discipline_slug(discipline).lower()
    return needle_l in {name, dtype, slug}


def resolve_discipline(
    session: requests.Session,
    base: str,
    needle: str,
) -> dict[str, Any]:
    needle = (needle or "").strip()
    if not needle:
        sys.exit("Discipline name/type/id required")
    matches = [d for d in list_disciplines(session, base) if match_discipline(d, needle)]
    if not matches:
        available = ", ".join(
            f"{discipline_slug(d)}/{d.get('name')}" for d in list_disciplines(session, base)
        ) or "(none)"
        sys.exit(f"Discipline {needle!r} not found. Available: {available}")
    if len(matches) > 1:
        labels = ", ".join(f"{d.get('id')}:{discipline_slug(d)}" for d in matches)
        sys.exit(f"Discipline {needle!r} is ambiguous ({labels}). Use id or type slug.")
    return matches[0]


def collections_for_discipline(
    session: requests.Session,
    base: str,
    discipline_id: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for col in list_collections(session, base):
        if resource_pk(col.get("discipline")) == int(discipline_id):
            out.append(col)
    return out


def pick_collection_name(
    collections: list[dict[str, Any]],
    preferred_name: str | None = None,
) -> str:
    if not collections:
        sys.exit("No collections available for this discipline")
    if preferred_name:
        needle = preferred_name.strip().lower()
        for col in collections:
            name = str(col.get("collectionname") or col.get("name") or "").strip()
            if name.lower() == needle:
                return name
    # Prefer alphabetically stable pick
    named = sorted(
        (
            str(c.get("collectionname") or c.get("name") or f"collection-{c['id']}").strip()
            for c in collections
        )
    )
    return named[0]


def collection_display_name(col: dict[str, Any]) -> str:
    return str(col.get("collectionname") or col.get("name") or f"collection-{col.get('id')}").strip()


def open_session(
    *,
    url: str,
    user: str,
    password: str,
    login_collection: str | None = None,
    discipline: str | None = None,
    log_prefix: str = "specli",
) -> tuple[requests.Session, str, int, dict[str, Any] | None]:
    """Open an authenticated session, optionally switched into a discipline.

    Returns ``(session, collection_name, collection_id, discipline_or_none)``.
    """
    session, selected_name, col_id = login(
        url, user, password, login_collection, log_prefix=log_prefix
    )
    if not discipline:
        # Resolve discipline of the login collection for callers that want it.
        collection = get_json(session, url, f"/api/specify/collection/{col_id}/")
        disc_id = resource_pk(collection.get("discipline"))
        disc = None
        if disc_id is not None:
            disc = get_json(session, url, f"/api/specify/discipline/{disc_id}/")
        return session, selected_name, col_id, disc

    disc = resolve_discipline(session, url, discipline)
    disc_id = int(disc["id"])
    cols = collections_for_discipline(session, url, disc_id)
    if not cols:
        sys.exit(
            f"Discipline {discipline_slug(disc)!r} has no collections; "
            "cannot open a Specify session for it"
        )
    target_name = pick_collection_name(cols, preferred_name=login_collection)
    if target_name.lower() != selected_name.lower():
        session, selected_name, col_id = login(
            url,
            user,
            password,
            target_name,
            log_prefix=f"{log_prefix}:{discipline_slug(disc)}",
        )
    return session, selected_name, col_id, disc
