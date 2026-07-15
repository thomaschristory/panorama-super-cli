"""Promote engine: collapse a cross-DG duplicate bucket into a common ancestor."""

from __future__ import annotations

import pytest

from psc.core.changeset import ChangeSet, ObjectKind
from psc.core.dedup import ObjectRef
from psc.core.models import (
    Address,
    AddressType,
    Location,
    SecurityRule,
    Service,
    Snapshot,
)
from psc.core.promote import SkippedBucket, plan_promote, plan_promote_all, select_bucket
from psc.core.refs import ReferenceGraph
from psc.output.errors import PscError

EMEA = "EMEA"
DC = "EMEA-DC"  # child of EMEA
APAC = "APAC"  # sibling of EMEA


def _snap(**kw: object) -> Snapshot:
    base: dict[str, object] = {
        "device_groups": [EMEA, DC, APAC],
        "device_group_parents": {DC: EMEA},
    }
    base.update(kw)
    return Snapshot(**base)  # type: ignore[arg-type]


def _loc(name: str) -> Location:
    return Location.shared() if name == "shared" else Location.dg(name)


def _addr(name: str, loc: str, value: str = "10.0.0.1/32", **kw: object) -> Address:
    return Address(
        name=name,
        location=_loc(loc),
        type=AddressType.IP_NETMASK,
        value=value,
        **kw,  # type: ignore[arg-type]
    )


def _svc(name: str, loc: str, port: str = "443") -> Service:
    return Service(name=name, location=_loc(loc), protocol="tcp", destination_port=port)


def _rule(name: str, loc: str, *, source: list[str]) -> SecurityRule:
    return SecurityRule(name=name, location=_loc(loc), source=source)


def _promote(
    snap: Snapshot, *, kind: ObjectKind, members: list[tuple[str, str]], dest: str = "shared"
) -> ChangeSet:
    return plan_promote(
        snap,
        ReferenceGraph.build(snap),
        kind=kind,
        members=[ObjectRef(name=n, location=loc) for n, loc in members],
        dest_name=dest,
    )


def test_same_name_bucket_promotes_with_one_upsert_and_no_reference_edits() -> None:
    snap = _snap(
        addresses=[_addr("web", EMEA), _addr("web", APAC)],
        security_rules=[_rule("r1", EMEA, source=["web"]), _rule("r2", APAC, source=["web"])],
    )
    cs = _promote(snap, kind=ObjectKind.ADDRESS, members=[("web", EMEA), ("web", APAC)])

    assert not cs.is_blocked
    assert [(u.name, u.location, u.exists) for u in cs.upserts] == [("web", "shared", False)]
    assert cs.upserts[0].fields["ip-netmask"] == "10.0.0.1/32"
    # Upward promotion: every reference falls through by shadowing.
    assert cs.reference_edits == []
    assert sorted((d.name, d.location) for d in cs.deletes) == [("web", APAC), ("web", EMEA)]


def test_bucket_already_containing_the_destination_copy_is_a_pure_delete() -> None:
    snap = _snap(addresses=[_addr("web", "shared"), _addr("web", EMEA)])
    cs = _promote(snap, kind=ObjectKind.ADDRESS, members=[("web", "shared"), ("web", EMEA)])

    assert not cs.is_blocked
    assert cs.upserts == []  # the shared copy already IS the destination object
    assert [(d.name, d.location) for d in cs.deletes] == [("web", EMEA)]
    assert any("already defines" in w for w in cs.warnings)


def test_services_promote_the_same_way() -> None:
    snap = _snap(services=[_svc("https", EMEA), _svc("https", APAC)])
    cs = _promote(snap, kind=ObjectKind.SERVICE, members=[("https", EMEA), ("https", APAC)])

    assert not cs.is_blocked
    assert [(u.name, u.location) for u in cs.upserts] == [("https", "shared")]
    assert len(cs.deletes) == 2


def test_promotes_to_a_common_ancestor_device_group() -> None:
    snap = _snap(addresses=[_addr("web", DC), _addr("web", EMEA)])
    cs = _promote(snap, kind=ObjectKind.ADDRESS, members=[("web", DC), ("web", EMEA)], dest=EMEA)

    assert not cs.is_blocked
    # EMEA already defines it, so it IS the destination object — only DC's copy goes.
    assert cs.upserts == []
    assert [(d.name, d.location) for d in cs.deletes] == [("web", DC)]


def test_destination_that_is_not_a_common_ancestor_is_blocked() -> None:
    snap = _snap(addresses=[_addr("web", EMEA), _addr("web", APAC)])
    cs = _promote(snap, kind=ObjectKind.ADDRESS, members=[("web", EMEA), ("web", APAC)], dest=EMEA)

    assert cs.is_blocked
    assert any("only promotes toward shared" in b for b in cs.blockers)
    assert cs.upserts == [] and cs.deletes == []


