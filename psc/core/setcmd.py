"""Render objects and change-sets as PAN-OS `set` CLI commands.

`set` output is both a first-class `--output` format and the actionable
artifact for offline mode: paste it into a Panorama config session (or
`load config partial`) to apply what `psc` planned. Because PAN-OS `set` on a
member field *appends*, every member-field edit is rendered as
`delete <path> <field>` followed by `set <path> <field> [ ... ]`, which makes
the result idempotent rather than additive.
"""

from __future__ import annotations

from psc.core.changeset import (
    ChangeSet,
    ObjectDelete,
    ObjectRename,
    ObjectUpsert,
    ReferenceEdit,
    reference_edit_is_mappable,
)
from psc.core.models import (
    Address,
    AddressGroup,
    Service,
    ServiceGroup,
    Tag,
)
from psc.core.rulebases import rule_container


def scope_prefix(location_name: str) -> str:
    """`shared` -> 'shared'; a device-group name -> 'device-group <name>'."""
    if location_name == "shared":
        return "shared"
    return f"device-group {location_name}"


def _members(values: list[str]) -> str:
    return "[ " + " ".join(values) + " ]"


def _quote(text: str) -> str:
    return '"' + text.replace('"', '\\"') + '"'


def address_lines(a: Address) -> list[str]:
    p = f"set {scope_prefix(a.location.name)} address {a.name}"
    lines = [f"{p} {a.type.value} {a.value}"]
    if a.description:
        lines.append(f"{p} description {_quote(a.description)}")
    if a.tags:
        lines.append(f"{p} tag {_members(a.tags)}")
    return lines


def address_group_lines(g: AddressGroup) -> list[str]:
    p = f"set {scope_prefix(g.location.name)} address-group {g.name}"
    lines: list[str] = []
    if g.dynamic_filter is not None:
        lines.append(f"{p} dynamic filter {_quote(g.dynamic_filter)}")
    else:
        lines.append(f"{p} static {_members(g.static_members or [])}")
    if g.description:
        lines.append(f"{p} description {_quote(g.description)}")
    if g.tags:
        lines.append(f"{p} tag {_members(g.tags)}")
    return lines


def service_lines(s: Service) -> list[str]:
    p = f"set {scope_prefix(s.location.name)} service {s.name}"
    lines: list[str] = []
    if s.destination_port:
        lines.append(f"{p} protocol {s.protocol} port {s.destination_port}")
    if s.source_port:
        lines.append(f"{p} protocol {s.protocol} source-port {s.source_port}")
    if s.description:
        lines.append(f"{p} description {_quote(s.description)}")
    if s.tags:
        lines.append(f"{p} tag {_members(s.tags)}")
    return lines


def service_group_lines(g: ServiceGroup) -> list[str]:
    p = f"set {scope_prefix(g.location.name)} service-group {g.name}"
    lines = [f"{p} members {_members(g.members)}"]
    if g.tags:
        lines.append(f"{p} tag {_members(g.tags)}")
    return lines


def tag_lines(t: Tag) -> list[str]:
    p = f"set {scope_prefix(t.location.name)} tag {t.name}"
    lines: list[str] = []
    if t.color:
        lines.append(f"{p} color {t.color}")
    if t.comments:
        lines.append(f"{p} comments {_quote(t.comments)}")
    return lines or [p]


# -- reference-edit field paths -----------------------------------------


def _referrer_path(edit: ReferenceEdit) -> tuple[str, str] | None:
    """Return `(path_to_field_parent, field_leaf)` or None if the field path
    is too complex to render as a flat member-list (NAT translation, PBF
    nexthop). Uses the same mappability gate as both appliers, so a `# REVIEW`
    here lines up exactly with an op the appliers skip.
    """
    scope = scope_prefix(edit.referrer_location)
    if edit.referrer_kind == "address-group":
        return (f"{scope} address-group {edit.referrer_name}", "static")
    if edit.referrer_kind == "service-group":
        return (f"{scope} service-group {edit.referrer_name}", "members")
    if reference_edit_is_mappable(edit):
        rb = f"{edit.rulebase}-rulebase"
        container = rule_container(edit.referrer_kind)
        return (f"{scope} {rb} {container} rules {edit.referrer_name}", edit.field)
    return None


def reference_edit_lines(edit: ReferenceEdit) -> list[str]:
    parsed = _referrer_path(edit)
    if parsed is None:
        # NAT translation or another nested field — emit an advisory the
        # operator must hand-apply; the structured plan still carries it.
        return [
            f"# REVIEW: rewrite {edit.referrer_kind} '{edit.referrer_name}' "
            f"@{edit.referrer_location} {edit.field}: {edit.before} -> {edit.after}"
        ]
    path, leaf = parsed
    lines = [f"delete {path} {leaf}"]
    if edit.after:
        lines.append(f"set {path} {leaf} {_members(edit.after)}")
    return lines


def rename_lines(r: ObjectRename) -> list[str]:
    scope = scope_prefix(r.location)
    return [f"rename {scope} {r.kind.value} {r.old_name} to {r.new_name}"]


def delete_lines(d: ObjectDelete) -> list[str]:
    scope = scope_prefix(d.location)
    return [f"delete {scope} {d.kind.value} {d.name}"]


def upsert_lines(u: ObjectUpsert) -> list[str]:
    p = f"set {scope_prefix(u.location)} {u.kind.value} {u.name}"
    lines = [f"{p} {leaf} {val}" for leaf, val in u.fields.items()]
    if u.members:
        leaf = "static" if u.kind.value == "address-group" else "members"
        lines.append(f"{p} {leaf} {_members(u.members)}")
    if u.tags:
        lines.append(f"{p} tag {_members(u.tags)}")
    return lines


def render_changeset(cs: ChangeSet) -> list[str]:
    """Render a full change-set as an ordered `set`/`delete`/`rename` script.

    Order mirrors `ChangeSet`'s safe ordering: upserts, then reference
    rewrites, then renames, then deletes — never delete before repoint.
    """
    lines: list[str] = [f"# {cs.title}"]
    for w in cs.warnings:
        lines.append(f"# WARNING: {w}")
    if cs.is_blocked:
        lines.append("# BLOCKED — plan is unsafe and will not be applied:")
        lines += [f"#   - {b}" for b in cs.blockers]
        return lines
    for u in cs.upserts:
        lines += upsert_lines(u)
    for e in cs.reference_edits:
        lines += reference_edit_lines(e)
    for r in cs.renames:
        lines += rename_lines(r)
    for d in cs.deletes:
        lines += delete_lines(d)
    return lines
