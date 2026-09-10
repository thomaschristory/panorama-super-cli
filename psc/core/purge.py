"""Reference-safe teardown of objects named by kind, name and location (issue #181).

`refs unused` gives a list of cleanup candidates. Until now that list was a
dead end: nothing could turn it into a safe deletion. `plan_purge` closes that
gap. It takes `(kind, name, location)` targets of any of the five object kinds
and returns one `ChangeSet` that removes them safely.

`decommission` answers "this IP is gone, remove every trace of it". `purge`
answers "these named objects are dead, remove them". The two engines share one
safety model:

  1. scrub every deleted object out of every group member list and every rule
     field that names it,
  2. delete a rule that loses the last member of a required field, because an
     empty field can never match traffic,
  3. delete a group that the scrub empties, and cascade,
  4. delete the objects last.

The teardown cascades to a fixpoint. A scrub can empty a group, so that group
is deleted too, so the references to that group must also be scrubbed, and so
on. The plan therefore never removes a referent before it rewrites the
references to it. An executor can walk the plan from top to bottom and never
strand a dangling reference.

Two rules keep the engine honest where it cannot rewrite something:

  * `'any'` is a real surviving member. A field is orphaned only when it holds
    nothing at all.
  * A reference psc cannot rewrite (a NAT translation, a PBF next hop, a DAG
    filter, an object's own tag list) becomes a blocker, never a silent skip.
"""

from __future__ import annotations

from collections.abc import Sequence

from psc.core.changeset import (
    ChangeSet,
    ObjectDelete,
    ObjectKind,
    ReferenceEdit,
    RuleDelete,
    gate_unmappable_reference_edits,
)
from psc.core.dedup import field_members
from psc.core.models import Location, NatRule, PolicyRule, SecurityRule, Snapshot
from psc.core.refs import (
    Reference,
    ReferenceGraph,
    Target,
    dag_filter_tags,
    fall_through_note,
    reference_breaks,
)
from psc.core.rulebases import FLAT_RULE_FIELDS, rule_container

# The five kinds `refs unused` reports, and therefore the five kinds a delete
# plan must handle.
PURGE_KINDS: tuple[str, ...] = ("address", "address-group", "service", "service-group", "tag")

# The flat member list each group kind keeps its members in. These are the only
# object referrers psc can rewrite in place.
_GROUP_MEMBER_FIELD: dict[str, str] = {"address-group": "static", "service-group": "members"}

# The namespace a group's members live in. A member name resolves through the
# group's own location chain, so a device-group member can shadow a shared one.
_GROUP_NAMESPACE: dict[str, str] = {"address-group": "address", "service-group": "service"}

# Rule fields that must keep at least one member. PAN-OS rejects an empty
# source, destination, service or application, and such a rule can never match
# traffic, so losing the last member orphans the rule. `tag` is optional
# metadata, so emptying it is harmless.
_REQUIRED_RULE_FIELDS: tuple[str, ...] = ("source", "destination", "service", "application")

# Nested reference fields with no flat member list. They are recorded as edits
# with `before == after` so the gate can refuse the plan; they are never
# rewritten.
_NESTED_FIELDS: tuple[str, ...] = ("source-translation", "destination-translation", "nexthop")

# Object referrers that carry a `tag` list. psc has no repoint path for them, so
# an edit here is recorded only to make the gate block the plan.
_TAG_BEARING_KINDS: frozenset[str] = frozenset(
    {"address", "address-group", "service", "service-group"}
)

# Identity of a deletable object: (kind, name, location name).
_ObjId = tuple[str, str, str]

_KIND_TO_OBJECT_KIND: dict[str, ObjectKind] = {
    "address": ObjectKind.ADDRESS,
    "address-group": ObjectKind.ADDRESS_GROUP,
    "service": ObjectKind.SERVICE,
    "service-group": ObjectKind.SERVICE_GROUP,
    "tag": ObjectKind.TAG,
}


def _remove_members(before: list[str], drop: set[str]) -> list[str]:
    """`before` without the names in `drop`. The order does not change."""
    return [m for m in before if m not in drop]


