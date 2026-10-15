"""Specify viewset graph compiler for the specli/v3 form-family format.

The public functions are deliberately filesystem-independent.  Pull/push code
uses these pure operations, while repository_v3 owns paths and manifests.
"""

from __future__ import annotations

import copy
import hashlib
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable, Literal
from xml.etree import ElementTree as ET

from specli.client import safe_slug

XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>\n'
_UNSAFE_XML = re.compile(r"<!DOCTYPE|<!ENTITY|<xi:include\b", re.IGNORECASE)


def _exit(message: str) -> "None":
    sys.exit(message)


def parse_xml(text: str, *, where: str) -> ET.Element:
    """Parse XML while retaining comments/PIs and rejecting active content."""
    if not text.strip():
        _exit(f"{where}: empty XML")
    if _UNSAFE_XML.search(text):
        _exit(f"{where}: DTD, entity declarations, and XInclude are forbidden")
    try:
        parser = ET.XMLParser(
            target=ET.TreeBuilder(insert_comments=True, insert_pis=True)
        )
        return ET.fromstring(text, parser=parser)
    except ET.ParseError as exc:
        _exit(f"{where}: invalid XML: {exc}")


def xml_text(root: ET.Element) -> str:
    clone = copy.deepcopy(root)
    ET.indent(clone, space="  ")
    body = ET.tostring(clone, encoding="unicode", short_empty_elements=True)
    return XML_DECLARATION + body + ("" if body.endswith("\n") else "\n")


def canonical_xml(text: str, *, with_comments: bool = True) -> str:
    """Return the C14N identity used for comparison and locks."""
    root = parse_xml(text, where="canonical XML")
    _remove_formatting_whitespace(root)
    raw = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return ET.canonicalize(
        xml_data=raw,
        with_comments=with_comments,
        strip_text=False,
        rewrite_prefixes=False,
    )


def _remove_formatting_whitespace(element: ET.Element) -> None:
    """Discard indentation while retaining non-whitespace text verbatim."""
    if element.text is not None and not element.text.strip():
        element.text = None
    if element.tail is not None and not element.tail.strip():
        element.tail = None
    for child in list(element):
        _remove_formatting_whitespace(child)


