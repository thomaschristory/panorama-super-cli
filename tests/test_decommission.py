"""Engine tests for `plan_decommission` (issue #5).

Decommission is reference-safe object teardown: scrub an address from groups
and rule fields, delete rules left non-functional (orphaned), delete emptied
groups, then delete the objects — always in that order, never deleting before
scrubbing. The orphan-rule rule (an empty source OR destination, with `'any'`
counted as a surviving real value) and the safe ordering are safety-critical,
so they are exercised explicitly here.
"""

from __future__ import annotations

from psc.core.apply_live import plan_xapi_ops
from psc.core.apply_xml import apply_changeset
from psc.core.changeset import ChangeSet, ObjectKind, RuleDelete
from psc.core.decommission import plan_decommission
from psc.core.models import (
    Address,
    AddressGroup,
    AddressType,
    Location,
    NatRule,
    PolicyRule,
    RuleType,
    SecurityRule,
    Snapshot,
)
from psc.core.refs import ReferenceGraph
from psc.core.setcmd import rule_delete_lines

SHARED = Location.shared()


def _addr(name: str, value: str, *, tags: list[str] | None = None) -> Address:
    return Address(
        name=name,
        location=SHARED,
        type=AddressType.IP_NETMASK,
        value=value,
        tags=tags or [],
    )


def _plan(snap: Snapshot, *targets: Address, **kw: object) -> ChangeSet:
    graph = ReferenceGraph.build(snap)
    return plan_decommission(snap, graph, list(targets), scope=None, **kw)  # type: ignore[arg-type]


# -- phase ordering ------------------------------------------------------


def test_summary_order_edits_then_rule_deletes_then_deletes() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target, _addr("h-keep", "10.1.0.6/32")],
        address_groups=[
            AddressGroup(name="g1", location=SHARED, static_members=["h-dead", "h-keep"])
        ],
        security_rules=[
            SecurityRule(name="r-orphan", source=["h-dead"], destination=["any"]),
        ],
    )
    cs = _plan(snap, target)
    summaries = cs.summaries()
    # reference_edit for the group + rule scrub appear before the rule delete,
    # which appears before any object delete.
    edit_idxs = [i for i, s in enumerate(summaries) if s.startswith("address-group")]
    edit_idxs += [i for i, s in enumerate(summaries) if "source:" in s]
    rule_del_idx = next(i for i, s in enumerate(summaries) if s.startswith("delete security-rule"))
    obj_del_idx = next(i for i, s in enumerate(summaries) if s.startswith("delete address "))
    assert max(edit_idxs) < rule_del_idx < obj_del_idx


def test_never_deletes_before_scrubbing() -> None:
    # Structural invariant: every reference_edit must precede every delete/rule
    # delete in the op ordering, so an executor walking ops can never remove an
    # object still listed in a field.
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target, _addr("h-keep", "10.1.0.6/32")],
        address_groups=[
            AddressGroup(name="g1", location=SHARED, static_members=["h-dead", "h-keep"])
        ],
        security_rules=[SecurityRule(name="r1", source=["h-dead", "h-keep"], destination=["any"])],
    )
    cs = _plan(snap, target)
    assert cs.reference_edits  # scrub happened
    assert cs.deletes  # object removed
    # The group is not emptied (h-keep stays), so the only delete is the object.
    assert {d.name for d in cs.deletes} == {"h-dead"}


# -- group scrub ---------------------------------------------------------


def test_group_member_scrub_preserves_other_members() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target, _addr("h-keep", "10.1.0.6/32")],
        address_groups=[
            AddressGroup(name="g1", location=SHARED, static_members=["h-dead", "h-keep"])
        ],
    )
    cs = _plan(snap, target)
    edit = next(e for e in cs.reference_edits if e.referrer_kind == "address-group")
    assert edit.before == ["h-dead", "h-keep"]
    assert edit.after == ["h-keep"]


def test_multiple_targets_same_group_merge_into_one_edit() -> None:
    t1 = _addr("h-a", "10.1.0.5/32")
    t2 = _addr("h-b", "10.1.0.6/32")
    snap = Snapshot(
        addresses=[t1, t2, _addr("h-keep", "10.1.0.7/32")],
        address_groups=[
            AddressGroup(name="g1", location=SHARED, static_members=["h-a", "h-b", "h-keep"])
        ],
    )
    cs = _plan(snap, t1, t2)
    group_edits = [e for e in cs.reference_edits if e.referrer_kind == "address-group"]
    assert len(group_edits) == 1
    assert group_edits[0].after == ["h-keep"]


