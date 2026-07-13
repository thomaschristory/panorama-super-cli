"""Merging a device-group's local shadow into the object it shadows (#144).

The visibility gate on a merge must ask "after this plan applies, where does the
kept name resolve from the referrer's scope?" — not "where does it resolve
today". Today's namespace still contains the object being dropped, and that
object is usually the very shadow standing between the referrer and the keep.

Two container layouts pin the boundary. Siblings (`dg-a`, `dg-b` both under
`shared`) — dropping `'web'@dg-b` lets `dg-b` walk up to `'web'@shared`, and
`'web'@dg-a` is never on that walk. Nested (`shared` → `dg-parent` → `dg-child`)
— dropping `'web'@dg-child` stops the walk at `'web'@dg-parent`, an intermediate
shadow that is *not* the keep, so the merge must still block.
"""

from __future__ import annotations

import pytest

from psc.core.dedup import (
    ObjectRef,
    plan_merge,
    plan_merge_bucket,
    plan_merge_group,
)
from psc.core.models import (
    SHARED,
    Address,
    AddressGroup,
    AddressType,
    Location,
    SecurityRule,
    Snapshot,
)
from psc.core.refs import ReferenceGraph

DG_A = Location.dg("dg-a")
DG_B = Location.dg("dg-b")
PARENT = Location.dg("dg-parent")
CHILD = Location.dg("dg-child")

VALUE = "10.0.0.1/32"


def addr(name: str, loc: Location, *, value: str = VALUE, tags: list[str] | None = None) -> Address:
    return Address(
        name=name,
        location=loc,
        type=AddressType.IP_NETMASK,
        value=value,
        tags=tags or [],
    )


def rule(name: str, loc: Location, *, source: list[str]) -> SecurityRule:
    return SecurityRule(name=name, location=loc, source=source, destination=["any"])


@pytest.fixture
def siblings() -> Snapshot:
    """`dg-a` and `dg-b` are both children of `shared`; each has its own `web`."""
    return Snapshot(
        device_groups=["dg-a", "dg-b"],
        addresses=[addr("web", SHARED), addr("web", DG_A), addr("web", DG_B)],
        security_rules=[rule("r1", DG_B, source=["web"]), rule("r2", DG_A, source=["web"])],
    )


@pytest.fixture
def nested() -> Snapshot:
    """shared → dg-parent → dg-child, each defining its own `web`."""
    return Snapshot(
        device_groups=["dg-parent", "dg-child"],
        device_group_parents={"dg-child": "dg-parent"},
        addresses=[addr("web", SHARED), addr("web", PARENT), addr("web", CHILD)],
        security_rules=[rule("r1", CHILD, source=["web"])],
    )


# --- the false positive (#144) ---------------------------------------------


def test_sibling_shadow_collapses_into_shared(siblings: Snapshot) -> None:
    # `r1`@dg-b resolves `web` to 'web'@dg-b today. Deleting that shadow makes it
    # walk up to 'web'@shared — the keep. Nothing to repoint, nothing broken.
    cs = plan_merge(
        siblings,
        ReferenceGraph.build(siblings),
        keep=ObjectRef(name="web", location="shared"),
        drop=ObjectRef(name="web", location="dg-b"),
    )
    assert not cs.is_blocked, cs.blockers
    assert [(d.name, d.location) for d in cs.deletes] == [("web", "dg-b")]


def test_same_name_collapse_emits_no_reference_edits(siblings: Snapshot) -> None:
    # The referrer keeps the literal string `web`; only its resolution moves. A
    # ReferenceEdit rewriting `web` -> `web` would be a no-op in the plan and a
    # pointless write on the device.
    cs = plan_merge(
        siblings,
        ReferenceGraph.build(siblings),
        keep=ObjectRef(name="web", location="shared"),
        drop=ObjectRef(name="web", location="dg-b"),
    )
    assert cs.reference_edits == []


def test_same_name_collapse_warns_about_re_resolution(siblings: Snapshot) -> None:
    # The delete-only plan is correct but silent: warn that live references now
    # point somewhere new.
    cs = plan_merge(
        siblings,
        ReferenceGraph.build(siblings),
        keep=ObjectRef(name="web", location="shared"),
        drop=ObjectRef(name="web", location="dg-b"),
    )
    assert any(
        "re-resolve" in w and "'web'@dg-b" in w and "'web'@shared" in w for w in cs.warnings
    ), cs.warnings


