"""Read the exact Specify disk configuration exposed by a live instance."""

from __future__ import annotations

import sys

import requests


def list_disk_viewset_paths(session: requests.Session, base: str) -> list[str]:
    response = session.get(f"{base}/context/viewsets.json", timeout=60)
    if response.status_code != 200:
        sys.exit(
            "GET /context/viewsets.json failed "
            f"({response.status_code}): {response.text[:400]}"
        )
    data = response.json()
    if not isinstance(data, list):
        sys.exit("Unexpected /context/viewsets.json payload (expected array)")
    paths = [
        str(path).replace("\\", "/")
        for path in data
        if str(path).endswith(".views.xml")
    ]
    if not paths:
        sys.exit("GET /context/viewsets.json returned no *.views.xml paths")
    return paths


def _safe_relative_path(relpath: str) -> str:
    relative = relpath.lstrip("/")
    if ".." in relative.split("/"):
        sys.exit(f"Refusing unsafe config path: {relpath!r}")
    return relative


def fetch_static_config(
    session: requests.Session,
    base: str,
    relpath: str,
) -> str:
    """Return exact decoded text; any missing or empty resource is fatal."""
    relative = _safe_relative_path(relpath)
    response = session.get(f"{base}/static/config/{relative}", timeout=120)
    if response.status_code != 200:
        sys.exit(
            f"GET /static/config/{relative} failed "
            f"({response.status_code}): {response.text[:400]}"
        )
    if not response.text.strip():
        sys.exit(f"GET /static/config/{relative} returned empty body")
    return response.text


def try_fetch_static_config(
    session: requests.Session,
    base: str,
    relpath: str,
) -> str | None:
    """Return exact decoded text, allowing only an explicit 404 absence."""
    relative = _safe_relative_path(relpath)
    response = session.get(f"{base}/static/config/{relative}", timeout=120)
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        sys.exit(
            f"GET /static/config/{relative} failed "
            f"({response.status_code}): {response.text[:400]}"
        )
    if not response.text.strip():
        sys.exit(f"GET /static/config/{relative} returned empty body")
    return response.text
