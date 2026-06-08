"""Reference-safe promotion of an object toward shared (issue #74).

Hierarchy used throughout (child → parent):

    shared
    ├── EMEA
    │   └── EMEA-DC
    └── APAC          (sibling of EMEA — never sees EMEA/EMEA-DC objects)

`move` only promotes *toward* shared (shared or a strict ancestor of the
source), which is the direction where references fall through by ordinary
PAN-OS shadowing and never need repointing. Everything else is a blocker.
"""

from __future__ import annotations

from psc.core.changeset import ChangeSet, ObjectKind
from psc.core.models import (
    Address,
    AddressGroup,
    AddressType,
    Location,
    SecurityRule,
    Service,
    Snapshot,
    Tag,
)
from psc.core.refs import ReferenceGraph
from psc.core.relocate import plan_move

EMEA = "EMEA"
DC = "EMEA-DC"
APAC = "APAC"


def _snap(**kw: object) -> Snapshot:
    base: dict[str, object] = {
        "device_groups": [EMEA, DC, APAC],
        "device_group_parents": {DC: EMEA},
    }
    base.update(kw)
    return Snapshot(**base)  # type: ignore[arg-type]


def _addr(name: str, loc: str, value: str = "10.0.0.1/32", **kw: object) -> Address:
    return Address(
        name=name,
        location=Location.dg(loc) if loc != "shared" else Location.shared(),
        type=AddressType.IP_NETMASK,
        value=value,
        **kw,
    )  # type: ignore[arg-type]


def _move(snap: Snapshot, *, kind: ObjectKind, name: str, src: str, dst: str) -> ChangeSet:
    return plan_move(
        snap, ReferenceGraph.build(snap), kind=kind, name=name, source_name=src, dest_name=dst
    )


# -- clean promote ---------------------------------------------------------


def test_clean_promote_dg_to_shared() -> None:
    snap = _snap(
        addresses=[_addr("h1", DC)],
        security_rules=[SecurityRule(name="r1", location=Location.dg(DC), source=["h1"])],
    )
    cs = _move(snap, kind=ObjectKind.ADDRESS, name="h1", src=DC, dst="shared")
    assert not cs.is_blocked
    assert len(cs.upserts) == 1
    u = cs.upserts[0]
    assert (u.location, u.exists) == ("shared", False)
    assert u.fields["ip-netmask"] == "10.0.0.1/32"
    assert [(d.name, d.location) for d in cs.deletes] == [("h1", DC)]
    # Promote toward shared never needs a repoint: the rule falls through to the
    # new shared definition once the DG copy is gone.
    assert cs.reference_edits == []


def test_promote_to_ancestor_dg() -> None:
    snap = _snap(addresses=[_addr("h1", DC)])
    cs = _move(snap, kind=ObjectKind.ADDRESS, name="h1", src=DC, dst=EMEA)
    assert not cs.is_blocked
    assert cs.upserts[0].location == EMEA
    assert cs.deletes[0].location == DC


def test_service_clean_promote() -> None:
    snap = _snap(
        services=[
            Service(
                name="tcp-443", location=Location.dg(DC), protocol="tcp", destination_port="443"
            )
        ]
    )
    cs = _move(snap, kind=ObjectKind.SERVICE, name="tcp-443", src=DC, dst="shared")
    assert not cs.is_blocked
    assert cs.upserts[0].fields["protocol/tcp/port"] == "443"
    assert cs.deletes[0].location == DC


def test_tag_clean_promote() -> None:
    snap = _snap(tags=[Tag(name="prod", location=Location.dg(DC), color="color1")])
    cs = _move(snap, kind=ObjectKind.TAG, name="prod", src=DC, dst="shared")
    assert not cs.is_blocked
    assert cs.upserts[0].kind is ObjectKind.TAG
    assert cs.deletes[0].name == "prod"


# -- direction gate --------------------------------------------------------


def test_sibling_destination_blocked() -> None:
    snap = _snap(addresses=[_addr("h1", DC)])
    cs = _move(snap, kind=ObjectKind.ADDRESS, name="h1", src=DC, dst=APAC)
    assert cs.is_blocked
    assert cs.op_count == 0
    assert any("toward shared" in b or "ancestor" in b for b in cs.blockers)


def test_child_destination_blocked() -> None:
    snap = _snap(addresses=[_addr("h1", EMEA)])
    cs = _move(snap, kind=ObjectKind.ADDRESS, name="h1", src=EMEA, dst=DC)
    assert cs.is_blocked
    assert cs.op_count == 0


def test_dest_equals_source_blocked() -> None:
    snap = _snap(addresses=[_addr("h1", DC)])
    cs = _move(snap, kind=ObjectKind.ADDRESS, name="h1", src=DC, dst=DC)
    assert cs.is_blocked
    assert cs.op_count == 0


