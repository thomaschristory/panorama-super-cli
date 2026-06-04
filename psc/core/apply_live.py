"""Plan a `ChangeSet` as PAN-OS XML-API operations (live `--apply`, issue #1).

Pure and device-free: turn a `ChangeSet` into an ordered list of `XapiOp`
(set/edit/delete/rename addressed by xpath). `LiveSource.apply` walks the list
against a live `xapi`; keeping the planning here means the xpath construction is
unit-testable without a device, and `psc/core/` stays free of any SDK import.

Ordering mirrors `apply_xml`/`setcmd`: upserts, reference rewrites, renames,
then deletes — so a still-referenced object is never deleted before its
referrers are repointed, on the wire just as offline.

Unlike the offline applier (which *iterates* children to dodge quote-in-name
breakage), the XML API can only address a node by an `[@name='X']` xpath
predicate — there is no iteration alternative. A name carrying a single quote
therefore can't be addressed safely, so it is rejected up front rather than
sent as a malformed (or, worse, silently mis-resolving) xpath.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from pydantic import BaseModel

from psc.core.changeset import ChangeSet, ObjectUpsert, ReferenceEdit
from psc.output.errors import ErrorType, PscError

# The device entry under which Panorama keeps its device-groups. Fixed on
# Panorama; the same constant the SDK assumes for config xpaths.
_DEVICE = "localhost.localdomain"


class XapiOp(BaseModel):
    """One XML-API mutation. `element` carries the XML for set/edit; `newname`
    the target for rename; deletes need neither.
    """

    action: str  # "set" | "edit" | "delete" | "rename"
    xpath: str
    element: str | None = None
    newname: str | None = None


def _safe_name(value: str) -> str:
    if "'" in value:
        raise PscError(
            f"cannot address '{value}' over the XML API: a single quote in a name "
            "breaks the xpath predicate — rename it, or apply this plan offline "
            "(--config … --apply --out)",
            ErrorType.INPUT,
        )
    return value


def _base(location: str) -> str:
    if location == "shared":
        return "/config/shared"
    return (
        f"/config/devices/entry[@name='{_DEVICE}']"
        f"/device-group/entry[@name='{_safe_name(location)}']"
    )


def _entry_xpath(location: str, kind: str, name: str) -> str:
    return f"{_base(location)}/{kind}/entry[@name='{_safe_name(name)}']"


def _referrer_field_xpath(edit: ReferenceEdit) -> tuple[str, str] | None:
    """`(xpath_to_field, leaf_tag)` for a referrer's member field, or None for a
    nested/translation field the renderer flagged for manual review (NAT
    src/dst translation). Mirrors `apply_xml._referrer_field_element`.
    """
    base = _base(edit.referrer_location)
    name = _safe_name(edit.referrer_name)
    rb = edit.rulebase
    if edit.referrer_kind == "address-group":
        return f"{base}/address-group/entry[@name='{name}']/static", "static"
    if edit.referrer_kind == "service-group":
        return f"{base}/service-group/entry[@name='{name}']/members", "members"
    if edit.referrer_kind == "security-rule" and rb:
        path = f"{base}/{rb}-rulebase/security/rules/entry[@name='{name}']/{edit.field}"
        return path, edit.field
    if edit.referrer_kind == "nat-rule" and rb and edit.field in ("source", "destination"):
        path = f"{base}/{rb}-rulebase/nat/rules/entry[@name='{name}']/{edit.field}"
        return path, edit.field
    return None


def _member_field_xml(leaf: str, members: list[str]) -> str:
    el = ET.Element(leaf)
    for m in members:
        ET.SubElement(el, "member").text = m
    return ET.tostring(el, encoding="unicode")


def _entry_xml(u: ObjectUpsert) -> str:
    """The full `<entry>` element for an upsert — scalar leaves, members, tags.
    Built once and `edit`-ed in wholesale (create-or-replace), so a partial
    member append can't happen the way a `set` on a member field would.
    """
    entry = ET.Element("entry", {"name": u.name})
    for path, value in u.fields.items():
        cur = entry
        for part in path.split("/"):
            nxt = cur.find(part)
            if nxt is None:
                nxt = ET.SubElement(cur, part)
            cur = nxt
        cur.text = value
    if u.members:
        leaf = "static" if u.kind.value == "address-group" else "members"
        field = ET.SubElement(entry, leaf)
        for m in u.members:
            ET.SubElement(field, "member").text = m
    if u.tags:
        tag = ET.SubElement(entry, "tag")
        for t in u.tags:
            ET.SubElement(tag, "member").text = t
    return ET.tostring(entry, encoding="unicode")


def plan_xapi_ops(cs: ChangeSet) -> list[XapiOp]:
    """Lower a `ChangeSet` to ordered XML-API ops. Raises `CONFLICT` on a
    blocked plan (the safety gate, enforced on the live path too) and `INPUT` on
    a name that can't be addressed — both *before* any op is emitted, so a
    refused plan never reaches the device.
    """
    if cs.is_blocked:
        raise PscError(
            "refusing to apply a blocked plan",
            ErrorType.CONFLICT,
            details={"blockers": cs.blockers},
        )

    ops: list[XapiOp] = []
    for u in cs.upserts:
        ops.append(
            XapiOp(
                action="edit",
                xpath=_entry_xpath(u.location, u.kind.value, u.name),
                element=_entry_xml(u),
            )
        )
    for e in cs.reference_edits:
        parsed = _referrer_field_xpath(e)
        if parsed is None:
            continue  # nested/translation field: renderer already flagged it for review
        xpath, leaf = parsed
        if e.after:
            ops.append(XapiOp(action="edit", xpath=xpath, element=_member_field_xml(leaf, e.after)))
        else:
            ops.append(XapiOp(action="delete", xpath=xpath))
    for r in cs.renames:
        ops.append(
            XapiOp(
                action="rename",
                xpath=_entry_xpath(r.location, r.kind.value, r.old_name),
                newname=_safe_name(r.new_name),
            )
        )
    for d in cs.deletes:
        ops.append(XapiOp(action="delete", xpath=_entry_xpath(d.location, d.kind.value, d.name)))
    return ops
