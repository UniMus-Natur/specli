"""Pull Specify disk config (Backstop / Common / discipline FS) via the instance HTTP API.

No access to Specify source or ``SPECIFY_CONFIG_DIR`` is required. The server
exposes the same tree the UI uses:

* ``GET /context/viewsets.json`` — relative ``*.views.xml`` paths under config/
* ``GET /static/config/<path>`` — raw file bytes (Form Editor / App Resources use this)

Directory → scope mapping (mirrors ``get_path_for_level``)::

    backstop/…          → backstop
    common/…            → common (base/)
    <discipline>/…      → by/discipline/<slug>/   (2 path segments, not a usertype)
    <discipline>/<ut>/… → by/usertype/<slug>/<ut>/
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from specli.client import safe_slug
from specli.scopes import (
    APP_RESOURCE_NAMES,
    Scope,
    app_resource_filename,
    viewset_filename,
)

# Known stock app-resource filenames when registry is missing.
_FALLBACK_APP_FILES: dict[str, dict[str, str]] = {
    "backstop": {
        "UIFormatters": "uiformatters.xml",
        "DataObjFormatters": "dataobj_formatters.xml",
    },
    "common": {
        "WebLinks": "weblinks.xml",
    },
}


def list_disk_viewset_paths(session: requests.Session, base: str) -> list[str]:
    res = session.get(f"{base}/context/viewsets.json", timeout=60)
    if res.status_code != 200:
        sys.exit(
            f"GET /context/viewsets.json failed ({res.status_code}): {res.text[:400]}"
        )
    data = res.json()
    if not isinstance(data, list):
        sys.exit("Unexpected /context/viewsets.json payload (expected array)")
    return [str(p).replace("\\", "/") for p in data if str(p).endswith(".views.xml")]


def fetch_static_config(
    session: requests.Session,
    base: str,
    relpath: str,
) -> str | None:
    """Return text of ``/static/config/<relpath>``, or None if missing."""
    rel = relpath.lstrip("/")
    if ".." in rel.split("/"):
        sys.exit(f"Refusing unsafe config path: {relpath!r}")
    url = f"{base}/static/config/{rel}"
    res = session.get(url, timeout=120)
    if res.status_code == 404:
        return None
    if res.status_code != 200:
        print(
            f"[disk] GET /static/config/{rel} failed ({res.status_code})",
            file=sys.stderr,
        )
        return None
    return res.text


def scope_from_config_path(relpath: str) -> Scope | None:
    """Map ``backstop/x.views.xml`` / ``botany/y.views.xml`` / ``fish/manager/z`` → Scope."""
    parts = [p for p in relpath.replace("\\", "/").split("/") if p]
    if len(parts) < 2:
        return None
    top = parts[0].lower()
    if top == "backstop":
        return Scope(level="backstop")
    if top == "common":
        return Scope(level="common")
    if len(parts) == 2:
        # discipline/file.views.xml
        return Scope(level="discipline", discipline_slug=safe_slug(top))
    if len(parts) >= 3:
        # discipline/usertype/file.views.xml
        return Scope(
            level="usertype",
            discipline_slug=safe_slug(top),
            usertype=safe_slug(parts[1]),
        )
    return None


def _viewset_out_name(xml: str, fallback_stem: str) -> str:
    try:
        root = ET.fromstring(xml)
        name = (root.attrib.get("name") or "").strip()
        if name:
            return viewset_filename(name)
    except ET.ParseError:
        pass
    stem = fallback_stem
    if stem.endswith(".views"):
        stem = stem[: -len(".views")]
    return viewset_filename(stem)


def _scope_wanted(scope: Scope, scopes: list[Scope] | None) -> bool:
    if scopes is None:
        return True
    for s in scopes:
        if s.level != scope.level:
            continue
        if scope.level in ("backstop", "common"):
            return True
        if scope.level == "discipline" and s.discipline_slug == scope.discipline_slug:
            return True
        if (
            scope.level == "usertype"
            and s.discipline_slug == scope.discipline_slug
            and s.usertype == scope.usertype
        ):
            return True
    return False


def pull_disk_viewsets(
    *,
    workspace: Path,
    session: requests.Session,
    base: str,
    scopes: list[Scope] | None = None,
) -> int:
    """Download all disk viewsets into the workspace; filter by scopes if given."""
    paths = list_disk_viewset_paths(session, base)
    written = 0
    for rel in paths:
        scope = scope_from_config_path(rel)
        if scope is None or not _scope_wanted(scope, scopes):
            continue
        body = fetch_static_config(session, base, rel)
        if not body:
            continue
        fname = _viewset_out_name(body, Path(rel).stem)
        out = workspace / scope.forms_dir() / fname
        out.parent.mkdir(parents=True, exist_ok=True)
        text = body if body.endswith("\n") else body + "\n"
        out.write_text(text, encoding="utf-8")
        written += 1
        print(
            f"[disk] {scope.label()} ← /static/config/{rel} → {out.relative_to(workspace)}",
            file=sys.stderr,
        )
    return written


def _parse_app_registry(xml: str) -> dict[str, str]:
    """name → filename from app_resources.xml."""
    root = ET.fromstring(xml)
    out: dict[str, str] = {}
    for node in root.findall("file"):
        name = (node.attrib.get("name") or "").strip()
        fname = (node.attrib.get("file") or "").strip()
        if name and fname:
            out[name] = fname
    return out


def _config_dirs_for_scopes(scopes: list[Scope] | None, viewset_paths: list[str]) -> list[str]:
    """Unique config subdirs to probe for app_resources.xml."""
    dirs: set[str] = {"backstop", "common"}
    for rel in viewset_paths:
        parts = [p for p in rel.replace("\\", "/").split("/") if p]
        if len(parts) >= 2:
            dirs.add(parts[0])
        if len(parts) >= 3:
            dirs.add(f"{parts[0]}/{parts[1]}")
    if scopes is None:
        return sorted(dirs)
    filtered: set[str] = set()
    for d in dirs:
        scope = scope_from_config_path(f"{d}/placeholder.views.xml")
        if scope is not None and _scope_wanted(scope, scopes):
            filtered.add(d)
    return sorted(filtered)


def pull_disk_app_resources(
    *,
    workspace: Path,
    session: requests.Session,
    base: str,
    scopes: list[Scope] | None = None,
    names: tuple[str, ...] = APP_RESOURCE_NAMES,
) -> int:
    viewset_paths = list_disk_viewset_paths(session, base)
    written = 0
    for conf_dir in _config_dirs_for_scopes(scopes, viewset_paths):
        # Build a synthetic relative path for scope mapping
        probe = f"{conf_dir}/placeholder.views.xml"
        scope = scope_from_config_path(probe)
        if scope is None:
            continue
        if scopes is not None and not any(
            s.level == scope.level
            and s.discipline_slug == scope.discipline_slug
            and s.usertype == scope.usertype
            for s in scopes
        ):
            continue

        registry_xml = fetch_static_config(session, base, f"{conf_dir}/app_resources.xml")
        mapping: dict[str, str] = {}
        if registry_xml:
            try:
                mapping = _parse_app_registry(registry_xml)
            except ET.ParseError:
                mapping = {}
        if not mapping:
            mapping = dict(_FALLBACK_APP_FILES.get(conf_dir.split("/")[0], {}))

        for name in names:
            fname = mapping.get(name)
            if not fname:
                continue
            body = fetch_static_config(session, base, f"{conf_dir}/{fname}")
            if not body:
                continue
            out = workspace / scope.app_dir() / app_resource_filename(name)
            out.parent.mkdir(parents=True, exist_ok=True)
            text = body if body.endswith("\n") else body + "\n"
            out.write_text(text, encoding="utf-8")
            written += 1
            print(
                f"[disk] {scope.label()} ← /static/config/{conf_dir}/{fname} "
                f"→ {out.relative_to(workspace)}",
                file=sys.stderr,
            )
    return written