def _defined_index(snapshot: Snapshot) -> dict[_ObjId, object]:
    """Every deletable object in `snapshot`, keyed by identity."""
    out: dict[_ObjId, object] = {}
    for kind, items in (
        ("address", snapshot.addresses),
        ("address-group", snapshot.address_groups),
        ("service", snapshot.services),
        ("service-group", snapshot.service_groups),
        ("tag", snapshot.tags),
    ):
        for obj in items:
            out[(kind, obj.name, obj.location.name)] = obj
    return out


def _newly_empty_groups(
    snapshot: Snapshot, graph: ReferenceGraph, delete_set: set[_ObjId]
) -> list[Target]:
    """Static groups this plan empties.

    A group is emptied when no member name still resolves to an object. A member
    that falls through to a shared object of the same name still resolves, so a
    shadowed member never empties the group (see `refs.reference_breaks`).

    A dynamic address-group has no member list to empty, so the scan skips it.
    A group that is already empty stays: emptying is a consequence of this
    teardown, so a group that was empty before is somebody else's problem.
    """
    found: list[Target] = []
    for group, kind, members in (
        *(
            (g, "address-group", g.static_members)
            for g in snapshot.address_groups
            if g.static_members is not None
        ),
        *((s, "service-group", s.members) for s in snapshot.service_groups),
    ):
        gid: _ObjId = (kind, group.name, group.location.name)
        if gid in delete_set or not members:
            continue
        namespace = _GROUP_NAMESPACE[kind]
        if all(reference_breaks(graph, namespace, group.location, m, delete_set) for m in members):
            delete_set.add(gid)
            found.append(Target(kind=kind, name=group.name, location=group.location))
    return found


def _dag_blockers(
    snapshot: Snapshot, matched: list[Target], index: dict[_ObjId, object], delete_set: set[_ObjId]
) -> list[str]:
    """Refuse to delete anything a surviving DAG filter still selects.

    A dynamic address-group selects its members by tag. psc cannot edit a filter
    expression, so removing a tagged address, or the tag itself, would change
    what the group matches. The plan blocks instead. A DAG that this same plan
    deletes is not a problem, so it is skipped.
    """
    doomed: set[str] = set()
    for target in matched:
        if target.kind == "tag":
            # Deleting the tag itself strands every filter that names it.
            doomed.add(target.name)
        elif target.kind == "address":
            # Only an address can be a DAG member, so only an address's own tags
            # can change what a filter matches.
            obj = index[(target.kind, target.name, target.location.name)]
            doomed |= set(getattr(obj, "tags", []) or [])
    if not doomed:
        return []
    out: list[str] = []
    for group in snapshot.address_groups:
        if group.dynamic_filter is None:
            continue
        if ("address-group", group.name, group.location.name) in delete_set:
            continue
        hit = sorted(doomed & dag_filter_tags(group.dynamic_filter))
        if hit:
            names = ", ".join(f"'{t}'" for t in hit)
            out.append(
                f"dynamic address-group '{group.name}'@{group.location.name} selects the "
                f"deleted object(s) by tag {names}; psc cannot auto-edit a DAG filter — "
                "remove the tag(s) or the filter clause, or delete that group too, then re-run"
            )
    return out


def _locate_rule(snapshot: Snapshot, name: str, location: str, rulebase: str | None) -> str:
    """The rulebase (`pre` or `post`) of a rule. `pre` when the rule is unknown."""
    if rulebase is not None:
        return rulebase
    rules: list[SecurityRule | NatRule | PolicyRule] = [
        *snapshot.security_rules,
        *snapshot.nat_rules,
        *snapshot.policy_rules,
    ]
    for rule in rules:
        if rule.name == name and rule.location.name == location:
            return str(rule.rulebase.value)
    return "pre"