def canonical_sha256(text: str) -> str:
    digest = hashlib.sha256(canonical_xml(text).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass
class XmlObject:
    kind: Literal["view", "viewdef"]
    name: str
    element: ET.Element
    order: int
    before: list[ET.Element] = field(default_factory=list)
    after: list[ET.Element] = field(default_factory=list)

    def clone(self) -> "XmlObject":
        return XmlObject(
            kind=self.kind,
            name=self.name,
            element=copy.deepcopy(self.element),
            order=self.order,
            before=copy.deepcopy(self.before),
            after=copy.deepcopy(self.after),
        )


@dataclass
class FormFamily:
    table: str
    name: str
    views: list[XmlObject]
    viewdefs: list[XmlObject]

    @property
    def object_count(self) -> int:
        return len(self.views) + len(self.viewdefs)


@dataclass
class RootMisc:
    before_views: list[ET.Element] = field(default_factory=list)
    between_sections: list[ET.Element] = field(default_factory=list)
    after_viewdefs: list[ET.Element] = field(default_factory=list)


@dataclass
class SplitViewset:
    root_attributes: dict[str, str]
    families: list[FormFamily]
    orphan_viewdefs: list[XmlObject]
    unresolved_viewdefs: list[str]
    view_order: list[str]
    viewdef_order: list[str]
    root_misc: RootMisc = field(default_factory=RootMisc)


def _is_misc(element: ET.Element) -> bool:
    return not isinstance(element.tag, str)


def _objects(container: ET.Element, kind: Literal["view", "viewdef"]) -> list[XmlObject]:
    out: list[XmlObject] = []
    pending: list[ET.Element] = []
    for child in list(container):
        if _is_misc(child):
            pending.append(copy.deepcopy(child))
            continue
        if child.tag != kind:
            _exit(
                f"<{container.tag}> contains <{child.tag}>; expected only "
                f"<{kind}> plus comments/processing instructions"
            )
        name = (child.attrib.get("name") or "").strip()
        if not name:
            _exit(f"<{kind}> is missing a non-empty name attribute")
        out.append(
            XmlObject(
                kind=kind,
                name=name,
                element=copy.deepcopy(child),
                order=len(out),
                before=pending,
            )
        )
        pending = []
    if pending:
        if out:
            out[-1].after.extend(pending)
        else:
            _exit(f"<{container.tag}> contains only comments and no <{kind}>")
    names = [item.name for item in out]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        _exit(f"duplicate <{kind}> names: {duplicates}")
    return out


def _root_sections(root: ET.Element) -> tuple[ET.Element, ET.Element, RootMisc]:
    views: ET.Element | None = None
    viewdefs: ET.Element | None = None
    misc = RootMisc()
    phase = "before"
    for child in list(root):
        if _is_misc(child):
            target = (
                misc.before_views
                if phase == "before"
                else misc.between_sections
                if phase == "between"
                else misc.after_viewdefs
            )
            target.append(copy.deepcopy(child))
            continue
        if child.tag == "views" and views is None and phase == "before":
            views = child
            phase = "between"
            continue
        if child.tag == "viewdefs" and viewdefs is None and phase == "between":
            viewdefs = child
            phase = "after"
            continue
        _exit(
            "viewset root must contain exactly <views> then <viewdefs>; "
            f"unexpected <{child.tag}>"
        )
    if views is None or viewdefs is None:
        _exit("viewset root requires exactly one <views> and one <viewdefs>")
    return views, viewdefs, misc


def _view_refs(view: XmlObject) -> set[str]:
    refs: set[str] = set()
    for altview in view.element.findall("./altviews/altview"):
        name = (altview.attrib.get("viewdef") or "").strip()
        if not name:
            _exit(f"view {view.name!r}: <altview> has no viewdef")
        refs.add(name)
    return refs


def _definition_ref(viewdef: XmlObject) -> str | None:
    node = viewdef.element.find("definition")
    if node is None:
        return None
    value = (node.text or "").strip()
    return value or None


def _table_name(view: XmlObject) -> str:
    class_name = (view.element.attrib.get("class") or "").strip()
    if not class_name:
        _exit(f"view {view.name!r}: missing class attribute")
    return safe_slug(class_name.rsplit(".", 1)[-1])


def _family_name(
    views: list[XmlObject],
    viewdefs: list[XmlObject],
    reference_counts: dict[str, int],
) -> str:
    if viewdefs:
        winner = sorted(
            viewdefs,
            key=lambda item: (-reference_counts.get(item.name, 0), item.order, item.name),
        )[0]
        return safe_slug(winner.name)
    return safe_slug(views[0].name)


def split_viewset(xml: str, *, where: str = "viewset") -> SplitViewset:
    """Split a complete viewset into self-contained connected families."""
    root = parse_xml(xml, where=where)
    if root.tag != "viewset":
        _exit(f"{where}: expected <viewset>, got <{root.tag}>")
    if not (root.attrib.get("name") or "").strip():
        _exit(f"{where}: <viewset> has no name")

    views_node, viewdefs_node, root_misc = _root_sections(root)
    views = _objects(views_node, "view")
    viewdefs = _objects(viewdefs_node, "viewdef")
    if not views:
        _exit(f"{where}: <views> is empty")

    view_by_name = {item.name: item for item in views}
    def_by_name = {item.name: item for item in viewdefs}
    node_keys = {("view", name) for name in view_by_name}
    node_keys.update(("viewdef", name) for name in def_by_name)
    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = {
        key: set() for key in node_keys
    }
    unresolved: set[str] = set()
    reference_counts: dict[str, int] = {}

    for view in views:
        for target in _view_refs(view):
            reference_counts[target] = reference_counts.get(target, 0) + 1
            if target not in def_by_name:
                unresolved.add(target)
                continue
            left = ("view", view.name)
            right = ("viewdef", target)
            adjacency[left].add(right)
            adjacency[right].add(left)

    for viewdef in viewdefs:
        target = _definition_ref(viewdef)
        if target is None:
            continue
        reference_counts[target] = reference_counts.get(target, 0) + 1
        if target not in def_by_name:
            unresolved.add(target)
            continue
        left = ("viewdef", viewdef.name)
        right = ("viewdef", target)
        adjacency[left].add(right)
        adjacency[right].add(left)

    # Components rooted in views are editable form families. Viewdefs not
    # connected to any view are explicit orphans, never a synthetic family.
    remaining = set(node_keys)
    components: list[set[tuple[str, str]]] = []
    while remaining:
        start = min(remaining)
        stack = [start]
        component = {start}
        remaining.remove(start)
        while stack:
            current = stack.pop()
            for neighbor in adjacency[current]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    stack.append(neighbor)
        components.append(component)

    families: list[FormFamily] = []
    orphan_names: set[str] = set()
    used_paths: set[tuple[str, str]] = set()
    for component in components:
        component_views = sorted(
            (view_by_name[name] for kind, name in component if kind == "view"),
            key=lambda item: item.order,
        )
        component_defs = sorted(
            (def_by_name[name] for kind, name in component if kind == "viewdef"),
            key=lambda item: item.order,
        )
        if not component_views:
            orphan_names.update(item.name for item in component_defs)
            continue
        table = _table_name(component_views[0])
        family_name = _family_name(component_views, component_defs, reference_counts)
        path_key = (table.casefold(), family_name.casefold())
        if path_key in used_paths:
            suffix_source = "\0".join(
                [item.name for item in component_views + component_defs]
            )
            suffix = hashlib.sha256(suffix_source.encode("utf-8")).hexdigest()[:8]
            family_name = f"{family_name}--{suffix}"
            path_key = (table.casefold(), family_name.casefold())
        used_paths.add(path_key)
        families.append(
            FormFamily(
                table=table,
                name=family_name,
                views=[item.clone() for item in component_views],
                viewdefs=[item.clone() for item in component_defs],
            )
        )

    families.sort(key=lambda family: min(item.order for item in family.views))
    orphans = [
        item.clone() for item in viewdefs if item.name in orphan_names
    ]
    return SplitViewset(
        root_attributes=dict(root.attrib),
        families=families,
        orphan_viewdefs=orphans,
        unresolved_viewdefs=sorted(unresolved),
        view_order=[item.name for item in views],
        viewdef_order=[item.name for item in viewdefs],
        root_misc=root_misc,
    )


def _append_object(container: ET.Element, item: XmlObject) -> None:
    for decoration in item.before:
        container.append(copy.deepcopy(decoration))
    container.append(copy.deepcopy(item.element))
    for decoration in item.after:
        container.append(copy.deepcopy(decoration))


def family_xml(family: FormFamily, root_attributes: dict[str, str]) -> str:
    root = ET.Element("viewset", dict(root_attributes))
    views = ET.SubElement(root, "views")
    for item in family.views:
        _append_object(views, item)
    viewdefs = ET.SubElement(root, "viewdefs")
    for item in family.viewdefs:
        _append_object(viewdefs, item)
    return xml_text(root)


def orphan_xml(
    orphan_viewdefs: Iterable[XmlObject],
    root_attributes: dict[str, str],
) -> str | None:
    items = list(orphan_viewdefs)
    if not items:
        return None
    root = ET.Element("viewset", dict(root_attributes))
    ET.SubElement(root, "views")
    viewdefs = ET.SubElement(root, "viewdefs")
    for item in items:
        _append_object(viewdefs, item)
    return xml_text(root)


def root_misc_xml(misc: RootMisc) -> str | None:
    if not (misc.before_views or misc.between_sections or misc.after_viewdefs):
        return None
    root = ET.Element("viewset-misc")
    for name, items in (
        ("before-views", misc.before_views),
        ("between-sections", misc.between_sections),
        ("after-viewdefs", misc.after_viewdefs),
    ):
        node = ET.SubElement(root, name)
        for item in items:
            node.append(copy.deepcopy(item))
    return xml_text(root)


def parse_root_misc(xml: str | None) -> RootMisc:
    if xml is None:
        return RootMisc()
    root = parse_xml(xml, where="viewset misc")
    if root.tag != "viewset-misc":
        _exit(f"viewset misc: expected <viewset-misc>, got <{root.tag}>")

    def children(name: str) -> list[ET.Element]:
        node = root.find(name)
        if node is None:
            _exit(f"viewset misc: missing <{name}>")
        if any(not _is_misc(child) for child in list(node)):
            _exit(f"viewset misc: <{name}> may contain only comments/PIs")
        return copy.deepcopy(list(node))

    return RootMisc(
        before_views=children("before-views"),
        between_sections=children("between-sections"),
        after_viewdefs=children("after-viewdefs"),
    )


def _load_fragment(
    xml: str,
    *,
    where: str,
    expected_root_attributes: dict[str, str],
) -> tuple[list[XmlObject], list[XmlObject]]:
    root = parse_xml(xml, where=where)
    if root.tag != "viewset":
        _exit(f"{where}: expected <viewset>, got <{root.tag}>")
    if dict(root.attrib) != expected_root_attributes:
        _exit(
            f"{where}: root attributes differ from manifest: "
            f"{dict(root.attrib)!r} != {expected_root_attributes!r}"
        )
    views_node, viewdefs_node, misc = _root_sections(root)
    if misc.before_views or misc.between_sections or misc.after_viewdefs:
        _exit(f"{where}: root-level comments/PIs belong in viewset-misc.xml")
    return _objects(views_node, "view"), _objects(viewdefs_node, "viewdef")


def assemble_fragments(
    *,
    root_attributes: dict[str, str],
    family_fragments: Iterable[tuple[str, str]],
    view_order: list[str],
    viewdef_order: list[str],
    orphans: str | None = None,
    root_misc: str | None = None,
) -> str:
    """Assemble repository fragments into one complete viewset document."""
    views: dict[str, XmlObject] = {}
    viewdefs: dict[str, XmlObject] = {}

    def add(item: XmlObject, *, where: str) -> None:
        target = views if item.kind == "view" else viewdefs
        if item.name in target:
            _exit(f"{where}: duplicate {item.kind} {item.name!r}")
        target[item.name] = item

    for where, xml in family_fragments:
        fragment_views, fragment_defs = _load_fragment(
            xml,
            where=where,
            expected_root_attributes=root_attributes,
        )
        if not fragment_views:
            _exit(f"{where}: a form family must contain at least one <view>")
        for item in fragment_views + fragment_defs:
            add(item, where=where)

    if orphans is not None:
        orphan_views, orphan_defs = _load_fragment(
            orphans,
            where="orphans.xml",
            expected_root_attributes=root_attributes,
        )
        if orphan_views:
            _exit("orphans.xml must not contain views")
        for item in orphan_defs:
            add(item, where="orphans.xml")

    if set(view_order) != set(views):
        _exit(
            "manifest view order does not match fragments: "
            f"missing={sorted(set(view_order) - set(views))}, "
            f"extra={sorted(set(views) - set(view_order))}"
        )
    if set(viewdef_order) != set(viewdefs):
        _exit(
            "manifest viewdef order does not match fragments: "
            f"missing={sorted(set(viewdef_order) - set(viewdefs))}, "
            f"extra={sorted(set(viewdefs) - set(viewdef_order))}"
        )

    misc = parse_root_misc(root_misc)
    root = ET.Element("viewset", dict(root_attributes))
    for item in misc.before_views:
        root.append(copy.deepcopy(item))
    views_node = ET.SubElement(root, "views")
    for name in view_order:
        _append_object(views_node, views[name])
    for item in misc.between_sections:
        root.append(copy.deepcopy(item))
    viewdefs_node = ET.SubElement(root, "viewdefs")
    for name in viewdef_order:
        _append_object(viewdefs_node, viewdefs[name])
    for item in misc.after_viewdefs:
        root.append(copy.deepcopy(item))
    return xml_text(root)


def validate_reference_graph(xml: str, *, where: str) -> list[str]:
    """Return unresolved names after rejecting duplicate/cyclic structures."""
    split = split_viewset(xml, where=where)
    root = parse_xml(xml, where=where)
    _, viewdefs_node, _ = _root_sections(root)
    definitions = {
        (item.attrib.get("name") or "").strip():
        ((item.findtext("definition") or "").strip() or None)
        for item in list(viewdefs_node)
        if isinstance(item.tag, str)
    }
    for start in definitions:
        seen: set[str] = set()
        current: str | None = start
        while current is not None and current in definitions:
            if current in seen:
                _exit(f"{where}: cyclic <definition> chain at {current!r}")
            seen.add(current)
            current = definitions[current]
    return split.unresolved_viewdefs