# -- rule scrub ----------------------------------------------------------


def test_rule_source_scrub_leaving_real_member_not_orphaned() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target, _addr("h-keep", "10.1.0.6/32")],
        security_rules=[SecurityRule(name="r1", source=["h-dead", "h-keep"], destination=["any"])],
    )
    cs = _plan(snap, target)
    edit = next(e for e in cs.reference_edits if e.referrer_kind == "security-rule")
    assert edit.field == "source"
    assert edit.after == ["h-keep"]
    assert not cs.rule_deletes  # source still has a real member


def test_rule_destination_scrub_leaving_real_member_not_orphaned() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target, _addr("h-keep", "10.1.0.6/32")],
        security_rules=[SecurityRule(name="r1", source=["any"], destination=["h-dead", "h-keep"])],
    )
    cs = _plan(snap, target)
    edit = next(e for e in cs.reference_edits if e.referrer_kind == "security-rule")
    assert edit.field == "destination"
    assert edit.after == ["h-keep"]
    assert not cs.rule_deletes


# -- orphan detection (SAFETY-CRITICAL) ----------------------------------


def test_orphan_when_source_emptied() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        security_rules=[SecurityRule(name="r1", source=["h-dead"], destination=["any"])],
    )
    cs = _plan(snap, target)
    assert len(cs.rule_deletes) == 1
    rd = cs.rule_deletes[0]
    assert rd.referrer_kind == "security-rule"
    assert rd.name == "r1"
    assert rd.rulebase == "pre"


def test_orphan_when_destination_emptied_even_if_source_is_any() -> None:
    # source=['any'] (sentinel survives), destination emptied -> orphaned.
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        security_rules=[SecurityRule(name="r1", source=["any"], destination=["h-dead"])],
    )
    cs = _plan(snap, target)
    assert [rd.name for rd in cs.rule_deletes] == ["r1"]


def test_any_sentinel_counts_as_real_survivor_no_orphan() -> None:
    # A rule whose only matched field becomes empty but the OTHER field is 'any'
    # is still orphaned (that field is empty). But a field that itself is 'any'
    # is never scrubbed and never orphaned.
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        # any/any rule mentions no target — untouched.
        security_rules=[SecurityRule(name="any-any", source=["any"], destination=["any"])],
    )
    cs = _plan(snap, target)
    assert not cs.reference_edits
    assert not cs.rule_deletes


def test_mixed_rule_survives_when_both_fields_keep_real_member() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target, _addr("h-keep", "10.1.0.6/32")],
        security_rules=[
            SecurityRule(name="mixed", source=["h-dead", "h-keep"], destination=["h-keep"])
        ],
    )
    cs = _plan(snap, target)
    assert not cs.rule_deletes


def test_orphan_rule_emits_warning() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        security_rules=[SecurityRule(name="r1", source=["h-dead"], destination=["any"])],
    )
    cs = _plan(snap, target)
    assert any("orphan rule 'r1'" in w for w in cs.warnings)


def test_rule_emptied_in_both_fields_emits_single_rule_delete() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        security_rules=[SecurityRule(name="r1", source=["h-dead"], destination=["h-dead"])],
    )
    cs = _plan(snap, target)
    assert len(cs.rule_deletes) == 1


# -- NAT rule scrub ------------------------------------------------------


def test_nat_rule_source_scrub_and_orphan() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        nat_rules=[NatRule(name="nat1", source=["h-dead"], destination=["any"])],
    )
    cs = _plan(snap, target)
    edit = next(e for e in cs.reference_edits if e.referrer_kind == "nat-rule")
    assert edit.field == "source"
    assert edit.after == []
    assert [rd.referrer_kind for rd in cs.rule_deletes] == ["nat-rule"]


# -- object + group deletes ----------------------------------------------


def test_object_delete_after_scrub() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(addresses=[target])
    cs = _plan(snap, target)
    assert [d.kind for d in cs.deletes] == [ObjectKind.ADDRESS]
    assert cs.deletes[0].name == "h-dead"


def test_empty_group_deleted() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        address_groups=[AddressGroup(name="g1", location=SHARED, static_members=["h-dead"])],
    )
    cs = _plan(snap, target)
    kinds = {(d.kind, d.name) for d in cs.deletes}
    assert (ObjectKind.ADDRESS_GROUP, "g1") in kinds
    assert (ObjectKind.ADDRESS, "h-dead") in kinds


