"""Reference-safe teardown of address objects (issue #5).

`decommission` answers a single operational question: *"this host/subnet is
gone — remove every trace of it, safely."* Given the address objects that match
an IP/CIDR/list, it builds one `ChangeSet` that, in this exact order:

  1. scrubs each object from every static address-group's member list,
  2. scrubs it from every rule's `source`/`destination` (security/NAT/policy),
  3. deletes any rule left *orphaned* — a rule whose source OR destination has
     no real member after the scrub (an empty field can never match traffic),
  4. deletes any static group emptied by the scrub,
  5. deletes the objects themselves.

The ordering is the whole point: a referent is never removed before the
references to it are rewritten, so an executor walking the plan top-to-bottom
can never strand a dangling reference. This composes the existing engines —
`ReferenceGraph.where_used` for discovery, `field_members` for reading the
current member lists, and `gate_unmappable_reference_edits` for the
NAT-translation / PBF-nexthop safety gate — and adds exactly one new op type,
`RuleDelete`.

Two predefined sentinels are load-bearing for orphan detection: `'any'` is a
*real* surviving value (a rule matching `any` source still works), so a field is
orphaned only when it is *truly empty* — no members at all, not even `'any'`.
"""

from __future__ import annotations

import re

from psc.core.changeset import (
    ChangeSet,
    ObjectDelete,
    ObjectKind,
    ReferenceEdit,
    RuleDelete,
    gate_unmappable_reference_edits,
)
from psc.core.dedup import field_members
from psc.core.models import Address, Location, Snapshot
from psc.core.refs import Reference, ReferenceGraph

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


def plan_decommission(  # noqa: PLR0912, PLR0915 — five explicit safety phases
    snapshot: Snapshot,
    graph: ReferenceGraph,
    targets: list[Address],
    *,
    scope: Location | None = None,  # caller pre-filters matches; kept for API symmetry
    keep_groups: bool = False,
    keep_rules: bool = False,
) -> ChangeSet:
    """Plan the reference-safe teardown of `targets` (resolved address objects).

    `keep_groups` scrubs rule/group fields but deletes neither groups nor the
    objects (a "loosen, don't remove" mode); `keep_rules` keeps an orphaned
    rule's emptied-field edit instead of deleting the rule. A blocked plan
    carries zero ops (the invariant every consumer relies on).
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

    # An edit accumulator keyed by referrer field identity, so multiple targets
    # hitting the same group/rule field merge into one edit with all removed.
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

    # -- PHASE 1 + 2: collect scrub edits for groups and rules ----------------
    # Walk every reference to every matched object once; bucket by referrer.
    for a in matched:
        drop = {a.name}
        for ref in graph.where_used("address", a.name, a.location):
            is_group_static = ref.referrer_kind == "address-group" and ref.field == "static"
            if is_group_static or ref.field in _SCRUB_FIELDS:
                edit = _edit_for(ref)
                edit.after = _remove_members(edit.after, drop)
            elif ref.field in _NESTED_ADDR_FIELDS:
                # A NAT translation field or PBF nexthop names the object but
                # has no flat member list to rewrite. Record the would-be edit
                # (members untouched) purely so the unmappable gate can see that
                # the teardown strands it — and turn that into a blocker.
                _edit_for(ref)
            # service/tag fields are explicitly out of scope.

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
            filter_tags = set(re.findall(r"'([^']+)'", ag.dynamic_filter))
            shared = target_tags & filter_tags
            if shared:
                tags = ", ".join(f"'{t}'" for t in sorted(shared))
                cs.blockers.append(
                    f"dynamic address-group '{ag.name}'@{ag.location.name} selects the "
                    f"decommissioned object(s) by tag {tags}; psc cannot auto-edit a DAG "
                    "filter — remove the tag(s) or the filter clause, then re-run"
                )

    # -- PHASE 3: orphan rule detection ---------------------------------------
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

    # Emit the accumulated scrub edits (groups + rules), groups first then rules
    # so the human plan reads in dependency order.
    group_edits = [e for e in edits.values() if e.referrer_kind == "address-group"]
    rule_edits = [e for e in edits.values() if e.referrer_kind != "address-group"]
    cs.reference_edits.extend(group_edits)
    cs.reference_edits.extend(rule_edits)

    # -- PHASE 4: delete groups emptied by the scrub --------------------------
    if not keep_groups:
        for edit in group_edits:
            if not edit.after:
                cs.deletes.append(
                    ObjectDelete(
                        kind=ObjectKind.ADDRESS_GROUP,
                        name=edit.referrer_name,
                        location=edit.referrer_location,
                    )
                )
    else:
        for edit in group_edits:
            if not edit.after:
                cs.warnings.append(
                    f"address-group '{edit.referrer_name}'@{edit.referrer_location} is now empty "
                    "(kept per --keep-groups) — it may dangle; delete it by hand if unused"
                )

    # -- PHASE 5: delete the objects themselves -------------------------------
    if not keep_groups:
        for a in matched:
            cs.deletes.append(
                ObjectDelete(kind=ObjectKind.ADDRESS, name=a.name, location=a.location.name)
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
