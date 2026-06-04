from __future__ import annotations

from psc.core.dedup import (
    ObjectRef,
    find_duplicate_addresses,
    find_duplicate_services,
    plan_merge,
)
from psc.core.models import Address, AddressType, Snapshot
from psc.core.refs import ReferenceGraph


def test_merge_repoints_across_new_rulebases(all_rb_snapshot: Snapshot) -> None:
    # a2-dup is referenced only by the SD-WAN rule's destination; merging it into
    # a2 must repoint that rule before deleting the duplicate.
    graph = ReferenceGraph.build(all_rb_snapshot)
    cs = plan_merge(
        all_rb_snapshot,
        graph,
        keep=ObjectRef(name="a2", location="shared"),
        drop=ObjectRef(name="a2-dup", location="shared"),
    )
    assert not cs.is_blocked
    edits = {(e.referrer_kind, e.referrer_name, e.field): e for e in cs.reference_edits}
    assert edits[("sdwan-rule", "sdwan-1", "destination")].after == ["a2"]
    assert cs.deletes[0].name == "a2-dup"


def test_merge_blocks_when_repoint_hits_pbf_nexthop(all_rb_snapshot: Snapshot) -> None:
    # nh-host is a PBF forwarding next-hop — a nested field with no flat member
    # list. Merging it away can't repoint that reference, so the plan must block
    # rather than strand a dangling next-hop.
    graph = ReferenceGraph.build(all_rb_snapshot)
    cs = plan_merge(
        all_rb_snapshot,
        graph,
        keep=ObjectRef(name="nh-dup", location="shared"),
        drop=ObjectRef(name="nh-host", location="shared"),
    )
    assert cs.is_blocked
    assert any("nh-host" in b and "pbf-1" in b for b in cs.blockers)
    assert cs.op_count == 0


def _host_and_network() -> Snapshot:
    """A host written with a subnet mask and a real network object: identical
    only after host-bit masking, so a strict dedup must keep them apart."""
    return Snapshot(
        addresses=[
            Address(name="host", type=AddressType.IP_NETMASK, value="10.1.1.50/24"),
            Address(name="net", type=AddressType.IP_NETMASK, value="10.1.1.0/24"),
        ]
    )


def test_duplicate_addresses_grouped_by_value(snapshot: Snapshot) -> None:
    groups = {g.value: {m.name for m in g.members} for g in find_duplicate_addresses(snapshot)}
    assert groups["ip-netmask 10.0.0.10/32"] == {"h-web1", "web-primary", "h-web1-slash"}
    assert groups["ip-netmask 192.168.1.1/32"] == {"edge-dup", "local-only"}


def test_dedup_strict_default_excludes_host_with_mask() -> None:
    # Strict (default): a /24-masked host is NOT a duplicate of the /24 network.
    assert find_duplicate_addresses(_host_and_network()) == []


def test_dedup_not_strict_groups_host_with_mask() -> None:
    # --not-strict restores the host-bit-masking behaviour for the fringe case.
    groups = find_duplicate_addresses(_host_and_network(), strict=False)
    assert len(groups) == 1
    assert {m.name for m in groups[0].members} == {"host", "net"}


def test_dedup_strict_still_groups_genuine_duplicates(snapshot: Snapshot) -> None:
    # Strict must keep collapsing 10.0.0.10 and 10.0.0.10/32 — genuinely identical.
    groups = {g.value: {m.name for m in g.members} for g in find_duplicate_addresses(snapshot)}
    assert groups["ip-netmask 10.0.0.10/32"] == {"h-web1", "web-primary", "h-web1-slash"}


def test_duplicate_services(snapshot: Snapshot) -> None:
    groups = find_duplicate_services(snapshot)
    members = {m.name for g in groups for m in g.members}
    assert {"tcp-443", "svc-https"} <= members


def test_merge_repoints_then_deletes(snapshot: Snapshot) -> None:
    graph = ReferenceGraph.build(snapshot)
    cs = plan_merge(
        snapshot,
        graph,
        keep=ObjectRef(name="h-web1", location="shared"),
        drop=ObjectRef(name="web-primary", location="shared"),
    )
    assert not cs.is_blocked
    # group rewrite drops web-primary, keeps h-web1 (deduped)
    edits = {(e.referrer_name, e.field): e for e in cs.reference_edits}
    assert edits[("grp-web", "static")].after == ["h-web1"]
    assert edits[("nat-web", "source")].after == ["h-web1"]
    # delete is last and targets the dropped object
    assert cs.deletes[0].name == "web-primary"


def test_merge_blocks_value_mismatch(snapshot: Snapshot) -> None:
    graph = ReferenceGraph.build(snapshot)
    cs = plan_merge(
        snapshot,
        graph,
        keep=ObjectRef(name="net-10", location="shared"),
        drop=ObjectRef(name="local-only", location="DG-EDGE"),
    )
    assert cs.is_blocked
    assert any("value mismatch" in b for b in cs.blockers)


def test_merge_blocks_host_vs_network_value_mismatch() -> None:
    # The merge gate compares exact values: a /24-masked host and the /24
    # network mean different things, so the merge is blocked without --force.
    snap = _host_and_network()
    graph = ReferenceGraph.build(snap)
    cs = plan_merge(
        snap,
        graph,
        keep=ObjectRef(name="net", location="shared"),
        drop=ObjectRef(name="host", location="shared"),
    )
    assert cs.is_blocked
    assert any("value mismatch" in b for b in cs.blockers)


def test_merge_allows_value_change_when_forced(snapshot: Snapshot) -> None:
    graph = ReferenceGraph.build(snapshot)
    cs = plan_merge(
        snapshot,
        graph,
        keep=ObjectRef(name="net-10", location="shared"),
        drop=ObjectRef(name="rng-db", location="shared"),
        allow_value_change=True,
    )
    assert not cs.is_blocked


def test_merge_missing_object_blocks(snapshot: Snapshot) -> None:
    graph = ReferenceGraph.build(snapshot)
    cs = plan_merge(
        snapshot,
        graph,
        keep=ObjectRef(name="does-not-exist", location="shared"),
        drop=ObjectRef(name="web-primary", location="shared"),
    )
    assert cs.is_blocked


def test_merge_blocks_when_repoint_hits_nat_translation(snapshot: Snapshot) -> None:
    """net-10 is referenced by nat-web's source-translation, which has no flat
    member list — repointing it away can't be expressed offline or live, so a
    merge that would delete net-10 must be blocked, not silently skipped (#28).
    """
    graph = ReferenceGraph.build(snapshot)
    cs = plan_merge(
        snapshot,
        graph,
        keep=ObjectRef(name="h-web1", location="shared"),
        drop=ObjectRef(name="net-10", location="shared"),
        allow_value_change=True,
    )
    assert cs.is_blocked
    assert any("net-10" in b and "nat-web" in b for b in cs.blockers)
    # a blocked plan carries zero executable ops
    assert cs.op_count == 0