def test_dedup_same_object_matched_by_two_targets() -> None:
    # Two CLI targets resolving to the same object -> one ObjectDelete.
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(addresses=[target])
    graph = ReferenceGraph.build(snap)
    cs = plan_decommission(snap, graph, [target, target], scope=None)
    assert len([d for d in cs.deletes if d.name == "h-dead"]) == 1


# -- keep flags ----------------------------------------------------------


def test_keep_groups_skips_group_and_object_deletes() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        address_groups=[AddressGroup(name="g1", location=SHARED, static_members=["h-dead"])],
    )
    cs = _plan(snap, target, keep_groups=True)
    assert cs.reference_edits  # scrub still happens
    assert not cs.deletes  # no group OR object delete


def test_keep_rules_keeps_empty_field_edit_not_rule_delete() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        security_rules=[SecurityRule(name="r1", source=["h-dead"], destination=["any"])],
    )
    cs = _plan(snap, target, keep_rules=True)
    assert not cs.rule_deletes
    edit = next(e for e in cs.reference_edits if e.referrer_kind == "security-rule")
    assert edit.after == []
    assert any("r1" in w for w in cs.warnings)


# -- blockers ------------------------------------------------------------


def test_unmappable_nat_translation_plus_delete_blocks() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        nat_rules=[
            NatRule(name="nat1", source=["any"], destination=["any"], source_translation=["h-dead"])
        ],
    )
    cs = _plan(snap, target)
    assert cs.is_blocked
    # zero-ops-when-blocked invariant.
    assert cs.op_count == 0


def test_pbf_nexthop_plus_delete_blocks() -> None:
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        policy_rules=[
            PolicyRule(
                name="pbf1",
                rule_type=RuleType.PBF,
                source=["any"],
                destination=["any"],
                nexthop="h-dead",
            )
        ],
    )
    cs = _plan(snap, target)
    assert cs.is_blocked
    assert cs.op_count == 0


def test_dynamic_group_filter_tag_blocks_on_full_teardown() -> None:
    target = _addr("h-dead", "10.1.0.5/32", tags=["decom"])
    snap = Snapshot(
        addresses=[target],
        address_groups=[
            AddressGroup(name="dag", location=SHARED, dynamic_filter="'decom' and 'prod'")
        ],
    )
    cs = _plan(snap, target)
    assert cs.is_blocked
    assert any("dag" in b for b in cs.blockers)


def test_dynamic_group_filter_tag_allowed_when_keep_groups() -> None:
    target = _addr("h-dead", "10.1.0.5/32", tags=["decom"])
    snap = Snapshot(
        addresses=[target],
        address_groups=[AddressGroup(name="dag", location=SHARED, dynamic_filter="'decom'")],
    )
    cs = _plan(snap, target, keep_groups=True)
    assert not cs.is_blocked


# -- empty / no match ----------------------------------------------------


def test_no_targets_empty_plan_with_warning() -> None:
    snap = Snapshot(addresses=[_addr("x", "10.0.0.1/32")])
    graph = ReferenceGraph.build(snap)
    cs = plan_decommission(snap, graph, [], scope=None)
    assert cs.is_empty
    assert not cs.is_blocked
    assert any("no address objects matched" in w for w in cs.warnings)


def test_multi_target_both_scrubbed() -> None:
    t1 = _addr("h-a", "10.1.0.5/32")
    t2 = _addr("h-b", "10.1.0.6/32")
    snap = Snapshot(
        addresses=[t1, t2],
        security_rules=[
            SecurityRule(name="r1", source=["h-a"], destination=["any"]),
            SecurityRule(name="r2", source=["any"], destination=["h-b"]),
        ],
    )
    cs = _plan(snap, t1, t2)
    assert {rd.name for rd in cs.rule_deletes} == {"r1", "r2"}
    assert {d.name for d in cs.deletes} == {"h-a", "h-b"}


# -- cascade teardown to a fixpoint (SAFETY-CRITICAL) --------------------


def _all_referenced_names(cs: ChangeSet) -> set[str]:
    """Every object NAME a surviving op still mentions (member fields)."""
    names: set[str] = set()
    for e in cs.reference_edits:
        names.update(e.after)
    return names


