"""Reference-safe teardown of address objects (issue #5).

`decommission` answers a single operational question: *"this host/subnet is
gone — remove every trace of it, safely."* Given the address objects that match
an IP/CIDR/list, it builds one `ChangeSet` that, in this exact order:

  1. scrubs every deleted object (the matched addresses *and* any group emptied
     by the cascade) from every static address-group's member list and every
     rule's `source`/`destination` (security/NAT/policy),
  2. deletes any rule left *orphaned* — a rule whose source OR destination has
     no real member after the scrub (an empty field can never match traffic),
  3. deletes every group emptied by the scrub and the matched objects.

The teardown CASCADES to a fixpoint: scrubbing the matched addresses out of a
static group can empty it, so that group is deleted too — which means the
references *to that group* must in turn be scrubbed, possibly emptying a parent
group, and so on. Computing the delete set and the scrubs together to a fixpoint
is what upholds the core invariant: a referent is never removed before the
references to it are rewritten, so an executor walking the plan top-to-bottom can
never strand a dangling reference — even when the referent is a group emptied
mid-teardown. This composes the existing engines — `ReferenceGraph.where_used`
for discovery (groups are reference targets too), `field_members` for reading the
current member lists, and `gate_unmappable_reference_edits` for the
NAT-translation / PBF-nexthop safety gate — and adds exactly one new op type,
`RuleDelete`.

Two predefined sentinels are load-bearing for orphan detection: `'any'` is a
*real* surviving value (a rule matching `any` source still works), so a field is
orphaned only when it is *truly empty* — no members at all, not even `'any'`.
"""

from __future__ import annotations

from psc.core.changeset import (
    ChangeSet,
    ObjectDelete,
    ObjectKind,
    ReferenceEdit,
    RuleDelete,
    gate_unmappable_reference_edits,
)
from psc.core.dedup import field_members
from psc.core.models import Address, AddressGroup, Location, Snapshot
from psc.core.refs import Reference, ReferenceGraph, dag_filter_tags

# The two address-member rule fields decommission scrubs. `service`/`tag` are
# explicitly out of scope (an address object never lives there), and NAT
# translation / PBF nexthop are nested and gate-handled, not scrubbed in place.
_SCRUB_FIELDS = ("source", "destination")

# Address-naming fields that are nested (no flat member list): the gate refuses
# to silently skip these when the same plan tears the object down. Recorded as
# edits only so the gate can flag them; never scrubbed in place.
_NESTED_ADDR_FIELDS = ("source-translation", "destination-translation", "nexthop")


def _remove_members(before: list[str], drop: set[str]) -> list[str]:
    """`before` with every name in `drop` removed, order preserved."""
    return [m for m in before if m not in drop]


def _has_real_member(members: list[str]) -> bool:
    """Whether a scrubbed field still matches traffic.

    `'any'` is a predefined sentinel that counts as a surviving real value, so
    only a *truly empty* list (no members at all) means the field can no longer
    match — the orphan trigger.
    """
    return bool(members)


# Identity of a deletable object: (kind, name, location_name). `kind` is
# "address" or "address-group" — the two things a teardown can remove.
_ObjId = tuple[str, str, str]


