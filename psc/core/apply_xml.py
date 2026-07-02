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

import copy
import xml.etree.ElementTree as ET

from defusedxml.ElementTree import fromstring as _safe_fromstring

from psc.core.changeset import (
    ChangeSet,
    ObjectUpsert,
    ReferenceEdit,
    RuleDelete,
    member_field_leaf,
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
        leaf = member_field_leaf(u.kind)
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


def _dg_device_name(root: ET.Element, location: str) -> str:
    """The `<devices>/<entry name>` under which device-group `location` lives, so
    a partial config nests the DG at its real structural path. Defaults to the
    conventional `localhost.localdomain` when the export omits the wrapper."""
    for dev in root.findall("./devices/entry"):
        for dg in dev.findall("./device-group/entry"):
            if dg.get("name") == location:
                return dev.get("name") or "localhost.localdomain"
    return "localhost.localdomain"


# One touched container: its element tag path under a scope, plus which entries
# the changeset added/updated (kept, copied in final state) vs removed (can't be
# expressed by an additive partial import — noted as a comment instead).
class _TouchedContainer:
    __slots__ = ("path", "present", "removed")

    def __init__(self, path: str) -> None:
        self.path = path
        self.present: list[str] = []
        self.removed: list[str] = []

    def add_present(self, name: str) -> None:
        if name in self.removed:
            self.removed.remove(name)  # a later re-add supersedes an earlier remove
        if name not in self.present:
            self.present.append(name)

    def add_removed(self, name: str) -> None:
        if name in self.present:
            self.present.remove(name)  # a later remove supersedes an earlier add
        if name not in self.removed:
            self.removed.append(name)


def _referrer_container_path(kind: str, rulebase: str | None) -> str | None:
    """Element-path (under a scope) of a reference-edit / rule-delete referrer."""
    if kind == "address-group":
        return "address-group"
    if kind == "service-group":
        return "service-group"
    container = rule_container(kind)
    if container is None or rulebase is None:
        return None
    return f"{rulebase}-rulebase/{container}/rules"


def _collect_touched(
    changesets: list[ChangeSet],
) -> dict[str, dict[str, _TouchedContainer]]:
    """Group every touched entry by scope location then container path.

    Accepts several changesets (a compounded batch) applied in order. Deterministic:
    locations, container paths, and entry names all keep first-seen order, so the
    rendered partial config is stable for equal input. A later change that removes
    an entry an earlier change added supersedes it (present → removed) so the final
    state governs.
    """
    scopes: dict[str, dict[str, _TouchedContainer]] = {}

    def container(location: str, path: str) -> _TouchedContainer:
        by_path = scopes.setdefault(location, {})
        tc = by_path.get(path)
        if tc is None:
            tc = _TouchedContainer(path)
            by_path[path] = tc
        return tc

    for cs in changesets:
        for u in cs.upserts:
            container(u.location, u.kind.value).add_present(u.name)
        for edit in cs.reference_edits:
            path = _referrer_container_path(edit.referrer_kind, edit.rulebase)
            if path is not None:
                container(edit.referrer_location, path).add_present(edit.referrer_name)
        for rd in cs.rule_deletes:
            path = _referrer_container_path(rd.referrer_kind, rd.rulebase)
            if path is not None:
                container(rd.location, path).add_removed(rd.name)
        for r in cs.renames:
            # The object survives under its NEW name; the old name is a removal the
            # additive partial can't express (import would leave the old entry).
            container(r.location, r.kind.value).add_present(r.new_name)
            container(r.location, r.kind.value).add_removed(r.old_name)
        for d in cs.deletes:
            container(d.location, d.kind.value).add_removed(d.name)

    return scopes


def _ensure_path(parent: ET.Element, path: str) -> ET.Element:
    """Find-or-create the nested element chain `path` (e.g. `pre-rulebase/security/rules`)."""
    cur = parent
    for part in path.split("/"):
        nxt = cur.find(part)
        if nxt is None:
            nxt = ET.SubElement(cur, part)
        cur = nxt
    return cur


def _fill_container(
    dest_scope: ET.Element,
    applied_scope: ET.Element | None,
    tc: _TouchedContainer,
) -> None:
    """Populate one container in the partial with the FINAL state of its touched
    entries (copied from the applied config), plus comment markers for entries the
    changeset removed (additive import cannot delete them)."""
    dest_container = _ensure_path(dest_scope, tc.path)
    src_container = applied_scope.find(tc.path) if applied_scope is not None else None
    for name in tc.present:
        entry = _find_named(src_container, "entry", name)
        if entry is not None:
            # deepcopy, not append: an ET element has a single parent, so a bare
            # append would MOVE the node out of `applied_scope` and mutate the
            # source tree. Copy so the applied config stays intact.
            dest_container.append(copy.deepcopy(entry))
    for name in tc.removed:
        # Only a marker: PAN-OS partial import is additive, so a deletion is
        # reported here (and in the plan's set-script / warnings), never applied.
        comment = ET.Comment(f" psc: delete entry '{name}' (not expressible in additive import) ")
        dest_container.append(comment)


def partial_config_xml(xml_text: str, cs: ChangeSet) -> str:
    """Return a MINIMAL partial `<config>` holding only the subtrees `cs` touches.

    Unlike `apply_changeset` (which rewrites the whole document), this emits a
    small, diffable, targeted config: each touched object/group/rule entry in its
    FINAL post-change state, positioned at its real structural path (`<shared>` or
    `<devices><entry><device-group><entry name=DG>`), so PAN-OS can `load config
    partial` it or an operator can review just the delta.

    Semantics — the partial carries the *final presence* of every touched
    container:

    * upserts / renames / reference edits / rule targets → the touched entry is
      copied in its final state (a repointed group shows the new member list; a
      renamed object shows its new name).
    * deletions (top-level object deletes, rule deletes, and the pre-rename name)
      CANNOT be expressed by an additive partial import — importing this file does
      not remove anything. Each removal is emitted as an XML comment marker inside
      its container and surfaced as a plan warning, so a delete is *reported*, not
      silently dropped. Apply deletions via the plan's `set`/`delete` script (or a
      full `apply_changeset`) instead.

    Output is well-formed, deterministic (stable ordering), and parseable by
    `parse_config`. Raises on a blocked plan (same gate as `apply_changeset`).
    """
    if cs.is_blocked:
        raise PscError(
            "refusing to apply a blocked plan",
            ErrorType.CONFLICT,
            details={"blockers": cs.blockers},
        )
    applied_text = apply_changeset(xml_text, cs)
    return _render_partial(applied_text, [cs])


def partial_config_from_batch(applied_xml: str, changesets: list[ChangeSet]) -> str:
    """Partial config for an already-compounded batch (the workbench path).

    `applied_xml` is the config with every changeset already applied (e.g. the
    session's `working_xml`); `changesets` names which subtrees were touched.
    Same semantics as `partial_config_xml` (see its docstring), for N changesets.
    Refuses a blocked plan, mirroring the hard gate in `apply_changeset`.
    """
    for cs in changesets:
        if cs.is_blocked:
            raise PscError(
                "refusing to render a partial config for a blocked plan",
                ErrorType.CONFLICT,
                details={"blockers": cs.blockers},
            )
    return _render_partial(applied_xml, changesets)


def _render_partial(applied_text: str, changesets: list[ChangeSet]) -> str:
    applied_root = _safe_fromstring(applied_text)
    applied_config = (
        applied_root
        if applied_root.tag == "config"
        else (applied_root.find(".//config") or applied_root)
    )

    partial = ET.Element("config")
    touched = _collect_touched(changesets)

    # Shared first, then device-groups, for stable, human-readable structure.
    shared_touched = touched.pop("shared", None)
    if shared_touched is not None:
        shared_el = ET.SubElement(partial, "shared")
        applied_shared = applied_config.find("shared")
        for path in shared_touched:
            _fill_container(shared_el, applied_shared, shared_touched[path])

    if touched:
        devices_el = ET.SubElement(partial, "devices")
        # Group DGs under their owning device entry (usually one), stable order.
        dev_entries: dict[str, ET.Element] = {}
        for location in touched:
            dev_name = _dg_device_name(applied_config, location)
            dev_el = dev_entries.get(dev_name)
            if dev_el is None:
                dev_el = ET.SubElement(devices_el, "entry")
                dev_el.set("name", dev_name)
                ET.SubElement(dev_el, "device-group")
                dev_entries[dev_name] = dev_el
            dg_container = dev_el.find("device-group")
            assert dg_container is not None  # created just above
            dg_el = ET.SubElement(dg_container, "entry")
            dg_el.set("name", location)
            applied_scope = _scope_element(applied_config, location)
            for path in touched[location]:
                _fill_container(dg_el, applied_scope, touched[location][path])

    return ET.tostring(partial, encoding="unicode")


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
