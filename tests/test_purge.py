"""Engine tests for `plan_purge` (issue #181).

`plan_purge` deletes objects by name. It accepts a `(kind, name, location)`
target of any of the five kinds. It is the engine behind `psc delete` and
behind the workbench delete spoke.

The safety rules are the same as `decommission`: scrub every reference before
you delete the referent, cascade to a fixpoint, delete a rule that loses a
required field, and block instead of doing something surprising. These tests
pin those rules.
"""

from __future__ import annotations

import pytest

from psc.core.apply_xml import apply_changeset
from psc.core.changeset import ChangeSet, ObjectKind
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
    Service,
    ServiceGroup,
    Snapshot,
    Tag,
)
from psc.core.parse import parse_config
from psc.core.purge import PURGE_KINDS, plan_purge
from psc.core.refs import ReferenceGraph, Target

SHARED = Location.shared()
DG = Location.dg("dg-edge")


def _addr(name: str, value: str, *, loc: Location = SHARED, tags: list[str] | None = None):
    return Address(
        name=name, location=loc, type=AddressType.IP_NETMASK, value=value, tags=tags or []
    )


def _svc(name: str, port: str, *, loc: Location = SHARED) -> Service:
    return Service(name=name, location=loc, protocol="tcp", destination_port=port)


def _t(kind: str, name: str, loc: Location = SHARED) -> Target:
    return Target(kind=kind, name=name, location=loc)


def _plan(snap: Snapshot, *targets: Target, **kw: object) -> ChangeSet:
    graph = ReferenceGraph.build(snap)
    return plan_purge(snap, graph, list(targets), **kw)  # type: ignore[arg-type]


def _deleted(cs: ChangeSet) -> set[tuple[str, str, str]]:
    return {(d.kind.value, d.name, d.location) for d in cs.deletes}


# -- the five kinds ------------------------------------------------------


def test_purge_kinds_covers_every_unused_kind() -> None:
    assert PURGE_KINDS == ("address", "address-group", "service", "service-group", "tag")


def test_deletes_a_lone_address() -> None:
    snap = Snapshot(addresses=[_addr("h-dead", "10.1.0.5/32"), _addr("h-keep", "10.1.0.6/32")])
    cs = _plan(snap, _t("address", "h-dead"))
    assert _deleted(cs) == {("address", "h-dead", "shared")}
    assert not cs.blockers


def test_deletes_a_lone_service() -> None:
    snap = Snapshot(services=[_svc("tcp-8443", "8443"), _svc("tcp-80", "80")])
    cs = _plan(snap, _t("service", "tcp-8443"))
    assert _deleted(cs) == {("service", "tcp-8443", "shared")}


def test_deletes_a_lone_tag() -> None:
    snap = Snapshot(tags=[Tag(name="t-dead", location=SHARED)])
    cs = _plan(snap, _t("tag", "t-dead"))
    assert _deleted(cs) == {("tag", "t-dead", "shared")}
    assert not cs.blockers


def test_deletes_an_address_group_and_scrubs_its_parent() -> None:
    snap = Snapshot(
        addresses=[_addr("h1", "10.0.0.1/32")],
        address_groups=[
            AddressGroup(name="g-dead", location=SHARED, static_members=["h1"]),
            AddressGroup(name="g-parent", location=SHARED, static_members=["g-dead", "h1"]),
        ],
    )
    cs = _plan(snap, _t("address-group", "g-dead"))
    assert _deleted(cs) == {("address-group", "g-dead", "shared")}
    edit = next(e for e in cs.reference_edits if e.referrer_name == "g-parent")
    assert edit.before == ["g-dead", "h1"]
    assert edit.after == ["h1"]


def test_deletes_a_service_group_and_scrubs_its_parent() -> None:
    snap = Snapshot(
        services=[_svc("tcp-80", "80")],
        service_groups=[
            ServiceGroup(name="sg-dead", location=SHARED, members=["tcp-80"]),
            ServiceGroup(name="sg-parent", location=SHARED, members=["sg-dead", "tcp-80"]),
        ],
    )
    cs = _plan(snap, _t("service-group", "sg-dead"))
    assert _deleted(cs) == {("service-group", "sg-dead", "shared")}
    edit = next(e for e in cs.reference_edits if e.referrer_name == "sg-parent")
    assert edit.after == ["tcp-80"]


# -- the cascade ---------------------------------------------------------


def test_emptied_address_group_cascades_into_the_delete_set() -> None:
    snap = Snapshot(
        addresses=[_addr("h-dead", "10.1.0.5/32")],
        address_groups=[AddressGroup(name="g-only", location=SHARED, static_members=["h-dead"])],
    )
    cs = _plan(snap, _t("address", "h-dead"))
    assert _deleted(cs) == {
        ("address", "h-dead", "shared"),
        ("address-group", "g-only", "shared"),
    }


def test_emptied_service_group_cascades_into_the_delete_set() -> None:
    snap = Snapshot(
        services=[_svc("tcp-8443", "8443")],
        service_groups=[ServiceGroup(name="sg-only", location=SHARED, members=["tcp-8443"])],
    )
    cs = _plan(snap, _t("service", "tcp-8443"))
    assert _deleted(cs) == {
        ("service", "tcp-8443", "shared"),
        ("service-group", "sg-only", "shared"),
    }


def test_cascade_reaches_a_fixpoint_through_nested_groups() -> None:
    snap = Snapshot(
        addresses=[_addr("h-dead", "10.1.0.5/32")],
        address_groups=[
            AddressGroup(name="g-leaf", location=SHARED, static_members=["h-dead"]),
            AddressGroup(name="g-mid", location=SHARED, static_members=["g-leaf"]),
            AddressGroup(name="g-top", location=SHARED, static_members=["g-mid"]),
        ],
    )
    cs = _plan(snap, _t("address", "h-dead"))
    assert _deleted(cs) == {
        ("address", "h-dead", "shared"),
        ("address-group", "g-leaf", "shared"),
        ("address-group", "g-mid", "shared"),
        ("address-group", "g-top", "shared"),
    }


def test_keep_groups_scrubs_but_deletes_nothing() -> None:
    snap = Snapshot(
        addresses=[_addr("h-dead", "10.1.0.5/32")],
        address_groups=[AddressGroup(name="g-only", location=SHARED, static_members=["h-dead"])],
    )
    cs = _plan(snap, _t("address", "h-dead"), keep_groups=True)
    assert not cs.deletes
    assert cs.reference_edits and cs.reference_edits[0].after == []


# -- orphan rules --------------------------------------------------------


def test_rule_that_loses_its_last_source_is_deleted() -> None:
    snap = Snapshot(
        addresses=[_addr("h-dead", "10.1.0.5/32")],
        security_rules=[SecurityRule(name="r-sole", source=["h-dead"], destination=["any"])],
    )
    cs = _plan(snap, _t("address", "h-dead"))
    assert [(r.name, r.location, r.rulebase) for r in cs.rule_deletes] == [
        ("r-sole", "shared", "pre")
    ]
    assert any("orphan rule" in w for w in cs.warnings)


def test_rule_that_loses_its_last_service_is_deleted() -> None:
    snap = Snapshot(
        services=[_svc("tcp-8443", "8443")],
        security_rules=[
            SecurityRule(name="r-svc", source=["any"], destination=["any"], service=["tcp-8443"])
        ],
    )
    cs = _plan(snap, _t("service", "tcp-8443"))
    assert [r.name for r in cs.rule_deletes] == ["r-svc"]


def test_any_survives_and_keeps_the_rule() -> None:
    snap = Snapshot(
        addresses=[_addr("h-dead", "10.1.0.5/32")],
        security_rules=[
            SecurityRule(name="r-mixed", source=["h-dead", "any"], destination=["any"])
        ],
    )
    cs = _plan(snap, _t("address", "h-dead"))
    assert not cs.rule_deletes


def test_an_emptied_rule_tag_never_orphans_the_rule() -> None:
    snap = Snapshot(
        tags=[Tag(name="t-dead", location=SHARED)],
        security_rules=[
            SecurityRule(name="r-tagged", source=["any"], destination=["any"], tags=["t-dead"])
        ],
    )
    cs = _plan(snap, _t("tag", "t-dead"))
    assert not cs.rule_deletes
    edit = next(e for e in cs.reference_edits if e.referrer_name == "r-tagged")
    assert edit.field == "tag"
    assert edit.after == []


def test_keep_rules_keeps_the_orphan_and_warns() -> None:
    snap = Snapshot(
        addresses=[_addr("h-dead", "10.1.0.5/32")],
        security_rules=[SecurityRule(name="r-sole", source=["h-dead"], destination=["any"])],
    )
    cs = _plan(snap, _t("address", "h-dead"), keep_rules=True)
    assert not cs.rule_deletes
    assert any("kept per --keep-rules" in w for w in cs.warnings)


# -- blockers ------------------------------------------------------------


def test_missing_target_blocks() -> None:
    snap = Snapshot(addresses=[_addr("h-keep", "10.1.0.6/32")])
    cs = _plan(snap, _t("address", "h-ghost"))
    assert cs.is_blocked
    assert any("h-ghost" in b for b in cs.blockers)


def test_target_at_the_wrong_location_blocks() -> None:
    snap = Snapshot(addresses=[_addr("h1", "10.0.0.1/32", loc=DG)], device_groups=["dg-edge"])
    cs = _plan(snap, _t("address", "h1", SHARED))
    assert cs.is_blocked


def test_a_blocked_plan_carries_zero_ops() -> None:
    snap = Snapshot(
        addresses=[_addr("h-dead", "10.1.0.5/32")],
        security_rules=[SecurityRule(name="r-sole", source=["h-dead"], destination=["any"])],
    )
    cs = _plan(snap, _t("address", "h-dead"), _t("address", "h-ghost"))
    assert cs.is_blocked
    assert cs.op_count == 0


def test_surviving_dag_that_selects_the_object_by_tag_blocks() -> None:
    snap = Snapshot(
        addresses=[_addr("h-dead", "10.1.0.5/32", tags=["web"])],
        address_groups=[AddressGroup(name="dag-web", location=SHARED, dynamic_filter="'web'")],
        tags=[Tag(name="web", location=SHARED)],
    )
    cs = _plan(snap, _t("address", "h-dead"))
    assert cs.is_blocked
    assert any("dag-web" in b for b in cs.blockers)


def test_dag_deleted_in_the_same_plan_does_not_block() -> None:
    snap = Snapshot(
        addresses=[_addr("h-dead", "10.1.0.5/32", tags=["web"])],
        address_groups=[AddressGroup(name="dag-web", location=SHARED, dynamic_filter="'web'")],
        tags=[Tag(name="web", location=SHARED)],
    )
    cs = _plan(snap, _t("address", "h-dead"), _t("address-group", "dag-web"))
    assert not cs.is_blocked, cs.blockers
    assert _deleted(cs) == {
        ("address", "h-dead", "shared"),
        ("address-group", "dag-web", "shared"),
    }


def test_deleting_a_tag_a_surviving_dag_filters_on_blocks() -> None:
    snap = Snapshot(
        tags=[Tag(name="web", location=SHARED)],
        address_groups=[AddressGroup(name="dag-web", location=SHARED, dynamic_filter="'web'")],
    )
    cs = _plan(snap, _t("tag", "web"))
    assert cs.is_blocked
    assert any("dag-web" in b for b in cs.blockers)


def test_nat_translation_reference_blocks() -> None:
    snap = Snapshot(
        addresses=[_addr("h-nat", "10.9.9.9/32")],
        nat_rules=[
            NatRule(
                name="n1",
                source=["any"],
                destination=["any"],
                destination_translation="h-nat",
            )
        ],
    )
    cs = _plan(snap, _t("address", "h-nat"))
    assert cs.is_blocked
    assert cs.op_count == 0


def test_deleting_a_tag_a_surviving_address_carries_blocks() -> None:
    """psc cannot rewrite an object's own tag list, so it refuses instead."""
    snap = Snapshot(
        addresses=[_addr("h-keep", "10.0.0.1/32", tags=["web"])],
        tags=[Tag(name="web", location=SHARED)],
    )
    cs = _plan(snap, _t("tag", "web"))
    assert cs.is_blocked
    assert cs.op_count == 0


# -- warnings ------------------------------------------------------------


def test_shared_candidate_warns() -> None:
    snap = Snapshot(addresses=[_addr("h-dead", "10.1.0.5/32")])
    cs = _plan(snap, _t("address", "h-dead"))
    assert any("shared" in w for w in cs.warnings)


def test_tagged_candidate_warns_about_runtime_dag_membership() -> None:
    snap = Snapshot(
        addresses=[_addr("h-dead", "10.1.0.5/32", loc=DG, tags=["web"])],
        tags=[Tag(name="web", location=SHARED)],
        device_groups=["dg-edge"],
    )
    cs = _plan(snap, _t("address", "h-dead", DG))
    assert any("web" in w and "dynamic address-group" in w for w in cs.warnings)


def test_untagged_device_group_candidate_warns_about_nothing() -> None:
    snap = Snapshot(addresses=[_addr("h-dead", "10.1.0.5/32", loc=DG)], device_groups=["dg-edge"])
    cs = _plan(snap, _t("address", "h-dead", DG))
    assert cs.warnings == []


def test_no_targets_warns_and_plans_nothing() -> None:
    cs = _plan(Snapshot())
    assert cs.is_empty
    assert not cs.is_blocked
    assert cs.warnings


# -- parity and round-trip ----------------------------------------------


@pytest.mark.parametrize("keep_rules", [False, True])
def test_address_only_plan_matches_decommission(keep_rules: bool) -> None:
    """For addresses the generic planner must agree with the IP-based one."""
    target = _addr("h-dead", "10.1.0.5/32")
    snap = Snapshot(
        addresses=[target, _addr("h-keep", "10.1.0.6/32")],
        address_groups=[
            AddressGroup(name="g-only", location=SHARED, static_members=["h-dead"]),
            AddressGroup(name="g-mixed", location=SHARED, static_members=["h-dead", "h-keep"]),
        ],
        security_rules=[
            SecurityRule(name="r-sole", source=["h-dead"], destination=["any"]),
            SecurityRule(name="r-mixed", source=["h-dead", "h-keep"], destination=["any"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    dec = plan_decommission(snap, graph, [target], keep_rules=keep_rules)
    pur = plan_purge(snap, graph, [_t("address", "h-dead")], keep_rules=keep_rules)
    assert {d.summary for d in pur.deletes} == {d.summary for d in dec.deletes}
    assert {r.summary for r in pur.rule_deletes} == {r.summary for r in dec.rule_deletes}
    assert {e.summary for e in pur.reference_edits} == {e.summary for e in dec.reference_edits}


def test_plan_applies_to_config_xml() -> None:
    xml = """<config><shared>
      <address>
        <entry name="h-dead"><ip-netmask>10.1.0.5/32</ip-netmask></entry>
        <entry name="h-keep"><ip-netmask>10.1.0.6/32</ip-netmask></entry>
      </address>
      <address-group>
        <entry name="g-only"><static><member>h-dead</member></static></entry>
      </address-group>
      <service>
        <entry name="tcp-8443"><protocol><tcp><port>8443</port></tcp></protocol></entry>
      </service>
      <service-group>
        <entry name="sg-only"><members><member>tcp-8443</member></members></entry>
      </service-group>
    </shared></config>"""
    snap = parse_config(xml)
    graph = ReferenceGraph.build(snap)
    cs = plan_purge(snap, graph, [_t("address", "h-dead"), _t("service", "tcp-8443")])
    out = apply_changeset(xml, cs)
    assert "h-dead" not in out
    assert "g-only" not in out
    assert "tcp-8443" not in out
    assert "sg-only" not in out
    assert "h-keep" in out


def test_delete_kinds_are_object_kinds() -> None:
    snap = Snapshot(
        addresses=[_addr("h1", "10.0.0.1/32")],
        services=[_svc("tcp-80", "80")],
        tags=[Tag(name="t1", location=SHARED)],
    )
    cs = _plan(snap, _t("address", "h1"), _t("service", "tcp-80"), _t("tag", "t1"))
    assert {d.kind for d in cs.deletes} == {
        ObjectKind.ADDRESS,
        ObjectKind.SERVICE,
        ObjectKind.TAG,
    }


# -- PAN-OS shadowing ----------------------------------------------------


def _shadow_snapshot() -> Snapshot:
    """`web` defined in shared AND in dg-edge, with a dg-local group and rule."""
    return Snapshot(
        addresses=[_addr("web", "10.0.0.1/32"), _addr("web", "10.9.9.9/32", loc=DG)],
        address_groups=[AddressGroup(name="g", location=DG, static_members=["web"])],
        security_rules=[SecurityRule(name="r1", location=DG, source=["g"], destination=["any"])],
        device_groups=["dg-edge"],
    )


def test_deleting_a_shadow_leaves_the_group_and_rule_alone() -> None:
    """The member falls through to the shared object, so nothing else breaks."""
    cs = _plan(_shadow_snapshot(), _t("address", "web", DG))
    assert _deleted(cs) == {("address", "web", "dg-edge")}
    assert not cs.rule_deletes
    assert not cs.reference_edits


def test_deleting_both_shadow_and_shared_cascades() -> None:
    snap = _shadow_snapshot()
    cs = _plan(snap, _t("address", "web", DG), _t("address", "web", SHARED))
    assert ("address-group", "g", "dg-edge") in _deleted(cs)
    assert [r.name for r in cs.rule_deletes] == ["r1"]


def test_deleting_only_the_shared_object_keeps_the_shadowed_group() -> None:
    """`g`'s member resolves to the dg-local `web`, which survives."""
    cs = _plan(_shadow_snapshot(), _t("address", "web", SHARED))
    assert _deleted(cs) == {("address", "web", "shared")}
    assert not cs.reference_edits


def test_a_dangling_member_never_empties_its_group() -> None:
    snap = Snapshot(
        addresses=[_addr("h1", "10.0.0.1/32")],
        address_groups=[AddressGroup(name="g", location=SHARED, static_members=["h1", "ghost"])],
    )
    cs = _plan(snap, _t("address", "h1"))
    assert _deleted(cs) == {("address", "h1", "shared")}
    assert cs.reference_edits[0].after == ["ghost"]


# -- an object's own tag list -------------------------------------------


def test_deleting_a_tag_an_address_group_carries_blocks() -> None:
    """`field_members` cannot read a tag list, so an edit here would corrupt it."""
    snap = Snapshot(
        tags=[Tag(name="web", location=SHARED)],
        addresses=[_addr("h1", "10.0.0.1/32"), _addr("h2", "10.0.0.2/32")],
        address_groups=[
            AddressGroup(name="g", location=SHARED, static_members=["h1", "h2"], tags=["web"])
        ],
    )
    cs = _plan(snap, _t("tag", "web"))
    assert cs.is_blocked
    assert cs.op_count == 0
    assert any("carries tag 'web'" in b for b in cs.blockers)


def test_a_tag_edit_never_reaches_the_plan() -> None:
    """A tag edit on a group would be applied to its member list — never emit one."""
    snap = Snapshot(
        tags=[Tag(name="web", location=SHARED)],
        addresses=[_addr("h1", "10.0.0.1/32"), _addr("h2", "10.0.0.2/32")],
        address_groups=[
            AddressGroup(name="g", location=SHARED, static_members=["h1", "h2"], tags=["web"])
        ],
    )
    cs = _plan(snap, _t("address", "h1"), _t("tag", "web"))
    assert cs.is_blocked
    assert cs.op_count == 0
    assert not [e for e in cs.reference_edits if e.field == "tag"]


def test_deleting_a_tag_with_its_only_carrier_does_not_block() -> None:
    snap = Snapshot(
        tags=[Tag(name="web", location=SHARED)],
        addresses=[_addr("h1", "10.0.0.1/32", tags=["web"])],
    )
    cs = _plan(snap, _t("address", "h1"), _t("tag", "web"))
    assert not cs.is_blocked, cs.blockers
    assert _deleted(cs) == {("address", "h1", "shared"), ("tag", "web", "shared")}


def test_deleting_a_tag_with_a_group_carrier_in_the_same_plan_does_not_block() -> None:
    snap = Snapshot(
        tags=[Tag(name="web", location=SHARED)],
        addresses=[_addr("h1", "10.0.0.1/32")],
        address_groups=[
            AddressGroup(name="g", location=SHARED, static_members=["h1"], tags=["web"])
        ],
    )
    cs = _plan(snap, _t("address-group", "g"), _t("tag", "web"))
    assert not cs.is_blocked, cs.blockers


# -- rulebase coverage ---------------------------------------------------


def test_pbf_nexthop_reference_blocks() -> None:
    snap = Snapshot(
        addresses=[_addr("h-hop", "10.0.0.1/32")],
        policy_rules=[
            PolicyRule(
                name="p1",
                rule_type=RuleType.PBF,
                source=["any"],
                destination=["any"],
                nexthop="h-hop",
            )
        ],
    )
    cs = _plan(snap, _t("address", "h-hop"))
    assert cs.is_blocked
    assert cs.op_count == 0


def test_nat_source_and_destination_are_scrubbed() -> None:
    snap = Snapshot(
        addresses=[_addr("h-dead", "10.0.0.1/32"), _addr("h-keep", "10.0.0.2/32")],
        nat_rules=[NatRule(name="n1", source=["h-dead", "h-keep"], destination=["h-keep"])],
    )
    cs = _plan(snap, _t("address", "h-dead"))
    assert not cs.is_blocked, cs.blockers
    edit = next(e for e in cs.reference_edits if e.referrer_name == "n1")
    assert edit.after == ["h-keep"]


def test_nat_scalar_service_reference_blocks() -> None:
    """`NatRule.service` is a scalar, so psc cannot rewrite it as a member list."""
    snap = Snapshot(
        services=[_svc("tcp-8443", "8443")],
        nat_rules=[NatRule(name="n1", source=["any"], destination=["any"], service="tcp-8443")],
    )
    cs = _plan(snap, _t("service", "tcp-8443"))
    assert cs.is_blocked
    assert cs.op_count == 0


def test_a_rule_that_loses_its_last_destination_is_deleted() -> None:
    snap = Snapshot(
        addresses=[_addr("h-dead", "10.0.0.1/32")],
        security_rules=[SecurityRule(name="r-dst", source=["any"], destination=["h-dead"])],
    )
    cs = _plan(snap, _t("address", "h-dead"))
    assert [r.name for r in cs.rule_deletes] == ["r-dst"]


def test_a_service_group_cascade_orphans_the_rule_that_uses_it() -> None:
    snap = Snapshot(
        services=[_svc("tcp-8443", "8443")],
        service_groups=[ServiceGroup(name="svcgrp", location=SHARED, members=["tcp-8443"])],
        security_rules=[
            SecurityRule(name="r-svc", source=["any"], destination=["any"], service=["svcgrp"])
        ],
    )
    cs = _plan(snap, _t("service", "tcp-8443"))
    assert ("service-group", "svcgrp", "shared") in _deleted(cs)
    assert [r.name for r in cs.rule_deletes] == ["r-svc"]


# -- plan shape ----------------------------------------------------------


def test_a_carrier_is_deleted_before_the_tag_it_carries() -> None:
    """The applier walks `deletes` in order, so a tag must come last."""
    snap = Snapshot(
        addresses=[_addr("h1", "10.0.0.1/32", tags=["web"])],
        tags=[Tag(name="web", location=SHARED)],
    )
    cs = _plan(snap, _t("tag", "web"), _t("address", "h1"))
    assert [(d.kind.value, d.name) for d in cs.deletes] == [("address", "h1"), ("tag", "web")]


def test_a_repeated_target_yields_one_delete() -> None:
    snap = Snapshot(addresses=[_addr("h1", "10.0.0.1/32")])
    cs = _plan(snap, _t("address", "h1"), _t("address", "h1"))
    assert len(cs.deletes) == 1


def test_an_undeletable_kind_blocks() -> None:
    snap = Snapshot(addresses=[_addr("h1", "10.0.0.1/32")])
    cs = _plan(snap, _t("security-rule", "r1"))
    assert cs.is_blocked
    assert any("not a deletable kind" in b for b in cs.blockers)


def test_keep_groups_warns_that_an_emptied_group_may_dangle() -> None:
    snap = Snapshot(
        addresses=[_addr("h-dead", "10.0.0.1/32")],
        address_groups=[AddressGroup(name="g-only", location=SHARED, static_members=["h-dead"])],
    )
    cs = _plan(snap, _t("address", "h-dead"), keep_groups=True)
    assert any("keep-groups" in w for w in cs.warnings)


# -- the shadow fall-through is reported (#187) --------------------------


def test_shadow_fall_through_warns_about_the_survivor() -> None:
    """A kept reference now names another object, so the plan must say so."""
    cs = _plan(_shadow_snapshot(), _t("address", "web", DG))
    assert any(
        "address-group 'g'@dg-edge static" in w and "address 'web'@shared (10.0.0.1/32)" in w
        for w in cs.warnings
    )


def test_shadow_fall_through_warning_names_the_new_value() -> None:
    """The hazard is the value change: the rule keeps matching another host."""
    cs = _plan(_shadow_snapshot(), _t("address", "web", DG))
    note = next(w for w in cs.warnings if "points to" in w)
    assert "10.0.0.1/32" in note
    assert "10.9.9.9/32" not in note


def test_a_real_break_carries_no_fall_through_warning() -> None:
    snap = _shadow_snapshot()
    cs = _plan(snap, _t("address", "web", DG), _t("address", "web", SHARED))
    assert not [w for w in cs.warnings if "points to" in w]


def test_shadowed_nat_translation_does_not_block() -> None:
    """A nested field is not stranded when the name still resolves."""
    snap = Snapshot(
        addresses=[_addr("web", "10.0.0.1/32"), _addr("web", "10.9.9.9/32", loc=DG)],
        nat_rules=[NatRule(name="n1", location=DG, source_translation=["web"])],
        device_groups=["dg-edge"],
    )
    cs = _plan(snap, _t("address", "web", DG))
    assert not cs.is_blocked, cs.blockers
    assert _deleted(cs) == {("address", "web", "dg-edge")}


def test_shadowed_tag_carrier_does_not_block() -> None:
    """The carrier keeps a tag name that still resolves, so nothing is stranded."""
    snap = Snapshot(
        tags=[Tag(name="web", location=SHARED), Tag(name="web", location=DG)],
        addresses=[_addr("h1", "10.0.0.1/32", loc=DG, tags=["web"])],
        device_groups=["dg-edge"],
    )
    cs = _plan(snap, _t("tag", "web", DG))
    assert not cs.is_blocked, cs.blockers
    assert _deleted(cs) == {("tag", "web", "dg-edge")}


def test_shadowed_group_member_inside_a_nested_group() -> None:
    """An outer group names an inner group whose name is shadowed."""
    dg = Location.dg("dg-a")
    snap = Snapshot(
        addresses=[_addr("web", "10.9.9.9/32", loc=dg), _addr("h-shared", "10.0.0.7/32")],
        address_groups=[
            AddressGroup(name="inner", location=dg, static_members=["web"]),
            AddressGroup(name="inner", location=SHARED, static_members=["h-shared"]),
            AddressGroup(name="outer", location=dg, static_members=["inner"]),
        ],
        device_groups=["dg-a"],
    )
    cs = _plan(snap, _t("address", "web", dg))
    assert ("address-group", "inner", "dg-a") in _deleted(cs)
    assert ("address-group", "outer", "dg-a") not in _deleted(cs)
    assert not [e for e in cs.reference_edits if e.referrer_name == "outer"]


def test_shadow_plan_matches_decommission() -> None:
    """The two engines must agree on a shadowed address, ops and warnings."""
    snap = _shadow_snapshot()
    graph = ReferenceGraph.build(snap)
    target = next(a for a in snap.addresses if a.location == DG)
    dec = plan_decommission(snap, graph, [target])
    pur = plan_purge(snap, graph, [_t("address", "web", DG)])
    assert {d.summary for d in pur.deletes} == {d.summary for d in dec.deletes}
    assert {r.summary for r in pur.rule_deletes} == {r.summary for r in dec.rule_deletes}
    assert {e.summary for e in pur.reference_edits} == {e.summary for e in dec.reference_edits}
    assert not pur.rule_deletes
    assert not pur.reference_edits
    fall_through = [w for w in pur.warnings if "points to" in w]
    assert fall_through == [w for w in dec.warnings if "points to" in w]
    assert fall_through


# -- `--keep-groups` deletes nothing, so the shadow gate does not apply ---


def test_keep_groups_scrubs_a_shadowed_member() -> None:
    """`--keep-groups` deletes nothing, so no name falls through and the scrub runs."""
    cs = _plan(_shadow_snapshot(), _t("address", "web", DG), keep_groups=True)
    assert [(e.referrer_name, e.before, e.after) for e in cs.reference_edits] == [
        ("g", ["web"], [])
    ]
    assert cs.deletes == []
    assert not [w for w in cs.warnings if "points to" in w]


def test_keep_groups_still_lets_a_shadowed_tag_carrier_pass() -> None:
    """The gate still answers the blocker paths: nothing here is stranded."""
    snap = Snapshot(
        tags=[Tag(name="web", location=SHARED), Tag(name="web", location=DG)],
        addresses=[_addr("h1", "10.0.0.1/32", loc=DG, tags=["web"])],
        device_groups=["dg-edge"],
    )
    cs = _plan(snap, _t("tag", "web", DG), keep_groups=True)
    assert not cs.is_blocked, cs.blockers
    assert cs.deletes == []


def test_keep_groups_never_turns_a_dag_edge_into_an_edit() -> None:
    """A `dynamic` edge has no member list, so it must never reach the plan."""
    snap = Snapshot(
        tags=[Tag(name="prod", location=SHARED)],
        addresses=[_addr("h1", "10.0.0.1/32", tags=["prod"])],
        address_groups=[AddressGroup(name="dag", location=SHARED, dynamic_filter="'prod'")],
    )
    cs = _plan(snap, _t("address", "h1"), keep_groups=True)
    assert cs.reference_edits == []
    assert cs.op_count == 0


def test_no_fall_through_warning_for_a_rule_the_plan_deletes() -> None:
    """A deleted referrer keeps no name, so the plan must not warn about one."""
    snap = Snapshot(
        addresses=[
            _addr("web", "10.0.0.1/32"),
            _addr("web", "10.9.9.9/32", loc=DG),
            _addr("doomed", "10.9.9.10/32", loc=DG),
        ],
        security_rules=[
            SecurityRule(name="r1", location=DG, source=["doomed"], destination=["web"])
        ],
        device_groups=["dg-edge"],
    )
    cs = _plan(snap, _t("address", "web", DG), _t("address", "doomed", DG))
    assert [(r.name, r.location) for r in cs.rule_deletes] == [("r1", "dg-edge")]
    assert not [w for w in cs.warnings if "points to" in w]


# -- the cascade fixpoint really iterates --------------------------------


def test_cascade_reaches_the_fixpoint_with_the_outer_group_listed_first() -> None:
    """A single pass over the snapshot order is not enough, so the loop must repeat.

    `_newly_empty_groups` scans the snapshot list in order. With the outer group
    first, pass 1 can only find `inner`, pass 2 finds `mid`, and pass 3 finds
    `outer`. A one-pass engine leaves two live empty groups and no orphan rule.
    """
    snap = Snapshot(
        addresses=[_addr("h1", "10.1.0.5/32")],
        address_groups=[
            AddressGroup(name="outer", location=SHARED, static_members=["mid"]),
            AddressGroup(name="mid", location=SHARED, static_members=["inner"]),
            AddressGroup(name="inner", location=SHARED, static_members=["h1"]),
        ],
        security_rules=[SecurityRule(name="r1", source=["outer"], destination=["any"])],
    )
    cs = _plan(snap, _t("address", "h1"))
    assert _deleted(cs) == {
        ("address", "h1", "shared"),
        ("address-group", "inner", "shared"),
        ("address-group", "mid", "shared"),
        ("address-group", "outer", "shared"),
    }
    assert [(r.name, r.location) for r in cs.rule_deletes] == [("r1", "shared")]