def plan_decommission(  # noqa: PLR0912, PLR0915 — explicit safety phases + fixpoint
    snapshot: Snapshot,
    graph: ReferenceGraph,
    targets: list[Address],
    *,
    scope: Location | None = None,  # caller pre-filters matches; kept for API symmetry
    keep_groups: bool = False,
    keep_rules: bool = False,
) -> ChangeSet:
    """Plan the reference-safe teardown of `targets` (resolved address objects).

    The teardown CASCADES to a fixpoint: scrubbing the matched objects out of a
    static group can empty it, so that group is itself deleted — which means
    references *to that group* (a parent group's static list, a rule's
    source/destination) must in turn be scrubbed, possibly emptying more groups,
    and so on. We iterate scrub→find-newly-empty-groups until a pass discovers
    nothing new, so a referent is never removed before the references to it are
    rewritten — even when the referent is a group emptied mid-teardown.

    `keep_groups` scrubs rule/group fields but deletes neither groups nor the
    objects (a "loosen, don't remove" mode) and therefore performs no cascade;
    `keep_rules` keeps an orphaned rule's emptied-field edit instead of deleting
    the rule. A blocked plan carries zero ops (the invariant every consumer
    relies on).
    """
    cs = ChangeSet(title="decommission address objects")

    # Dedup matched objects by identity so each object yields exactly one delete
    # even when several CLI targets resolve to it.
    seen: set[tuple[str, str]] = set()
    matched: list[Address] = []
    for a in targets:
        ident = (a.location.name, a.name)
        if ident not in seen:
            seen.add(ident)
            matched.append(a)

    if not matched:
        cs.warnings.append("no address objects matched")
        return cs

    # An edit accumulator keyed by referrer field identity, so multiple removed
    # members hitting the same group/rule field merge into one edit.
    edit_key = tuple[str, str, str, str, str | None]
    edits: dict[edit_key, ReferenceEdit] = {}

    def _edit_for(ref: Reference) -> ReferenceEdit:
        rb = ref.rulebase.value if ref.rulebase else None
        key: edit_key = (
            ref.referrer_kind,
            ref.referrer_name,
            ref.referrer_location.name,
            ref.field,
            rb,
        )
        existing = edits.get(key)
        if existing is None:
            before = field_members(snapshot, ref)
            existing = ReferenceEdit(
                referrer_kind=ref.referrer_kind,
                referrer_name=ref.referrer_name,
                referrer_location=ref.referrer_location.name,
                field=ref.field,
                rulebase=rb,
                before=before,
                after=list(before),
            )
            edits[key] = existing
        return existing

    # -- the fixpoint ---------------------------------------------------------
    # `delete_set` is every object slated for deletion (matched addresses, then
    # any group emptied by the cascade). `worklist` holds objects whose
    # referents still need scrubbing; we seed it with the matched addresses and
    # add each newly-emptied group as it is discovered. Scrubbing an object's
    # NAME out of every referrer's flat member field, then recomputing which
    # static groups are now empty, repeats until a pass adds nothing new.
    delete_set: set[_ObjId] = {("address", a.name, a.location.name) for a in matched}
    worklist: list[tuple[str, str, Location]] = [("address", a.name, a.location) for a in matched]

    while worklist:
        next_worklist: list[tuple[str, str, Location]] = []
        for kind, name, location in worklist:
            drop = {name}
            for ref in graph.where_used(kind, name, location):
                is_group_static = ref.referrer_kind == "address-group" and ref.field == "static"
                if is_group_static or ref.field in _SCRUB_FIELDS:
                    edit = _edit_for(ref)
                    edit.after = _remove_members(edit.after, drop)
                elif ref.field in _NESTED_ADDR_FIELDS:
                    # A NAT translation field or PBF nexthop names the object but
                    # has no flat member list to rewrite. Record the would-be
                    # edit (members untouched) purely so the unmappable gate can
                    # see that the teardown strands it — and turn it into a
                    # blocker. This applies equally to a deleted group named in
                    # such a field.
                    _edit_for(ref)
                # service/tag fields are explicitly out of scope.

        if keep_groups:
            # "loosen, don't remove": scrub the direct referrers but never delete
            # a group, so there is no cascade to chase.
            break

        # Recompute which static groups are now empty (every current static
        # member is in `delete_set`, i.e. the post-scrub `after` is []). A newly
        # empty group not already slated for deletion is added to `delete_set`
        # AND queued so references TO it are scrubbed next pass — the cascade.
        for ag in snapshot.address_groups:
            if ag.static_members is None:  # dynamic group: no static list to empty
                continue
            gid: _ObjId = ("address-group", ag.name, ag.location.name)
            if gid in delete_set:
                continue
            if ag.static_members and all(
                _member_deleted(graph, ag, m, delete_set) for m in ag.static_members
            ):
                delete_set.add(gid)
                next_worklist.append(("address-group", ag.name, ag.location))

        worklist = next_worklist

    # -- dynamic-group filter tags (full teardown only) -----------------------
    # A dynamic group selects by tag at runtime; psc cannot rewrite a filter, so
    # if a matched object carries a tag that a DAG filter names, a full teardown
    # can't be made safe automatically — block. With keep_groups the group is
    # deliberately left in place, so this is allowed.
    if not keep_groups:
        target_tags: set[str] = set()
        for a in matched:
            target_tags.update(a.tags)
        for ag in snapshot.address_groups:
            if ag.dynamic_filter is None:
                continue
            shared = target_tags & dag_filter_tags(ag.dynamic_filter)
            if shared:
                tags = ", ".join(f"'{t}'" for t in sorted(shared))
                cs.blockers.append(
                    f"dynamic address-group '{ag.name}'@{ag.location.name} selects the "
                    f"decommissioned object(s) by tag {tags}; psc cannot auto-edit a DAG "
                    "filter — remove the tag(s) or the filter clause, then re-run"
                )

    # -- orphan rule detection ------------------------------------------------
    # A rule is orphaned iff after scrub its source OR destination is truly
    # empty. Reconstruct each rule's final src/dst from its edits.
    rule_fields: dict[tuple[str, str, str | None], dict[str, list[str]]] = {}
    for edit in edits.values():
        if edit.referrer_kind == "address-group":
            continue
        rid = (edit.referrer_kind, edit.referrer_name, edit.rulebase)
        rule_fields.setdefault(rid, {})[edit.field] = edit.after

    rule_delete_keys: set[tuple[str, str, str | None]] = set()
    for rid, fields in rule_fields.items():
        # An unscrubbed field keeps its real members; only a scrubbed field can
        # have become empty. So a rule is orphaned iff any *scrubbed* field is
        # empty (the other field, scrubbed or not, is irrelevant to that test).
        emptied = any(
            field in fields and not _has_real_member(fields[field]) for field in _SCRUB_FIELDS
        )
        if emptied:
            rule_delete_keys.add(rid)

    # Locate each orphaned rule to recover its location + rulebase for the op.
    if not keep_rules:
        for rid in rule_delete_keys:
            kind, name, rb = rid
            loc, rulebase = _locate_rule(snapshot, kind, name, rb)
            cs.rule_deletes.append(
                RuleDelete(referrer_kind=kind, name=name, location=loc, rulebase=rulebase)
            )
            cs.warnings.append(
                f"orphan rule '{name}' @{loc} {rulebase} will be deleted "
                "(source/destination empty after decommission — verify no traffic depends on it)"
            )
    else:
        for rid in rule_delete_keys:
            _kind, name, _rb = rid
            cs.warnings.append(
                f"rule '{name}' source/destination is now empty (kept per --keep-rules); "
                "it can no longer match traffic — review it by hand"
            )

    # -- emit ops in the safe order, minimally -------------------------------
    # Drop the now-pointless scrub edit on a GROUP that is itself being deleted:
    # rewriting members that vanish with the group is redundant (the whole
    # group entry goes). A SURVIVING group that named a deleted object keeps its
    # scrub. Rule edits are kept even for an orphaned rule: the existing
    # contract emits both the emptied-field edit *and* the RuleDelete (the edit
    # makes the plan auditable, the delete removes the dead rule). Group edits
    # first, then rule edits, so the human plan reads in dependency order.
    deleted_group_ids = {(n, loc) for (k, n, loc) in delete_set if k == "address-group"}
    group_edits = [
        e
        for e in edits.values()
        if e.referrer_kind == "address-group"
        and (e.referrer_name, e.referrer_location) not in deleted_group_ids
    ]
    rule_edits = [e for e in edits.values() if e.referrer_kind != "address-group"]
    cs.reference_edits.extend(group_edits)
    cs.reference_edits.extend(rule_edits)

    # -- delete groups (emptied by the cascade), then the matched objects ----
    if not keep_groups:
        for kind, name, loc_name in sorted(delete_set):
            obj_kind = ObjectKind.ADDRESS_GROUP if kind == "address-group" else ObjectKind.ADDRESS
            cs.deletes.append(ObjectDelete(kind=obj_kind, name=name, location=loc_name))
    else:
        for e in edits.values():
            if e.referrer_kind == "address-group" and not e.after:
                cs.warnings.append(
                    f"address-group '{e.referrer_name}'@{e.referrer_location} is now empty "
                    "(kept per --keep-groups) — it may dangle; delete it by hand if unused"
                )

    # The shared gate: refuse any scrub edit the appliers would silently skip
    # (NAT translation / PBF nexthop) now that the plan tears the object down —
    # a skipped repoint + delete is a dangling reference. RuleDelete and
    # ObjectDelete both count as teardowns inside the gate.
    gate_unmappable_reference_edits(cs)

    if cs.blockers:
        # Invariant: a blocked plan carries zero ops, so no consumer can execute
        # a partial teardown by iterating ops without checking `is_blocked`.
        cs.reference_edits.clear()
        cs.rule_deletes.clear()
        cs.deletes.clear()
    return cs