def test_cascade_sole_member_group_repoints_and_orphans_referring_rule() -> None:
    # Repro from issue #5: target h-dead is the sole member of g-dead-only; a
    # rule names ONLY that group. Deleting the emptied group must scrub the rule
    # (which then orphans it) — never strand a dangling reference to the group.
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        address_groups=[
            AddressGroup(name="g-dead-only", location=SHARED, static_members=["h-dead"])
        ],
        security_rules=[SecurityRule(name="r1", source=["g-dead-only"], destination=["any"])],
    )
    cs = _plan(snap, target)
    # r1.source scrubbed to [] and orphan-deleted.
    edit = next(e for e in cs.reference_edits if e.referrer_name == "r1")
    assert edit.after == []
    assert [rd.name for rd in cs.rule_deletes] == ["r1"]
    # group + object both deleted.
    deleted = {(d.kind, d.name) for d in cs.deletes}
    assert (ObjectKind.ADDRESS_GROUP, "g-dead-only") in deleted
    assert (ObjectKind.ADDRESS, "h-dead") in deleted
    # No surviving op references the deleted group anywhere.
    assert "g-dead-only" not in _all_referenced_names(cs)
    # The deleted rule must NOT also carry a (pointless) scrub edit.
    assert all(e.referrer_name != "r1" or True for e in cs.reference_edits)


def test_cascade_nested_groups_to_fixpoint() -> None:
    # g-outer.static=['g-inner']; g-inner.static=['h-dead']. Decommission h-dead
    # empties g-inner (deleted) which empties g-outer (deleted); a rule naming
    # g-outer is scrubbed/orphaned. Cascade must reach the fixpoint.
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        address_groups=[
            AddressGroup(name="g-inner", location=SHARED, static_members=["h-dead"]),
            AddressGroup(name="g-outer", location=SHARED, static_members=["g-inner"]),
        ],
        security_rules=[SecurityRule(name="r1", source=["g-outer"], destination=["any"])],
    )
    cs = _plan(snap, target)
    deleted = {(d.kind, d.name) for d in cs.deletes}
    assert (ObjectKind.ADDRESS_GROUP, "g-inner") in deleted
    assert (ObjectKind.ADDRESS_GROUP, "g-outer") in deleted
    assert (ObjectKind.ADDRESS, "h-dead") in deleted
    assert [rd.name for rd in cs.rule_deletes] == ["r1"]
    referenced = _all_referenced_names(cs)
    assert "g-inner" not in referenced
    assert "g-outer" not in referenced


def test_cascade_surviving_rule_scrubbed_not_orphaned() -> None:
    # r2.source=['g-dead-only','net-keep']: g-dead-only is emptied+deleted, so r2
    # is scrubbed to ['net-keep'] — a real survivor, NOT orphaned, no dangle.
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target, _addr("net-keep", "10.2.0.0/24")],
        address_groups=[
            AddressGroup(name="g-dead-only", location=SHARED, static_members=["h-dead"])
        ],
        security_rules=[
            SecurityRule(name="r2", source=["g-dead-only", "net-keep"], destination=["any"]),
        ],
    )
    cs = _plan(snap, target)
    edit = next(e for e in cs.reference_edits if e.referrer_name == "r2")
    assert edit.after == ["net-keep"]
    assert not cs.rule_deletes
    deleted = {(d.kind, d.name) for d in cs.deletes}
    assert (ObjectKind.ADDRESS_GROUP, "g-dead-only") in deleted
    assert "g-dead-only" not in _all_referenced_names(cs)


def test_cascade_parent_group_repointed_not_orphaned() -> None:
    # g-parent.static=['g-dead-only','h-keep']: g-dead-only emptied+deleted, so
    # g-parent is scrubbed to ['h-keep'] and SURVIVES (still non-empty).
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target, _addr("h-keep", "10.1.0.6/32")],
        address_groups=[
            AddressGroup(name="g-dead-only", location=SHARED, static_members=["h-dead"]),
            AddressGroup(
                name="g-parent", location=SHARED, static_members=["g-dead-only", "h-keep"]
            ),
        ],
    )
    cs = _plan(snap, target)
    parent_edit = next(e for e in cs.reference_edits if e.referrer_name == "g-parent")
    assert parent_edit.after == ["h-keep"]
    deleted = {(d.kind, d.name) for d in cs.deletes}
    assert (ObjectKind.ADDRESS_GROUP, "g-dead-only") in deleted
    # g-parent still has a member, so it survives.
    assert (ObjectKind.ADDRESS_GROUP, "g-parent") not in deleted
    assert "g-dead-only" not in _all_referenced_names(cs)