def test_sibling_shadow_collapses_into_parent_dg() -> None:
    # Same shape one level down: the keep is a parent DG rather than `shared`.
    snap = Snapshot(
        device_groups=["dg-parent", "dg-child"],
        device_group_parents={"dg-child": "dg-parent"},
        addresses=[addr("web", PARENT), addr("web", CHILD)],
        security_rules=[rule("r1", CHILD, source=["web"])],
    )
    cs = plan_merge(
        snap,
        ReferenceGraph.build(snap),
        keep=ObjectRef(name="web", location="dg-parent"),
        drop=ObjectRef(name="web", location="dg-child"),
    )
    assert not cs.is_blocked, cs.blockers


# --- the blocker that must survive ------------------------------------------


def test_nested_intermediate_shadow_still_blocks(nested: Snapshot) -> None:
    # Dropping 'web'@dg-child makes dg-child stop at 'web'@dg-parent — NOT the
    # kept shared object. Repointing here would silently change what `r1` matches.
    cs = plan_merge(
        nested,
        ReferenceGraph.build(nested),
        keep=ObjectRef(name="web", location="shared"),
        drop=ObjectRef(name="web", location="dg-child"),
    )
    assert cs.is_blocked
    assert any("not visible" in b for b in cs.blockers), cs.blockers
    assert cs.deletes == []


def test_unrelated_shadow_at_referrer_still_blocks() -> None:
    # The keep is shadowed at the referrer's own DG by a *different* object that
    # this plan does not delete. Repointing `web2` -> `web` in dg-a would bind to
    # 'web'@dg-a, not the kept 'web'@shared.
    snap = Snapshot(
        device_groups=["dg-a"],
        addresses=[addr("web", SHARED), addr("web", DG_A), addr("web2", DG_A)],
        security_rules=[rule("r1", DG_A, source=["web2"])],
    )
    cs = plan_merge(
        snap,
        ReferenceGraph.build(snap),
        keep=ObjectRef(name="web", location="shared"),
        drop=ObjectRef(name="web2", location="dg-a"),
    )
    assert cs.is_blocked
    assert any("not visible" in b for b in cs.blockers), cs.blockers


def test_cross_scope_keep_still_blocks() -> None:
    # Keep lives in dg-a; the referrer lives in dg-b. dg-a is never on dg-b's
    # upward walk, so the kept object is genuinely invisible there.
    snap = Snapshot(
        device_groups=["dg-a", "dg-b"],
        addresses=[addr("web", DG_A), addr("web2", SHARED)],
        security_rules=[rule("r1", DG_B, source=["web2"])],
    )
    cs = plan_merge(
        snap,
        ReferenceGraph.build(snap),
        keep=ObjectRef(name="web", location="dg-a"),
        drop=ObjectRef(name="web2", location="shared"),
    )
    assert cs.is_blocked
    assert any("not visible" in b for b in cs.blockers), cs.blockers


# --- address-groups take the same path --------------------------------------


def test_group_sibling_shadow_collapses_into_shared() -> None:
    snap = Snapshot(
        device_groups=["dg-a", "dg-b"],
        addresses=[addr("h1", SHARED)],
        address_groups=[
            AddressGroup(name="grp", location=SHARED, static_members=["h1"]),
            AddressGroup(name="grp", location=DG_A, static_members=["h1"]),
            AddressGroup(name="grp", location=DG_B, static_members=["h1"]),
        ],
        security_rules=[rule("r1", DG_B, source=["grp"])],
    )
    cs = plan_merge_group(
        snap,
        ReferenceGraph.build(snap),
        keep=ObjectRef(name="grp", location="shared"),
        drop=ObjectRef(name="grp", location="dg-b"),
    )
    assert not cs.is_blocked, cs.blockers
    assert cs.reference_edits == []
    assert [(d.name, d.location) for d in cs.deletes] == [("grp", "dg-b")]


def test_group_nested_intermediate_shadow_still_blocks() -> None:
    snap = Snapshot(
        device_groups=["dg-parent", "dg-child"],
        device_group_parents={"dg-child": "dg-parent"},
        addresses=[addr("h1", SHARED)],
        address_groups=[
            AddressGroup(name="grp", location=SHARED, static_members=["h1"]),
            AddressGroup(name="grp", location=PARENT, static_members=["h1"]),
            AddressGroup(name="grp", location=CHILD, static_members=["h1"]),
        ],
        security_rules=[rule("r1", CHILD, source=["grp"])],
    )
    cs = plan_merge_group(
        snap,
        ReferenceGraph.build(snap),
        keep=ObjectRef(name="grp", location="shared"),
        drop=ObjectRef(name="grp", location="dg-child"),
    )
    assert cs.is_blocked
    assert any("not visible" in b for b in cs.blockers), cs.blockers