def test_divergent_names_without_keep_are_blocked() -> None:
    snap = _snap(addresses=[_addr("h-web1", EMEA), _addr("web-primary", APAC)])
    cs = _promote(snap, kind=ObjectKind.ADDRESS, members=[("h-web1", EMEA), ("web-primary", APAC)])

    assert cs.is_blocked
    assert any("names diverge" in b for b in cs.blockers)


def test_intermediate_shadow_is_blocked() -> None:
    # Bucket members are DC and APAC (same value); EMEA — an ancestor of DC that
    # sits *between* DC and shared, but is not itself a bucket member — already
    # defines "web" with a different value. Promoting DC's copy to shared would
    # re-resolve DC's rules to EMEA's object, not the promoted one.
    snap = _snap(
        addresses=[
            _addr("web", DC),
            _addr("web", APAC),
            _addr("web", EMEA, value="10.9.9.9/32"),
        ]
    )
    cs = _promote(snap, kind=ObjectKind.ADDRESS, members=[("web", DC), ("web", APAC)])

    assert cs.is_blocked
    assert any("between" in b and "already defines" in b for b in cs.blockers)


def test_destination_with_a_different_value_is_blocked() -> None:
    snap = _snap(
        addresses=[
            _addr("web", "shared", value="10.9.9.9/32"),
            _addr("web", EMEA),
            _addr("web", APAC),
        ]
    )
    cs = _promote(snap, kind=ObjectKind.ADDRESS, members=[("web", EMEA), ("web", APAC)])

    assert cs.is_blocked
    assert any("different value" in b for b in cs.blockers)


def test_members_with_different_values_are_not_one_bucket() -> None:
    snap = _snap(addresses=[_addr("web", EMEA), _addr("web", APAC, value="10.9.9.9/32")])
    cs = _promote(snap, kind=ObjectKind.ADDRESS, members=[("web", EMEA), ("web", APAC)])

    assert cs.is_blocked
    assert any("not one bucket" in b for b in cs.blockers)


def test_missing_member_is_blocked() -> None:
    snap = _snap(addresses=[_addr("web", EMEA)])
    cs = _promote(snap, kind=ObjectKind.ADDRESS, members=[("web", EMEA), ("web", APAC)])

    assert cs.is_blocked
    assert any("does not exist" in b for b in cs.blockers)


def test_unpromotable_kind_is_an_input_error() -> None:
    snap = _snap()
    with pytest.raises(PscError):
        plan_promote(
            snap,
            ReferenceGraph.build(snap),
            kind=ObjectKind.TAG,
            members=[ObjectRef(name="t", location=EMEA)],
        )


def test_empty_bucket_is_an_input_error() -> None:
    snap = _snap()
    with pytest.raises(PscError):
        plan_promote(snap, ReferenceGraph.build(snap), kind=ObjectKind.ADDRESS, members=[])


def test_a_sibling_device_group_still_defining_the_name_warns() -> None:
    # APAC is not in the bucket, but defines its own `web`. After promoting the
    # EMEA/DC copies to shared, APAC keeps shadowing shared/web for its subtree.
    snap = _snap(
        addresses=[
            _addr("web", EMEA),
            _addr("web", DC, value="10.0.0.1/32"),
            _addr("web", APAC, value="10.9.9.9/32"),
        ]
    )
    cs = _promote(snap, kind=ObjectKind.ADDRESS, members=[("web", EMEA), ("web", DC)])

    # DC->shared crosses EMEA, which defines `web` -> intermediate-shadow blocker.
    assert cs.is_blocked


def test_sibling_shadow_warning_on_a_clean_promote() -> None:
    # Bucket is DC + AMER; APAC is an uninvolved sibling that keeps its own `web`,
    # and is NOT between either source and shared, so it warns rather than blocks.
    snap = _snap(
        addresses=[
            _addr("web", DC),
            _addr("web", "AMER"),
            _addr("web", APAC, value="10.9.9.9/32"),
        ],
        device_groups=[EMEA, DC, APAC, "AMER"],
        device_group_parents={DC: EMEA},
    )
    cs = _promote(snap, kind=ObjectKind.ADDRESS, members=[("web", DC), ("web", "AMER")])

    assert not cs.is_blocked
    assert any(f"'{APAC}' still defines 'web'" in w and "keep shadowing" in w for w in cs.warnings)


def test_bucket_members_own_device_groups_are_not_reported_as_shadows() -> None:
    snap = _snap(addresses=[_addr("web", EMEA), _addr("web", APAC)])
    cs = _promote(snap, kind=ObjectKind.ADDRESS, members=[("web", EMEA), ("web", APAC)])

    assert not cs.is_blocked
    assert not any("keep shadowing" in w for w in cs.warnings)


