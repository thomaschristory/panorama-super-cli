"""Apply a `ChangeSet` to a Panorama config XML document, offline.

This is what makes `--apply` real without a live device: feed the original
config and a plan, get back a rewritten config you can `load config partial`
into Panorama. It edits the same XML the parser read, so the round-trip is
faithful — only the planned nodes change.

Only the operation kinds `psc` v0.1 generates are implemented (reference
rewrites, deletes, renames, upserts of address/service objects). Anything the
renderer flagged as `# REVIEW` (e.g. NAT translation paths) is *not* silently
mutated here — the plan's `warnings` already told the operator to hand-check it.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from defusedxml.ElementTree import fromstring as _safe_fromstring

from psc.core.changeset import (
    ChangeSet,
    ObjectUpsert,
    ReferenceEdit,
    RuleDelete,
    reference_edit_is_mappable,
)
from psc.core.rulebases import rule_container
from psc.output.errors import ErrorType, PscError


def _find_named(container: ET.Element | None, tag: str, name: str) -> ET.Element | None:
    """Find a `<tag name="name">` child by iterating — never via an XPath
    `[@name='...']` predicate, which breaks (crash or silent no-op) when a name
    contains a quote/bracket. A silent no-op mid-apply would skip a reference
    rewrite while the delete still runs, so this is a safety fix, not cosmetics.
    """
    if container is None:
        return None
    for el in container.findall(tag):
        if el.get("name") == name:
            return el
    return None


def _scope_element(root: ET.Element, location: str) -> ET.Element | None:
    if location == "shared":
        return root.find("shared")
    # Prefer the editable device-group tree; never write into the read-only
    # hierarchy mirror under `/config/readonly`, which carries the same names.
    editable = root.findall("./devices/entry/device-group/entry")
    candidates = editable or [
        e
        for e in root.findall(".//device-group/entry")
        if e.get("name") and root.find("readonly") is None
    ]
    for dg in candidates:
        if dg.get("name") == location:
            return dg
    return None


def _set_members(field_el: ET.Element, members: list[str]) -> None:
    for child in list(field_el):
        field_el.remove(child)
    for m in members:
        ET.SubElement(field_el, "member").text = m


def _referrer_field_element(scope: ET.Element, edit: ReferenceEdit) -> ET.Element | None:
    name = edit.referrer_name
    # `edit.rulebase` is a validated enum value ("pre"/"post"), safe to format.
    if edit.referrer_kind == "address-group":
        entry = _find_named(scope.find("address-group"), "entry", name)
        leaf = "static"
    elif edit.referrer_kind == "service-group":
        entry = _find_named(scope.find("service-group"), "entry", name)
        leaf = "members"
    elif reference_edit_is_mappable(edit):
        # Any rulebase whose field is a flat member list: container == the
        # tag derived from referrer_kind (security/nat/pbf/qos/…).
        container = rule_container(edit.referrer_kind)
        entry = _find_named(
            scope.find(f"./{edit.rulebase}-rulebase/{container}/rules"), "entry", name
        )
        leaf = edit.field
    else:
        return None
    if entry is None:
        return None
    field_el = entry.find(leaf)
    if field_el is None:
        field_el = ET.SubElement(entry, leaf)
    return field_el


def _apply_reference_edit(root: ET.Element, edit: ReferenceEdit) -> None:
    scope = _scope_element(root, edit.referrer_location)
    if scope is None:
        raise PscError(
            f"scope '{edit.referrer_location}' not found while applying reference edit",
            ErrorType.INPUT,
        )
    field_el = _referrer_field_element(scope, edit)
    if field_el is None:
        # A nested/translation field the renderer flagged for manual review.
        return
    _set_members(field_el, edit.after)


def _apply_delete(root: ET.Element, kind: str, name: str, location: str) -> None:
    scope = _scope_element(root, location)
    if scope is None:
        return
    container = scope.find(kind)
    if container is None:
        return
    entry = _find_named(container, "entry", name)
    if entry is not None:
        container.remove(entry)


def _apply_rule_delete(root: ET.Element, d: RuleDelete) -> None:
    scope = _scope_element(root, d.location)
    if scope is None:
        return
    container = rule_container(d.referrer_kind)
    rules = scope.find(f"./{d.rulebase}-rulebase/{container}/rules")
    if rules is None:
        return
    entry = _find_named(rules, "entry", d.name)
    if entry is not None:
        rules.remove(entry)


def _apply_rename(root: ET.Element, kind: str, location: str, old: str, new: str) -> None:
    scope = _scope_element(root, location)
    if scope is None:
        return
    container = scope.find(kind)
    if container is None:
        return
    entry = _find_named(container, "entry", old)
    if entry is not None:
        entry.set("name", new)


def _apply_upsert(root: ET.Element, u: ObjectUpsert) -> None:
    scope = _scope_element(root, u.location)
    if scope is None:
        raise PscError(f"scope '{u.location}' not found while applying upsert", ErrorType.INPUT)
    container = scope.find(u.kind.value)
    if container is None:
        container = ET.SubElement(scope, u.kind.value)
    entry = _find_named(container, "entry", u.name)
    if entry is None:
        entry = ET.SubElement(container, "entry")
        entry.set("name", u.name)
    for leaf_path, value in u.fields.items():
        _set_leaf(entry, leaf_path, value)
    if u.members:
        leaf = "static" if u.kind.value == "address-group" else "members"
        field_el = entry.find(leaf) or ET.SubElement(entry, leaf)
        _set_members(field_el, u.members)
    if u.tags:
        tag_el = entry.find("tag") or ET.SubElement(entry, "tag")
        _set_members(tag_el, u.tags)


def _set_leaf(entry: ET.Element, path: str, value: str) -> None:
    """Set a possibly-nested leaf like `protocol/tcp/port` to `value`."""
    cur = entry
    for part in path.split("/"):
        nxt = cur.find(part)
        if nxt is None:
            nxt = ET.SubElement(cur, part)
        cur = nxt
    cur.text = value


def apply_changeset(xml_text: str, cs: ChangeSet) -> str:
    """Return a new config XML string with `cs` applied. Raises on a blocked
    plan — the safety gate is enforced here too, not only in the CLI.
    """
    if cs.is_blocked:
        raise PscError(
            "refusing to apply a blocked plan",
            ErrorType.CONFLICT,
            details={"blockers": cs.blockers},
        )
    root = _safe_fromstring(xml_text)
    config = root if root.tag == "config" else (root.find(".//config") or root)

    for u in cs.upserts:
        _apply_upsert(config, u)
    for edit in cs.reference_edits:
        _apply_reference_edit(config, edit)
    for rd in cs.rule_deletes:
        _apply_rule_delete(config, rd)
    for r in cs.renames:
        _apply_rename(config, r.kind.value, r.location, r.old_name, r.new_name)
    for d in cs.deletes:
        _apply_delete(config, d.kind.value, d.name, d.location)

    return ET.tostring(root, encoding="unicode")
