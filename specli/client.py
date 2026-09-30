"""Shared Specify7 HTTP client helpers for specli."""

from __future__ import annotations

import sys
from typing import Any

try:
    import requests
except ImportError:
    sys.exit("requests not found. Install specli dependencies: pip install -e .")


def safe_slug(value: str) -> str:
    out = []
    for ch in value.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_", "."):
            out.append("-")
        else:
            out.append("-")
    collapsed = "".join(out)
    while "--" in collapsed:
        collapsed = collapsed.replace("--", "-")
    return collapsed.strip("-") or "unknown"


def resource_pk(uri: str | None) -> int | None:
    if not uri or not isinstance(uri, str):
        return None
    parts = uri.rstrip("/").split("/")
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return None


def iter_list_endpoint(session: requests.Session, base: str, query: str) -> list[dict[str, Any]]:
    offset = 0
    limit = 300
    out: list[dict[str, Any]] = []
    total: int | None = None
    while True:
        sep = "&" if "?" in query else "?"
        path = f"{query}{sep}limit={limit}&offset={offset}"
        url = f"{base}{path}" if path.startswith("/") else path
        res = session.get(url, timeout=60)
        if not res.ok:
            sys.exit(f"GET {path} failed ({res.status_code}): {res.text[:500]}")
        data = res.json()
        if not isinstance(data, dict):
            break
        objs = data.get("objects") or []
        meta = data.get("meta") or {}
        if total is None and meta.get("total_count") is not None:
            total = int(meta["total_count"])
        out.extend(objs)
        offset += len(objs)
        if not objs:
            break
        if total is not None and offset >= total:
            break
        if len(objs) < limit:
            break
    return out


def get_json(session: requests.Session, base: str, path: str) -> Any:
    url = f"{base}{path}" if path.startswith("/") else path
    res = session.get(url, timeout=60)
    if not res.ok:
        sys.exit(f"GET {path} failed ({res.status_code}): {res.text[:500]}")
    return res.json()


def put_json(
    session: requests.Session,
    base: str,
    path: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    url = f"{base}{path}" if path.startswith("/") else path
    res = session.put(
        url,
        json=body,
        headers={
            "X-CSRFToken": session.cookies.get("csrftoken", ""),
            "Referer": base,
            "Content-Type": "application/json",
        },
        timeout=120,
    )
    if not res.ok:
        sys.exit(f"PUT {path} failed ({res.status_code}): {res.text[:800]}")
    return res.json()


def post_json(
    session: requests.Session,
    base: str,
    path: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    url = f"{base}{path}" if path.startswith("/") else path
    res = session.post(
        url,
        json=body,
        headers={
            "X-CSRFToken": session.cookies.get("csrftoken", ""),
            "Referer": base,
            "Content-Type": "application/json",
        },
        timeout=120,
    )
    if not res.ok:
        sys.exit(f"POST {path} failed ({res.status_code}): {res.text[:800]}")
    return res.json()


def strip_meta_for_put(obj: dict[str, Any]) -> dict[str, Any]:
    skip = frozenset({"resource_uri", "recordset_info", "_tableName"})
    return {k: v for k, v in obj.items() if k not in skip}


def login(
    base: str,
    user: str,
    password: str,
    collection_name: str | None,
    *,
    log_prefix: str,
) -> tuple[requests.Session, str, int]:
    s = requests.Session()
    s.verify = False

    r = s.get(f"{base}/context/login/", timeout=20)
    if r.status_code != 200:
        sys.exit(f"GET /context/login/ failed ({r.status_code}): {r.text[:400]}")

    csrf = s.cookies.get("csrftoken", "")
    data = r.json()
    collections: dict[str, int] = data.get("collections", {})
    if not collections:
        sys.exit("No collections returned by /context/login/")

    if collection_name:
        needle = collection_name.lower()
        by_name = {k.lower(): (k, v) for k, v in collections.items()}
        if needle in by_name:
            selected_name, col_id = by_name[needle]
        else:
            prefix_hits = [(k, v) for kl, (k, v) in by_name.items() if kl.startswith(needle)]
            if len(prefix_hits) == 1:
                selected_name, col_id = prefix_hits[0]
            else:
                available = ", ".join(sorted(collections))
                sys.exit(f"Collection '{collection_name}' not found or ambiguous. Available: {available}")
    else:
        selected_name, col_id = sorted(collections.items(), key=lambda x: x[0])[0]

    auth = s.put(
        f"{base}/context/login/",
        json={"username": user, "password": password, "collection": col_id},
        headers={"X-CSRFToken": csrf, "Referer": base},
        timeout=20,
    )
    if auth.status_code not in (200, 204):
        sys.exit(f"Login failed ({auth.status_code}): {auth.text[:400]}")

    print(f"[{log_prefix}] logged in to {base} as {user}, collection={selected_name}", file=sys.stderr)
    return s, selected_name, col_id


def resolve_specify_user_id(session: requests.Session, base: str, username: str) -> int:
    """Return ``SpecifyUser.id`` for *username* (logged-in user)."""
    needle = username.strip().lower()
    rows = iter_list_endpoint(session, base, "/api/specify/specifyuser/?limit=300")
    for row in rows:
        if (row.get("name") or "").strip().lower() == needle:
            user_id = row.get("id")
            if user_id is not None:
                return int(user_id)
    sys.exit(f"SpecifyUser not found for username={username!r}")


def disable_insecure_request_warnings() -> None:
    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass
