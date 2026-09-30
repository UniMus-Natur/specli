"""Locate / create SpAppResourceDir rows for a Scope via the public API."""

from __future__ import annotations

import sys
from typing import Any

from specli.client import (
    get_json,
    iter_list_endpoint,
    post_json,
    resource_pk,
    safe_slug,
)
from specli.domain import (
    collection_display_name,
    discipline_slug,
    list_collections,
    list_disciplines,
    match_discipline,
)
from specli.scopes import Scope, normalize_usertype


def _uri(table: str, pk: int) -> str:
    return f"/api/specify/{table}/{int(pk)}/"


def list_dirs(session, base: str) -> list[dict[str, Any]]:
    return iter_list_endpoint(session, base, "/api/specify/spappresourcedir/?limit=500")


def find_dirs_for_scope(session, base: str, scope: Scope) -> list[dict[str, Any]]:
    """Return all SpAppResourceDir rows that match this workspace scope."""
    disciplines = {int(d["id"]): d for d in list_disciplines(session, base) if d.get("id") is not None}
    collections = {int(c["id"]): c for c in list_collections(session, base) if c.get("id") is not None}
    users = {
        int(u["id"]): u
        for u in iter_list_endpoint(session, base, "/api/specify/specifyuser/?limit=500")
        if u.get("id") is not None
    }
    matches: list[dict[str, Any]] = []
    for row in list_dirs(session, base):
        is_personal = bool(row.get("ispersonal"))
        usertype = normalize_usertype(row.get("usertype"))
        raw_ut = str(row.get("usertype") or "").strip()
        if raw_ut and usertype is None:
            continue
        disc_id = resource_pk(row.get("discipline"))
        col_id = resource_pk(row.get("collection"))
        user_id = resource_pk(row.get("specifyuser"))
        disc = disciplines.get(disc_id) if disc_id is not None else None
        col = collections.get(col_id) if col_id is not None else None
        user = users.get(user_id) if user_id is not None else None
        d_slug = discipline_slug(disc) if disc else None
        c_slug = safe_slug(collection_display_name(col)) if col else None
        username = safe_slug(str(user.get("name") or "")) if user else None

        if scope.level == "common":
            if (
                not is_personal
                and usertype is None
                and disc_id is None
                and col_id is None
            ):
                matches.append(row)
        elif scope.level == "discipline":
            if (
                not is_personal
                and usertype is None
                and col_id is None
                and d_slug == scope.discipline_slug
            ):
                matches.append(row)
        elif scope.level == "collection":
            if (
                not is_personal
                and usertype is None
                and c_slug == scope.collection_slug
            ):
                matches.append(row)
        elif scope.level == "usertype":
            if (
                not is_personal
                and usertype == scope.usertype
                and d_slug == scope.discipline_slug
            ):
                matches.append(row)
        elif scope.level == "personal":
            if is_personal and username == scope.username:
                matches.append(row)
    return matches