def test_tags_only_on_a_discarded_copy_are_warned_about() -> None:
    snap = _snap(
        addresses=[
            _addr("web", "shared"),
            _addr("web", EMEA, tags=["prod"]),
        ]
    )
    cs = _promote(snap, kind=ObjectKind.ADDRESS, members=[("web", "shared"), ("web", EMEA)])

    assert not cs.is_blocked
    assert any("tags the promoted copy will not carry: prod" in w for w in cs.warnings)


def _promote_all(
    snap: Snapshot, *, kind: ObjectKind, dest: str = "shared"
) -> tuple[ChangeSet, list[SkippedBucket]]:
    return plan_promote_all(snap, ReferenceGraph.build(snap), kind=kind, dest_name=dest)


def test_promote_all_aggregates_every_clean_bucket_into_one_plan() -> None:
    snap = _snap(
        addresses=[
            _addr("web", EMEA),
            _addr("web", APAC),
            _addr("db", EMEA, value="10.0.0.7/32"),
            _addr("db", APAC, value="10.0.0.7/32"),
        ]
    )
    cs, skipped = _promote_all(snap, kind=ObjectKind.ADDRESS)

    assert not cs.is_blocked
    assert skipped == []
    assert sorted(u.name for u in cs.upserts) == ["db", "web"]
    assert all(u.location == "shared" for u in cs.upserts)
    assert len(cs.deletes) == 4


def test_promote_all_skips_a_blocked_bucket_without_contaminating_blockers() -> None:
    snap = _snap(
        addresses=[
            _addr("web", EMEA),
            _addr("web", APAC),
            # divergent names -> this bucket cannot be promoted without --keep
            _addr("h-db", EMEA, value="10.0.0.7/32"),
            _addr("db-primary", APAC, value="10.0.0.7/32"),
        ]
    )
    cs, skipped = _promote_all(snap, kind=ObjectKind.ADDRESS)

    # The clean bucket still lands; the blocked one is reported, not fatal.
    assert not cs.is_blocked
    assert cs.blockers == []
    assert [u.name for u in cs.upserts] == ["web"]
    assert len(skipped) == 1
    assert "names diverge" in skipped[0].reason
    assert any("skipped 1 bucket" in w for w in cs.warnings)


def test_promote_all_skips_both_buckets_on_a_cross_bucket_name_collision() -> None:
    # Two DIFFERENT values, both wanting the name `web` at shared. Same name +
    # same value would be one bucket, so a name clash is always a value clash.
    # All four device-groups are flat top-level siblings (no parent entries), so
    # each bucket is internally clean on its own -- the only thing that stops
    # them is the destination-name collision between the two buckets.
    snap = _snap(
        addresses=[
            _addr("web", EMEA, value="10.0.0.1/32"),
            _addr("web", APAC, value="10.0.0.1/32"),
            _addr("web", "AMER", value="10.9.9.9/32"),
            _addr("web", "LATAM", value="10.9.9.9/32"),
        ],
        device_groups=[EMEA, APAC, "AMER", "LATAM"],
        device_group_parents={},
    )
    cs, skipped = _promote_all(snap, kind=ObjectKind.ADDRESS)

    assert cs.blockers == []
    assert cs.upserts == []  # neither bucket survives
    assert len(skipped) == 2
    assert all("name clash" in s.reason for s in skipped)


def test_promote_all_reports_nothing_when_there_are_no_duplicates() -> None:
    snap = _snap(addresses=[_addr("web", EMEA)])
    cs, skipped = _promote_all(snap, kind=ObjectKind.ADDRESS)

    assert cs.is_empty
    assert skipped == []


def test_select_bucket_finds_an_address_bucket_by_value() -> None:
    snap = _snap(addresses=[_addr("web", EMEA), _addr("web", APAC)])
    bucket = select_bucket(
        snap, ReferenceGraph.build(snap), kind=ObjectKind.ADDRESS, value="10.0.0.1/32"
    )
    assert sorted(m.location for m in bucket.members) == [APAC, EMEA]


def test_select_bucket_finds_a_service_bucket_by_value() -> None:
    snap = _snap(services=[_svc("https", EMEA), _svc("https", APAC)])
    bucket = select_bucket(
        snap, ReferenceGraph.build(snap), kind=ObjectKind.SERVICE, value="tcp/dst=443/src="
    )
    assert bucket.count == 2


def test_select_bucket_on_a_value_with_no_bucket_is_an_input_error() -> None:
    snap = _snap(addresses=[_addr("web", EMEA)])
    with pytest.raises(PscError):
        select_bucket(
            snap, ReferenceGraph.build(snap), kind=ObjectKind.ADDRESS, value="10.0.0.1/32"
        )