def test_object_absent_blocked() -> None:
    cs = _move(_snap(), kind=ObjectKind.ADDRESS, name="ghost", src=DC, dst="shared")
    assert cs.is_blocked
    assert any("ghost" in b for b in cs.blockers)
    assert cs.op_count == 0


# -- collision at destination ----------------------------------------------


def test_collision_identical_value_merges_by_delete() -> None:
    snap = _snap(
        addresses=[_addr("h1", "shared"), _addr("h1", DC)],
        security_rules=[SecurityRule(name="r1", location=Location.dg(DC), source=["h1"])],
    )
    cs = _move(snap, kind=ObjectKind.ADDRESS, name="h1", src=DC, dst="shared")
    assert not cs.is_blocked
    # Destination already holds the value: drop the source copy, no second create.
    assert cs.upserts == []
    assert [(d.name, d.location) for d in cs.deletes] == [("h1", DC)]
    assert cs.reference_edits == []
    assert cs.warnings  # tells the user references now resolve to shared


def test_collision_different_value_blocked() -> None:
    snap = _snap(addresses=[_addr("h1", "shared", "10.0.0.1/32"), _addr("h1", DC, "10.0.0.2/32")])
    cs = _move(snap, kind=ObjectKind.ADDRESS, name="h1", src=DC, dst="shared")
    assert cs.is_blocked
    assert cs.op_count == 0
    assert any("value" in b for b in cs.blockers)


def test_intermediate_shadow_blocked() -> None:
    # h1 is defined at the intermediate EMEA too; promoting EMEA-DC's copy to
    # shared would re-resolve references to EMEA's, not shared's.
    snap = _snap(addresses=[_addr("h1", DC), _addr("h1", EMEA, "10.0.0.9/32")])
    cs = _move(snap, kind=ObjectKind.ADDRESS, name="h1", src=DC, dst="shared")
    assert cs.is_blocked
    assert cs.op_count == 0
    assert any(EMEA in b for b in cs.blockers)


# -- dependency gate -------------------------------------------------------


def test_dependency_member_not_visible_at_dest_blocked() -> None:
    snap = _snap(
        addresses=[_addr("m1", DC)],
        address_groups=[AddressGroup(name="grp", location=Location.dg(DC), static_members=["m1"])],
    )
    cs = _move(snap, kind=ObjectKind.ADDRESS_GROUP, name="grp", src=DC, dst="shared")
    assert cs.is_blocked
    assert cs.op_count == 0
    assert any("m1" in b for b in cs.blockers)


def test_dependency_tag_not_visible_at_dest_blocked() -> None:
    snap = _snap(
        addresses=[_addr("h1", DC, tags=["t1"])],
        tags=[Tag(name="t1", location=Location.dg(DC))],
    )
    cs = _move(snap, kind=ObjectKind.ADDRESS, name="h1", src=DC, dst="shared")
    assert cs.is_blocked
    assert any("t1" in b for b in cs.blockers)


def test_dependency_visible_at_dest_ok() -> None:
    snap = _snap(
        addresses=[_addr("h1", DC, tags=["t1"])],
        tags=[Tag(name="t1", location=Location.shared())],
    )
    cs = _move(snap, kind=ObjectKind.ADDRESS, name="h1", src=DC, dst="shared")
    assert not cs.is_blocked
    assert cs.deletes[0].location == DC


def test_group_member_visible_at_dest_ok() -> None:
    snap = _snap(
        addresses=[_addr("m1", "shared")],
        address_groups=[AddressGroup(name="grp", location=Location.dg(DC), static_members=["m1"])],
    )
    cs = _move(snap, kind=ObjectKind.ADDRESS_GROUP, name="grp", src=DC, dst="shared")
    assert not cs.is_blocked
    assert cs.upserts[0].members == ["m1"]


# -- promote-to-shared side effect -----------------------------------------


def test_promote_to_shared_revives_sibling_dangling_warns() -> None:
    # APAC's rule references h1 but APAC cannot see EMEA-DC, so it dangles today.
    # Promoting h1 to shared makes that reference resolve — warn about it.
    snap = _snap(
        addresses=[_addr("h1", DC)],
        security_rules=[SecurityRule(name="apac-r", location=Location.dg(APAC), source=["h1"])],
    )
    cs = _move(snap, kind=ObjectKind.ADDRESS, name="h1", src=DC, dst="shared")
    assert not cs.is_blocked
    assert any("apac-r" in w for w in cs.warnings)


def test_promote_to_ancestor_does_not_warn_about_unrelated_dangling() -> None:
    # Promoting only to EMEA (not shared) does not make h1 visible to APAC, so
    # no revival warning should fire.
    snap = _snap(
        addresses=[_addr("h1", DC)],
        security_rules=[SecurityRule(name="apac-r", location=Location.dg(APAC), source=["h1"])],
    )
    cs = _move(snap, kind=ObjectKind.ADDRESS, name="h1", src=DC, dst=EMEA)
    assert not cs.is_blocked
    assert not any("apac-r" in w for w in cs.warnings)
