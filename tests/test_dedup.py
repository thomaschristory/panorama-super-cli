from __future__ import annotations

from psc.core.dedup import (
    ObjectRef,
    find_duplicate_addresses,
    find_duplicate_services,
    plan_merge,
)
from psc.core.models import Snapshot
from psc.core.refs import ReferenceGraph


def test_duplicate_addresses_grouped_by_value(snapshot: Snapshot) -> None:
    groups = {g.value: {m.name for m in g.members} for g in find_duplicate_addresses(snapshot)}
    assert groups["ip-netmask 10.0.0.10/32"] == {"h-web1", "web-primary", "h-web1-slash"}
    assert groups["ip-netmask 192.168.1.1/32"] == {"edge-dup", "local-only"}


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