def _member_deleted(
    graph: ReferenceGraph, group: AddressGroup, member_name: str, delete_set: set[_ObjId]
) -> bool:
    """Whether `member_name` (a static member of `group`) is slated for deletion.

    Resolves the member NAME to its actual object along the device-group chain
    (PAN-OS shadowing) so the `delete_set` lookup keys on the real object's
    identity, not the bare name — a member shadowed by a nearer definition must
    test against that definition. A name that resolves to nothing (dangling) is
    not in any delete set, so the group is treated as still non-empty.
    """
    target = graph.resolve("address", member_name, group.location)
    if target is None:
        return False
    return (target.kind, target.name, target.location.name) in delete_set


def _locate_rule(
    snapshot: Snapshot, referrer_kind: str, name: str, rulebase: str | None
) -> tuple[str, str]:
    """Recover `(location_name, rulebase)` for a rule from the snapshot.

    `rulebase` from the edit may be None only for a degenerate rule with no
    rulebase; fall back to whatever the snapshot rule carries.
    """
    if referrer_kind == "security-rule":
        for r in snapshot.security_rules:
            if r.name == name and (rulebase is None or r.rulebase.value == rulebase):
                return r.location.name, r.rulebase.value
    elif referrer_kind == "nat-rule":
        for n in snapshot.nat_rules:
            if n.name == name and (rulebase is None or n.rulebase.value == rulebase):
                return n.location.name, n.rulebase.value
    else:
        for p in snapshot.policy_rules:
            if (
                p.referrer_kind == referrer_kind
                and p.name == name
                and (rulebase is None or p.rulebase.value == rulebase)
            ):
                return p.location.name, p.rulebase.value
    # Should not happen (the rule was found via where_used); default safely.
    return "shared", rulebase or "pre"