def resolve_scope_ids(session, base: str, scope: Scope) -> Scope:
    """Fill discipline_id / collection_id / specifyuser_id when missing."""
    if scope.level == "common":
        return scope

    disciplines = list_disciplines(session, base)
    collections = list_collections(session, base)

    disc_id = scope.discipline_id
    col_id = scope.collection_id
    user_id = scope.specifyuser_id
    d_slug = scope.discipline_slug
    c_slug = scope.collection_slug

    if scope.level in ("discipline", "usertype") or (
        scope.level == "collection" and d_slug
    ):
        if disc_id is None and d_slug:
            for d in disciplines:
                if discipline_slug(d) == d_slug or match_discipline(d, d_slug):
                    disc_id = int(d["id"])
                    break

    if scope.level == "collection" and col_id is None and c_slug:
        for c in collections:
            if safe_slug(collection_display_name(c)) == c_slug:
                col_id = int(c["id"])
                if disc_id is None:
                    disc_id = resource_pk(c.get("discipline"))
                break
        if col_id is None:
            sys.exit(f"Collection scope {c_slug!r} not found on instance")

    if scope.level == "discipline" and disc_id is None:
        sys.exit(f"Discipline scope {d_slug!r} not found on instance")

    if scope.level == "usertype":
        if disc_id is None:
            sys.exit(f"Discipline {d_slug!r} not found for usertype scope")
        # Need a collection in that discipline for UserType dirs
        if col_id is None:
            for c in collections:
                if resource_pk(c.get("discipline")) == disc_id:
                    col_id = int(c["id"])
                    break
        if col_id is None:
            sys.exit(
                f"No collection in discipline {d_slug!r} to anchor usertype/{scope.usertype}"
            )

    if scope.level == "personal":
        users = iter_list_endpoint(session, base, "/api/specify/specifyuser/?limit=500")
        if user_id is None and scope.username:
            for u in users:
                if safe_slug(str(u.get("name") or "")) == scope.username:
                    user_id = int(u["id"])
                    break
        if user_id is None:
            sys.exit(f"User {scope.username!r} not found on instance")
        if col_id is None:
            # Prefer login collection; else first collection
            if collections:
                col_id = int(collections[0]["id"])
                disc_id = resource_pk(collections[0].get("discipline"))
        if disc_id is None and col_id is not None:
            col = get_json(session, base, f"/api/specify/collection/{col_id}/")
            disc_id = resource_pk(col.get("discipline"))

    return Scope(
        level=scope.level,
        discipline_slug=scope.discipline_slug,
        collection_slug=scope.collection_slug,
        usertype=scope.usertype,
        username=scope.username,
        discipline_id=disc_id,
        collection_id=col_id,
        specifyuser_id=user_id,
        dir_id=scope.dir_id,
    )


def ensure_dir(session, base: str, scope: Scope) -> tuple[int, bool]:
    """Return (dir_id, created). Creates SpAppResourceDir when missing."""
    if scope.is_filesystem_only():
        sys.exit(
            f"Cannot create SpAppResourceDir for {scope.label()} — "
            "Backstop is Specify disk stock. Edit under backstop/ in git, then "
            "copy/promote into base/ (Common) or a by/* scope and push that."
        )
    existing = find_dirs_for_scope(session, base, scope)
    if existing:
        dir_id = existing[0].get("id")
        if dir_id is None:
            dir_id = resource_pk(existing[0].get("resource_uri"))
        if dir_id is None:
            sys.exit(f"Could not parse SpAppResourceDir id for {scope.label()}")
        return int(dir_id), False

    resolved = resolve_scope_ids(session, base, scope)
    body: dict[str, Any] = {
        "ispersonal": resolved.level == "personal",
        "usertype": None,
        "disciplinetype": None,
        "discipline": None,
        "collection": None,
        "specifyuser": None,
    }

    if resolved.level == "common":
        pass
    elif resolved.level == "discipline":
        body["discipline"] = _uri("discipline", int(resolved.discipline_id))
        disc = get_json(session, base, f"/api/specify/discipline/{resolved.discipline_id}/")
        body["disciplinetype"] = disc.get("name") or resolved.discipline_slug
    elif resolved.level == "collection":
        body["collection"] = _uri("collection", int(resolved.collection_id))
        body["discipline"] = _uri("discipline", int(resolved.discipline_id))
        disc = get_json(session, base, f"/api/specify/discipline/{resolved.discipline_id}/")
        body["disciplinetype"] = disc.get("name") or resolved.discipline_slug
    elif resolved.level == "usertype":
        body["usertype"] = resolved.usertype
        body["collection"] = _uri("collection", int(resolved.collection_id))
        body["discipline"] = _uri("discipline", int(resolved.discipline_id))
        disc = get_json(session, base, f"/api/specify/discipline/{resolved.discipline_id}/")
        body["disciplinetype"] = disc.get("name") or resolved.discipline_slug
    elif resolved.level == "personal":
        body["ispersonal"] = True
        body["specifyuser"] = _uri("specifyuser", int(resolved.specifyuser_id))
        body["collection"] = _uri("collection", int(resolved.collection_id))
        body["discipline"] = _uri("discipline", int(resolved.discipline_id))
        disc = get_json(session, base, f"/api/specify/discipline/{resolved.discipline_id}/")
        body["disciplinetype"] = disc.get("name") or resolved.discipline_slug

    created = post_json(session, base, "/api/specify/spappresourcedir/", body)
    dir_id = created.get("id") or resource_pk(created.get("resource_uri"))
    if dir_id is None:
        sys.exit(f"Failed to create SpAppResourceDir for {scope.label()}")
    return int(dir_id), True