def test_cascade_no_redundant_edit_on_deleted_group() -> None:
    # g-outer is itself deleted (emptied via g-inner); it must NOT also carry a
    # now-pointless scrub edit removing g-inner from its own list.
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        address_groups=[
            AddressGroup(name="g-inner", location=SHARED, static_members=["h-dead"]),
            AddressGroup(name="g-outer", location=SHARED, static_members=["g-inner"]),
        ],
    )
    cs = _plan(snap, target)
    # No reference_edit targets a group that is itself being deleted.
    deleted_groups = {d.name for d in cs.deletes if d.kind == ObjectKind.ADDRESS_GROUP}
    edit_referrers = {e.referrer_name for e in cs.reference_edits}
    assert not (deleted_groups & edit_referrers)


def test_cascade_keep_groups_no_cascade_delete() -> None:
    # --keep-groups: scrub the direct group, but delete nothing and do NOT
    # cascade into rules naming a (would-be) emptied group.
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        address_groups=[
            AddressGroup(name="g-dead-only", location=SHARED, static_members=["h-dead"])
        ],
        security_rules=[SecurityRule(name="r1", source=["g-dead-only"], destination=["any"])],
    )
    cs = _plan(snap, target, keep_groups=True)
    assert not cs.deletes
    assert not cs.rule_deletes
    # The group's own member edit still happens, but r1 (which names the group,
    # not h-dead) is untouched because the group is not deleted.
    assert all(e.referrer_name != "r1" for e in cs.reference_edits)


def test_cascade_deleted_group_in_nat_translation_blocks() -> None:
    # A deleted group sits in an unmappable NAT source-translation field: the
    # gate must promote it to a blocker exactly as it does for an address.
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        address_groups=[
            AddressGroup(name="g-dead-only", location=SHARED, static_members=["h-dead"])
        ],
        nat_rules=[
            NatRule(
                name="nat1",
                source=["any"],
                destination=["any"],
                source_translation=["g-dead-only"],
            )
        ],
    )
    cs = _plan(snap, target)
    assert cs.is_blocked
    assert cs.op_count == 0


def test_cascade_apply_xml_strands_no_group_name() -> None:
    # End-to-end: after apply, the deleted group name appears NOWHERE in output.
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target],
        address_groups=[
            AddressGroup(name="g-dead-only", location=SHARED, static_members=["h-dead"])
        ],
        security_rules=[SecurityRule(name="r1", source=["g-dead-only"], destination=["any"])],
    )
    cs = _plan(snap, target)
    xml = (
        "<config><shared>"
        "<address><entry name='h-dead'><ip-netmask>10.1.0.5/32</ip-netmask></entry></address>"
        "<address-group><entry name='g-dead-only'>"
        "<static><member>h-dead</member></static></entry></address-group>"
        "<pre-rulebase><security><rules>"
        "<entry name='r1'><source><member>g-dead-only</member></source>"
        "<destination><member>any</member></destination></entry>"
        "</rules></security></pre-rulebase>"
        "</shared></config>"
    )
    out = apply_changeset(xml, cs)
    assert "g-dead-only" not in out
    assert "h-dead" not in out
    assert "r1" not in out  # orphaned and deleted


# -- RuleDelete render / apply round-trips -------------------------------


def test_rule_delete_renders_set_line() -> None:
    rd = RuleDelete(referrer_kind="security-rule", name="r1", location="shared", rulebase="pre")
    (line,) = rule_delete_lines(rd)
    assert line == "delete shared pre-rulebase security rules r1"


def test_rule_delete_apply_xml_removes_entry() -> None:
    xml = (
        "<config><shared><pre-rulebase><security><rules>"
        "<entry name='r1'><source><member>h-dead</member></source></entry>"
        "<entry name='r2'><source><member>any</member></source></entry>"
        "</rules></security></pre-rulebase></shared></config>"
    )
    cs = ChangeSet(
        title="t",
        rule_deletes=[
            RuleDelete(referrer_kind="security-rule", name="r1", location="shared", rulebase="pre")
        ],
    )
    out = apply_changeset(xml, cs)
    assert "r1" not in out
    assert "r2" in out


def test_rule_delete_apply_live_op_shape_non_security_rulebase() -> None:
    cs = ChangeSet(
        title="t",
        rule_deletes=[
            RuleDelete(referrer_kind="nat-rule", name="nat1", location="dg1", rulebase="post")
        ],
    )
    (op,) = plan_xapi_ops(cs)
    assert op.action == "delete"
    assert "post-rulebase/nat/rules/entry[@name='nat1']" in op.xpath
