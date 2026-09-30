"""Scope-first layout helpers — mirrors Specify DIR_LEVELS in the workspace.

Specify resolution order (most specific wins)::

    Personal → UserType → Collection → Discipline → Common → Backstop

specli maps those onto::

    backstop/                                  # stock disk baseline
    base/                                      # Common (DB + optional disk seed)
    by/discipline/<slug>/
    by/collection/<slug>/
    by/usertype/<discipline>/<usertype>/
    by/user/<username>/                       # Personal (opt-in)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from specli.client import iter_list_endpoint, resource_pk, safe_slug
from specli.domain import (
    collection_display_name,
    discipline_slug,
    list_collections,
    list_disciplines,
)

Level = Literal["backstop", "common", "discipline", "collection", "usertype", "personal"]

DEFAULT_LEVELS: tuple[Level, ...] = (
    "backstop",
    "common",
    "discipline",
    "collection",
    "usertype",
)

# SpAppResourceDir.usertype values that are prefs/system, not form hierarchy.
_SKIP_USERTYPES = frozenset(
    {
        "prefs",
        "globalprefs",
        "global prefs",
        "globprefs",
    }
)

APP_RESOURCE_NAMES = ("UIFormatters", "DataObjFormatters", "WebLinks")


@dataclass(frozen=True)
class Scope:
    """One Specify hierarchy level + identity keys for the workspace path."""

    level: Level
    discipline_slug: str | None = None
    collection_slug: str | None = None
    usertype: str | None = None
    username: str | None = None
    # Optional resolved API ids (filled when discovered from an instance)
    discipline_id: int | None = None
    collection_id: int | None = None
    specifyuser_id: int | None = None
    dir_id: int | None = None

    def label(self) -> str:
        if self.level == "backstop":
            return "backstop"
        if self.level == "common":
            return "common"
        if self.level == "discipline":
            return f"discipline/{self.discipline_slug}"
        if self.level == "collection":
            return f"collection/{self.collection_slug}"
        if self.level == "usertype":
            return f"usertype/{self.discipline_slug}/{self.usertype}"
        if self.level == "personal":
            return f"user/{self.username}"
        return self.level

    def relpath(self) -> Path:
        if self.level == "backstop":
            return Path("backstop")
        if self.level == "common":
            return Path("base")
        if self.level == "discipline":
            if not self.discipline_slug:
                raise ValueError("discipline scope requires discipline_slug")
            return Path("by") / "discipline" / self.discipline_slug
        if self.level == "collection":
            if not self.collection_slug:
                raise ValueError("collection scope requires collection_slug")
            return Path("by") / "collection" / self.collection_slug
        if self.level == "usertype":
            if not self.discipline_slug or not self.usertype:
                raise ValueError("usertype scope requires discipline_slug and usertype")
            return Path("by") / "usertype" / self.discipline_slug / self.usertype
        if self.level == "personal":
            if not self.username:
                raise ValueError("personal scope requires username")
            return Path("by") / "user" / self.username
        raise ValueError(f"unknown level: {self.level}")

    def forms_dir(self) -> Path:
        return self.relpath() / "forms"

    def app_dir(self) -> Path:
        return self.relpath() / "app"

    def schema_dir(self) -> Path:
        if self.level != "discipline":
            raise ValueError("schema only lives under discipline scopes")
        return self.relpath() / "schema"

    def is_filesystem_only(self) -> bool:
        """Backstop is Specify disk stock — not an SpAppResourceDir in the DB."""
        return self.level == "backstop"


def normalize_usertype(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    collapsed = text.replace(" ", "").lower()
    if collapsed in _SKIP_USERTYPES or text.lower() in _SKIP_USERTYPES:
        return None
    # Keep a stable filesystem slug; preserve readable form via safe_slug.
    return safe_slug(text)


def parse_scope_ref(ref: str) -> Scope:
    """Parse ``backstop``, ``common``, ``discipline/botany``, …"""
    raw = (ref or "").strip().strip("/")
    if not raw:
        sys.exit("Empty --scope value")
    parts = raw.split("/")
    head = parts[0].lower()
    if head == "backstop" and len(parts) == 1:
        return Scope(level="backstop")
    if head in ("common", "base") and len(parts) == 1:
        return Scope(level="common")
    if head == "discipline" and len(parts) == 2:
        return Scope(level="discipline", discipline_slug=safe_slug(parts[1]))
    if head == "collection" and len(parts) == 2:
        return Scope(level="collection", collection_slug=safe_slug(parts[1]))
    if head == "usertype" and len(parts) == 3:
        return Scope(
            level="usertype",
            discipline_slug=safe_slug(parts[1]),
            usertype=normalize_usertype(parts[2]) or safe_slug(parts[2]),
        )
    if head in ("user", "personal") and len(parts) == 2:
        return Scope(level="personal", username=safe_slug(parts[1]))
    sys.exit(
        f"Invalid --scope {ref!r}. Expected: backstop | common | discipline/<slug> | "
        "collection/<slug> | usertype/<discipline>/<usertype> | user/<username>"
    )


def classify_dir_row(
    row: dict[str, Any],
    *,
    discipline_by_id: dict[int, dict[str, Any]],
    collection_by_id: dict[int, dict[str, Any]],
    user_by_id: dict[int, dict[str, Any]],
) -> Scope | None:
    """Map one SpAppResourceDir API row to a Scope, or None if not syncable."""
    is_personal = bool(row.get("ispersonal"))
    usertype = normalize_usertype(row.get("usertype"))
    # Raw usertype may be Prefs — skip those dirs entirely.
    raw_ut = str(row.get("usertype") or "").strip()
    if raw_ut and normalize_usertype(raw_ut) is None:
        return None

    disc_id = resource_pk(row.get("discipline"))
    col_id = resource_pk(row.get("collection"))
    user_id = resource_pk(row.get("specifyuser"))
    dir_id = int(row["id"]) if row.get("id") is not None else resource_pk(row.get("resource_uri"))

    disc = discipline_by_id.get(disc_id) if disc_id is not None else None
    col = collection_by_id.get(col_id) if col_id is not None else None
    user = user_by_id.get(user_id) if user_id is not None else None
    d_slug = discipline_slug(disc) if disc else None
    c_slug = safe_slug(collection_display_name(col)) if col else None
    username = safe_slug(str(user.get("name") or f"user-{user_id}")) if user else None

    if is_personal:
        if not username:
            return None
        return Scope(
            level="personal",
            username=username,
            discipline_slug=d_slug,
            collection_slug=c_slug,
            discipline_id=disc_id,
            collection_id=col_id,
            specifyuser_id=user_id,
            dir_id=dir_id,
        )

    if usertype:
        if not d_slug:
            return None
        return Scope(
            level="usertype",
            discipline_slug=d_slug,
            usertype=usertype,
            collection_slug=c_slug,
            discipline_id=disc_id,
            collection_id=col_id,
            dir_id=dir_id,
        )

    if col_id is not None:
        if not c_slug:
            return None
        return Scope(
            level="collection",
            collection_slug=c_slug,
            discipline_slug=d_slug,
            discipline_id=disc_id,
            collection_id=col_id,
            dir_id=dir_id,
        )

    if disc_id is not None:
        if not d_slug:
            return None
        return Scope(
            level="discipline",
            discipline_slug=d_slug,
            discipline_id=disc_id,
            dir_id=dir_id,
        )

    # Common: no discipline, no collection, not personal, no usertype
    return Scope(level="common", dir_id=dir_id)


def _scope_dedupe_key(scope: Scope) -> tuple:
    """Identity for workspace path (one scope path may map to multiple DB dirs)."""
    if scope.level == "backstop":
        return ("backstop",)
    if scope.level == "common":
        return ("common",)
    if scope.level == "discipline":
        return ("discipline", scope.discipline_slug)
    if scope.level == "collection":
        return ("collection", scope.collection_slug)
    if scope.level == "usertype":
        return ("usertype", scope.discipline_slug, scope.usertype)
    if scope.level == "personal":
        return ("personal", scope.username)
    return (scope.level,)


def discover_scopes(
    session,
    base: str,
    *,
    include_personal: bool = False,
    levels: Iterable[Level] | None = None,
    discipline_filter: str | None = None,
) -> list[Scope]:
    """Discover scopes from SpAppResourceDir rows on the instance.

    Always includes a synthetic Common scope even if no Common dir exists yet
    (push may create it). Discipline scopes are ensured for every discipline
    so schema pull has a home.
    """
    wanted = set(levels or DEFAULT_LEVELS)
    if include_personal:
        wanted.add("personal")

    disciplines = list_disciplines(session, base)
    collections = list_collections(session, base)
    users = iter_list_endpoint(session, base, "/api/specify/specifyuser/?limit=500")
    dirs = iter_list_endpoint(session, base, "/api/specify/spappresourcedir/?limit=500")

    discipline_by_id = {int(d["id"]): d for d in disciplines if d.get("id") is not None}
    collection_by_id = {int(c["id"]): c for c in collections if c.get("id") is not None}
    user_by_id = {int(u["id"]): u for u in users if u.get("id") is not None}

    by_key: dict[tuple, Scope] = {}

    if "backstop" in wanted:
        by_key[("backstop",)] = Scope(level="backstop")

    if "common" in wanted:
        by_key[("common",)] = Scope(level="common")

    if "discipline" in wanted:
        for disc in disciplines:
            if discipline_filter and not _disc_matches(disc, discipline_filter):
                continue
            slug = discipline_slug(disc)
            by_key[("discipline", slug)] = Scope(
                level="discipline",
                discipline_slug=slug,
                discipline_id=int(disc["id"]),
            )

    for row in dirs:
        scope = classify_dir_row(
            row,
            discipline_by_id=discipline_by_id,
            collection_by_id=collection_by_id,
            user_by_id=user_by_id,
        )
        if scope is None or scope.level not in wanted:
            continue
        if discipline_filter and scope.level not in ("common", "backstop"):
            if scope.discipline_id is not None:
                disc = discipline_by_id.get(scope.discipline_id)
                if disc is None or not _disc_matches(disc, discipline_filter):
                    continue
            elif scope.discipline_slug:
                if scope.discipline_slug != safe_slug(discipline_filter):
                    continue
            else:
                continue
        key = _scope_dedupe_key(scope)
        existing = by_key.get(key)
        if existing is None or (existing.dir_id is None and scope.dir_id is not None):
            by_key[key] = scope

    scopes = list(by_key.values())
    scopes.sort(key=lambda s: (s.level, s.label()))
    return scopes


def _disc_matches(disc: dict[str, Any], needle: str) -> bool:
    from specli.domain import match_discipline

    return match_discipline(disc, needle)


def filter_scopes(
    scopes: list[Scope],
    *,
    scope_refs: list[str] | None = None,
    discipline_filter: str | None = None,
    include_personal: bool = False,
) -> list[Scope]:
    """Filter discovered scopes by CLI --scope / --discipline / personal flag."""
    out = list(scopes)
    if not include_personal:
        out = [s for s in out if s.level != "personal"]

    if discipline_filter:
        needle = safe_slug(discipline_filter)
        raw = discipline_filter.strip().lower()

        def _match(s: Scope) -> bool:
            if s.level in ("common", "backstop"):
                return True
            if not s.discipline_slug:
                return False
            return s.discipline_slug == needle or s.discipline_slug == raw

        out = [s for s in out if _match(s)]

    if scope_refs:
        wanted = {_scope_dedupe_key(parse_scope_ref(r)) for r in scope_refs}
        matched = [s for s in out if _scope_dedupe_key(s) in wanted]
        have = {_scope_dedupe_key(s) for s in matched}
        for ref in scope_refs:
            parsed = parse_scope_ref(ref)
            key = _scope_dedupe_key(parsed)
            if key not in have:
                if parsed.level == "personal" and not include_personal:
                    sys.exit("Personal scope requires --include-personal")
                matched.append(parsed)
                have.add(key)
        out = matched
    return out


def scopes_from_workspace(
    workspace: Path,
    *,
    include_personal: bool = False,
    scope_refs: list[str] | None = None,
) -> list[Scope]:
    """Discover scopes that already have directories on disk (for push)."""
    found: list[Scope] = []
    if (workspace / "backstop").is_dir():
        found.append(Scope(level="backstop"))
    base = workspace / "base"
    if base.is_dir():
        found.append(Scope(level="common"))

    disc_root = workspace / "by" / "discipline"
    if disc_root.is_dir():
        for p in sorted(disc_root.iterdir()):
            if p.is_dir():
                found.append(Scope(level="discipline", discipline_slug=p.name))

    col_root = workspace / "by" / "collection"
    if col_root.is_dir():
        for p in sorted(col_root.iterdir()):
            if p.is_dir():
                found.append(Scope(level="collection", collection_slug=p.name))

    ut_root = workspace / "by" / "usertype"
    if ut_root.is_dir():
        for disc_p in sorted(ut_root.iterdir()):
            if not disc_p.is_dir():
                continue
            for ut_p in sorted(disc_p.iterdir()):
                if ut_p.is_dir():
                    found.append(
                        Scope(
                            level="usertype",
                            discipline_slug=disc_p.name,
                            usertype=ut_p.name,
                        )
                    )

    user_root = workspace / "by" / "user"
    if include_personal and user_root.is_dir():
        for p in sorted(user_root.iterdir()):
            if p.is_dir():
                found.append(Scope(level="personal", username=p.name))

    if scope_refs:
        return filter_scopes(found, scope_refs=scope_refs, include_personal=include_personal)
    if not include_personal:
        found = [s for s in found if s.level != "personal"]
    return found


def viewset_filename(name: str) -> str:
    slug = safe_slug(name)
    return f"{slug}.views.xml"


def app_resource_filename(name: str) -> str:
    return f"{name}.xml"