def plan_purge(  # noqa: PLR0912, PLR0915 — explicit safety phases plus the fixpoint
    snapshot: Snapshot,
    graph: ReferenceGraph,
    targets: Sequence[Target],
    *,
    keep_groups: bool = False,
    keep_rules: bool = False,
) -> ChangeSet:
    """Plan the reference-safe deletion of `targets`.

    Each target names one object by kind, name and location. A target that names
    nothing blocks the plan, because a delete list that silently drops an entry
    is worse than no plan at all.

    `keep_groups` scrubs the member lists but deletes nothing, so nothing
    cascades. `keep_rules` keeps a rule that loses a required field and warns
    about it instead of deleting it. A blocked plan carries zero ops.
    """
    cs = ChangeSet(title="delete objects")
    index = _defined_index(snapshot)

    # Phase 0 — resolve the targets. Dedup first so one object yields one delete
    # even when the caller names it twice.
    matched: list[Target] = []
    seen: set[_ObjId] = set()
    for target in targets:
        ident: _ObjId = (target.kind, target.name, target.location.name)
        if ident in seen:
            continue
        seen.add(ident)
        if target.kind not in _KIND_TO_OBJECT_KIND:
            cs.blockers.append(
                f"'{target.kind}' is not a deletable kind (choose one of {', '.join(PURGE_KINDS)})"
            )
            continue
        if ident not in index:
            cs.blockers.append(
                f"no {target.kind} '{target.name}' @{target.location.name} — nothing to delete"
            )
            continue
        matched.append(target)

    if not matched:
        if not cs.blockers:
            cs.warnings.append("no objects matched")
        return cs

    delete_set: set[_ObjId] = {(t.kind, t.name, t.location.name) for t in matched}
    edits: dict[tuple[str, str, str, str, str | None], ReferenceEdit] = {}

    def _edit_for(ref: Reference) -> ReferenceEdit:
        """The accumulated edit for one referrer field, created on first use."""
        rulebase = ref.rulebase.value if ref.rulebase is not None else None
        key = (
            ref.referrer_kind,
            ref.referrer_name,
            ref.referrer_location.name,
            ref.field,
            rulebase,
        )
        edit = edits.get(key)
        if edit is None:
            before = field_members(snapshot, ref)
            edit = ReferenceEdit(
                referrer_kind=ref.referrer_kind,
                referrer_name=ref.referrer_name,
                referrer_location=ref.referrer_location.name,
                field=ref.field,
                rulebase=rulebase,
                before=before,
                after=list(before),
            )
            edits[key] = edit
        return edit

    # Phase 1 — grow the delete set to a fixpoint, BEFORE any scrub. Emptiness
    # depends only on the delete set, so the two can separate. They must: a scrub
    # decision needs the FINAL delete set to answer "does this name still resolve
    # afterwards", and a later pass can add the very object that answer turns on.
    if not keep_groups:
        while _newly_empty_groups(snapshot, graph, delete_set):
            pass

    # Phase 2 — scrub each deleted object out of every reference it breaks.
    for doomed_kind, doomed_name, doomed_loc_name in sorted(delete_set):
        doomed_loc = (
            Location.shared() if doomed_loc_name == "shared" else Location.dg(doomed_loc_name)
        )
        for ref in graph.where_used(doomed_kind, doomed_name, doomed_loc):
            rk, field = ref.referrer_kind, ref.field
            flat = _GROUP_MEMBER_FIELD.get(rk) == field or (
                rule_container(rk) is not None and field in FLAT_RULE_FIELDS
            )
            own_tag = field == "tag" and rk in _TAG_BEARING_KINDS
            if not (flat or own_tag or field in _NESTED_FIELDS):
                # A `dynamic` edge is a DAG filter match. `_dag_blockers` owns it.
                continue
            if not reference_breaks(
                graph, ref.namespace, ref.referrer_location, ref.target_name, delete_set
            ):
                # The name still resolves — a shared object of the same name
                # shadowed by this one. The reference is not stranded, so psc
                # rewrites nothing and refuses nothing. It says what the name
                # means now, because the referrer changed meaning in silence.
                note = fall_through_note(graph, ref, delete_set)
                if note is not None:
                    cs.warnings.append(note)
                continue
            if flat:
                edit = _edit_for(ref)
                edit.after = _remove_members(edit.after, {ref.target_name})
            elif own_tag:
                # An object's own tag list. psc has no repoint path for it, and
                # `field_members` cannot even read it, so never build an edit —
                # block instead. A carrier this plan also deletes is fine.
                carrier: _ObjId = (rk, ref.referrer_name, ref.referrer_location.name)
                if carrier not in delete_set:
                    cs.blockers.append(
                        f"{rk} '{ref.referrer_name}'@{ref.referrer_location.name} carries tag "
                        f"'{doomed_name}'; psc cannot rewrite an object's tag list — remove "
                        "the tag "
                        "there, or delete that object too, then re-run"
                    )
            else:
                # A nested field has no flat list to rewrite. Record the edit so
                # the gate refuses the plan rather than stranding the reference.
                _edit_for(ref)

    # Phase 3 — refuse a delete a surviving DAG filter still selects.
    if not keep_groups:
        cs.blockers.extend(_dag_blockers(snapshot, matched, index, delete_set))

    # Phase 4 — a rule that loses the last member of a required field is orphaned.
    rule_fields: dict[tuple[str, str, str, str | None], dict[str, list[str]]] = {}
    for edit in edits.values():
        if edit.referrer_kind in _GROUP_MEMBER_FIELD:
            continue
        rid = (edit.referrer_kind, edit.referrer_name, edit.referrer_location, edit.rulebase)
        rule_fields.setdefault(rid, {})[edit.field] = edit.after
    for rid, fields in rule_fields.items():
        kind, name, location, rulebase = rid
        emptied = [f for f in _REQUIRED_RULE_FIELDS if f in fields and not fields[f]]
        if not emptied:
            continue
        which = "/".join(emptied)
        if keep_rules:
            cs.warnings.append(
                f"rule '{name}' @{location} {which} is now empty (kept per --keep-rules); "
                "it can no longer match traffic — review it by hand"
            )
            continue
        resolved = _locate_rule(snapshot, name, location, rulebase)
        cs.rule_deletes.append(
            RuleDelete(referrer_kind=kind, name=name, location=location, rulebase=resolved)
        )
        cs.warnings.append(
            f"orphan rule '{name}' @{location} {resolved} will be deleted "
            f"({which} empty after the delete — verify no traffic depends on it)"
        )

    # Phase 5 — emit the edits. An edit on a referrer this plan deletes is
    # redundant, so drop it. A rule edit survives even when the rule goes, so the
    # plan stays readable.
    group_edits: list[ReferenceEdit] = []
    rule_edits: list[ReferenceEdit] = []
    for edit in edits.values():
        owner: _ObjId = (edit.referrer_kind, edit.referrer_name, edit.referrer_location)
        if edit.referrer_kind in _GROUP_MEMBER_FIELD:
            if owner not in delete_set:
                group_edits.append(edit)
        else:
            rule_edits.append(edit)
    cs.reference_edits = group_edits + rule_edits

    # Phase 6 — the deletes. `sorted` puts an address before an address-group and
    # a tag last, so a carrier is always removed before the tag it carries.
    if keep_groups:
        for edit in group_edits:
            if not edit.after:
                cs.warnings.append(
                    f"{edit.referrer_kind} '{edit.referrer_name}'@{edit.referrer_location} is now "
                    "empty (kept per --keep-groups) — it may dangle; delete it by hand if unused"
                )
    else:
        for kind, name, location in sorted(delete_set):
            cs.deletes.append(
                ObjectDelete(kind=_KIND_TO_OBJECT_KIND[kind], name=name, location=location)
            )

    # Phase 7 — the operator warnings `unused` cannot decide for them.
    cs.warnings.extend(_candidate_warnings(graph, matched))

    # Phase 8 — refuse a reference psc cannot repoint.
    gate_unmappable_reference_edits(cs)

    # Phase 9 — a blocked plan carries zero ops.
    if cs.blockers:
        cs.reference_edits.clear()
        cs.rule_deletes.clear()
        cs.deletes.clear()
    return cs


def _candidate_warnings(graph: ReferenceGraph, matched: list[Target]) -> list[str]:
    """Warn about what psc cannot see, per candidate.

    Two blind spots matter at delete time. A shared object may serve a device
    group or a template this config does not describe. A tagged address may join
    a dynamic address-group at runtime through an externally registered IP, which
    lives in device state and never appears in the config (see issue #180).
    """
    out: list[str] = []
    for target in matched:
        if target.location.is_shared:
            out.append(
                f"'{target.name}' is a shared {target.kind} — verify no device-group, template "
                "or device config outside this export depends on it"
            )
        tags = graph.tags_for(target)
        if tags:
            names = ", ".join(f"'{t}'" for t in tags)
            out.append(
                f"'{target.name}'@{target.location.name} carries tag {names} — a dynamic "
                "address-group may select it at runtime; verify in Panorama before you delete"
            )
    return out