# --- bucket merges ----------------------------------------------------------


def test_bucket_collapses_both_sibling_shadows(siblings: Snapshot) -> None:
    # Both DG copies drop in one plan; each pairwise sub-plan must ignore the
    # *other* drop too, or the sibling still shadowing the keep re-blocks it.
    cs = plan_merge_bucket(
        siblings,
        ReferenceGraph.build(siblings),
        members=[
            ObjectRef(name="web", location="shared"),
            ObjectRef(name="web", location="dg-a"),
            ObjectRef(name="web", location="dg-b"),
        ],
        keep=ObjectRef(name="web", location="shared"),
    )
    assert not cs.is_blocked, cs.blockers
    assert {(d.name, d.location) for d in cs.deletes} == {("web", "dg-a"), ("web", "dg-b")}


def test_bucket_default_keep_is_highest_in_hierarchy(siblings: Snapshot) -> None:
    # Without --keep the survivor must be the most visible member. Sorting the
    # location *strings* would pick "dg-a" over "shared".
    cs = plan_merge_bucket(
        siblings,
        ReferenceGraph.build(siblings),
        members=[
            ObjectRef(name="web", location="dg-a"),
            ObjectRef(name="web", location="dg-b"),
            ObjectRef(name="web", location="shared"),
        ],
    )
    assert not cs.is_blocked, cs.blockers
    assert "'web'@shared" in cs.title
    assert {(d.name, d.location) for d in cs.deletes} == {("web", "dg-a"), ("web", "dg-b")}


def test_bucket_default_keep_prefers_parent_over_child() -> None:
    # No shared member: the nearest-to-root device-group wins, not the
    # alphabetically-first one ("dg-child" < "dg-parent").
    snap = Snapshot(
        device_groups=["dg-parent", "dg-child"],
        device_group_parents={"dg-child": "dg-parent"},
        addresses=[addr("web", PARENT), addr("web", CHILD)],
    )
    cs = plan_merge_bucket(
        snap,
        ReferenceGraph.build(snap),
        members=[
            ObjectRef(name="web", location="dg-child"),
            ObjectRef(name="web", location="dg-parent"),
        ],
    )
    assert "'web'@dg-parent" in cs.title
    assert [(d.name, d.location) for d in cs.deletes] == [("web", "dg-child")]


# --- attribute drift --------------------------------------------------------


def test_dropped_shadow_with_extra_tags_warns(siblings: Snapshot) -> None:
    snap = siblings.model_copy(
        update={
            "addresses": [
                addr("web", SHARED),
                addr("web", DG_A),
                addr("web", DG_B, tags=["prod", "dmz"]),
            ]
        }
    )
    cs = plan_merge(
        snap,
        ReferenceGraph.build(snap),
        keep=ObjectRef(name="web", location="shared"),
        drop=ObjectRef(name="web", location="dg-b"),
    )
    assert not cs.is_blocked, cs.blockers
    tag_warnings = [w for w in cs.warnings if "tags" in w]
    assert tag_warnings, cs.warnings
    assert "dmz" in tag_warnings[0] and "prod" in tag_warnings[0]


def test_dropped_tag_feeding_a_dag_warns_about_membership(siblings: Snapshot) -> None:
    # The sharp edge: 'prod' is not just metadata, it decides DAG membership, so
    # dropping the shadow changes what traffic the DAG matches.
    snap = siblings.model_copy(
        update={
            "addresses": [addr("web", SHARED), addr("web", DG_A), addr("web", DG_B, tags=["prod"])],
            "address_groups": [
                AddressGroup(name="dag-prod", location=SHARED, dynamic_filter="'prod'")
            ],
        }
    )
    cs = plan_merge(
        snap,
        ReferenceGraph.build(snap),
        keep=ObjectRef(name="web", location="shared"),
        drop=ObjectRef(name="web", location="dg-b"),
    )
    assert not cs.is_blocked, cs.blockers
    assert any("dag-prod" in w and "membership" in w for w in cs.warnings), cs.warnings


def test_identical_shadow_warns_only_about_re_resolution(siblings: Snapshot) -> None:
    # No attribute drift → no drift noise. Only the re-resolution notice.
    cs = plan_merge(
        siblings,
        ReferenceGraph.build(siblings),
        keep=ObjectRef(name="web", location="shared"),
        drop=ObjectRef(name="web", location="dg-b"),
    )
    assert not any("tags" in w for w in cs.warnings), cs.warnings
