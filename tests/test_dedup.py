from __future__ import annotations

import pytest

from psc.core.apply_xml import apply_changeset
from psc.core.dedup import (
    ObjectRef,
    find_duplicate_addresses,
    find_duplicate_groups,
    find_duplicate_services,
    plan_merge,
    plan_merge_bucket,
    plan_merge_group,
    resolve_group_members,
)
from psc.core.models import (
    Address,
    AddressGroup,
    AddressType,
    Location,
    NatRule,
    SecurityRule,
    Snapshot,
)
from psc.core.normalize import normalize_address
from psc.core.parse import parse_config
from psc.core.refs import ReferenceGraph
from psc.output.errors import ErrorType, PscError


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


# --- group-level dedup (issue #10) -------------------------------------


def _addr(name: str, value: str) -> Address:
    return Address(name=name, type=AddressType.IP_NETMASK, value=value)


def _three_hosts() -> list[Address]:
    return [_addr("h1", "10.0.0.1/32"), _addr("h2", "10.0.0.2/32"), _addr("h3", "10.0.0.3/32")]


def _resolve_group_snapshot() -> Snapshot:
    """grp-a == grp-b (flat {h1,h2}), grp-b reaches {h2} through grp-nested."""
    return Snapshot(
        addresses=_three_hosts(),
        address_groups=[
            AddressGroup(name="grp-a", static_members=["h1", "h2"]),
            AddressGroup(name="grp-b", static_members=["h1", "grp-nested"]),
            AddressGroup(name="grp-nested", static_members=["h2"]),
            AddressGroup(name="grp-c", static_members=["h1", "h3"]),
            AddressGroup(name="grp-dyn", static_members=None, dynamic_filter="'t-prod'"),
        ],
    )


def _key(snap: Snapshot, name: str) -> str:
    a = next(a for a in snap.addresses if a.name == name)
    return normalize_address(a).exact_key()  # type: ignore[union-attr]


def test_resolve_group_members_flat() -> None:
    snap = _resolve_group_snapshot()
    graph = ReferenceGraph.build(snap)
    members = resolve_group_members(snap, graph, "grp-a", Location.shared())
    assert members == frozenset({_key(snap, "h1"), _key(snap, "h2")})


def test_resolve_group_members_nested_expands() -> None:
    snap = _resolve_group_snapshot()
    graph = ReferenceGraph.build(snap)
    # grp-b reaches h2 only through grp-nested; the leaf set must match grp-a's.
    assert resolve_group_members(snap, graph, "grp-b", Location.shared()) == resolve_group_members(
        snap, graph, "grp-a", Location.shared()
    )


def test_resolve_group_members_dynamic_is_none() -> None:
    snap = _resolve_group_snapshot()
    graph = ReferenceGraph.build(snap)
    # A dynamic group is a runtime-only set — never a comparable leaf set.
    assert resolve_group_members(snap, graph, "grp-dyn", Location.shared()) is None


def test_resolve_group_members_cycle_safe() -> None:
    # Two groups that reference each other must not loop forever; the leaf set
    # is just the directly-named addresses.
    snap = Snapshot(
        addresses=[_addr("h1", "10.0.0.1/32")],
        address_groups=[
            AddressGroup(name="loop-a", static_members=["h1", "loop-b"]),
            AddressGroup(name="loop-b", static_members=["loop-a"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    assert resolve_group_members(snap, graph, "loop-a", Location.shared()) == frozenset(
        {_key(snap, "h1")}
    )


def test_resolve_group_members_dangling_is_none() -> None:
    snap = Snapshot(
        addresses=[_addr("h1", "10.0.0.1/32")],
        address_groups=[AddressGroup(name="grp", static_members=["h1", "ghost"])],
    )
    graph = ReferenceGraph.build(snap)
    # A member that resolves to nothing makes the set unknowable, not narrower.
    assert resolve_group_members(snap, graph, "grp", Location.shared()) is None


def test_resolve_group_members_malformed_is_none() -> None:
    snap = Snapshot(
        addresses=[Address(name="bad", type=AddressType.IP_NETMASK, value="not-an-ip")],
        address_groups=[AddressGroup(name="grp", static_members=["bad"])],
    )
    graph = ReferenceGraph.build(snap)
    assert resolve_group_members(snap, graph, "grp", Location.shared()) is None


def test_find_duplicate_groups_buckets_identical_sets() -> None:
    snap = _resolve_group_snapshot()
    graph = ReferenceGraph.build(snap)
    res = find_duplicate_groups(snap, graph)
    names = {m.name for g in res.buckets for m in g.members}
    assert names == {"grp-a", "grp-b"}
    assert "grp-dyn" in res.dynamic_skipped


def test_find_duplicate_groups_distinct_not_grouped() -> None:
    snap = _resolve_group_snapshot()
    graph = ReferenceGraph.build(snap)
    res = find_duplicate_groups(snap, graph)
    # grp-c ({h1,h3}) shares its set with nobody — never a duplicate bucket.
    assert all("grp-c" not in {m.name for m in g.members} for g in res.buckets)


def test_find_duplicate_groups_reports_dangling() -> None:
    snap = Snapshot(
        addresses=[_addr("h1", "10.0.0.1/32")],
        address_groups=[
            AddressGroup(name="grp-x", static_members=["h1", "ghost"]),
            AddressGroup(name="grp-y", static_members=["h1", "ghost"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    res = find_duplicate_groups(snap, graph)
    assert res.buckets == []
    assert "grp-x" in res.unresolvable_skipped
    assert "grp-y" in res.unresolvable_skipped


def test_find_duplicate_groups_empty_sets_bucket() -> None:
    # Two empty static groups both match nothing — correctly equivalent.
    snap = Snapshot(
        address_groups=[
            AddressGroup(name="empty-a", static_members=[]),
            AddressGroup(name="empty-b", static_members=[]),
        ]
    )
    graph = ReferenceGraph.build(snap)
    res = find_duplicate_groups(snap, graph)
    assert {m.name for g in res.buckets for m in g.members} == {"empty-a", "empty-b"}


def test_plan_merge_group_repoints_security_rule() -> None:
    snap = Snapshot(
        addresses=_three_hosts(),
        address_groups=[
            AddressGroup(name="grp-a", static_members=["h1", "h2"]),
            AddressGroup(name="grp-b", static_members=["h1", "h2"]),
        ],
        security_rules=[
            SecurityRule(name="r1", destination=["grp-b"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name="grp-a", location="shared"),
        drop=ObjectRef(name="grp-b", location="shared"),
    )
    assert not cs.is_blocked
    edits = {(e.referrer_kind, e.referrer_name, e.field): e for e in cs.reference_edits}
    assert edits[("security-rule", "r1", "destination")].after == ["grp-a"]
    assert cs.deletes[0].name == "grp-b"


def test_plan_merge_group_repoints_parent_group() -> None:
    snap = Snapshot(
        addresses=_three_hosts(),
        address_groups=[
            AddressGroup(name="grp-a", static_members=["h1", "h2"]),
            AddressGroup(name="grp-b", static_members=["h1", "h2"]),
            AddressGroup(name="grp-parent", static_members=["grp-b"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name="grp-a", location="shared"),
        drop=ObjectRef(name="grp-b", location="shared"),
    )
    assert not cs.is_blocked
    edits = {(e.referrer_name, e.field): e for e in cs.reference_edits}
    assert edits[("grp-parent", "static")].after == ["grp-a"]


def test_plan_merge_group_blocks_non_equivalent() -> None:
    snap = Snapshot(
        addresses=_three_hosts(),
        address_groups=[
            AddressGroup(name="grp-a", static_members=["h1", "h2"]),
            AddressGroup(name="grp-c", static_members=["h1", "h3"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name="grp-a", location="shared"),
        drop=ObjectRef(name="grp-c", location="shared"),
    )
    assert cs.is_blocked
    assert any("effective member sets differ" in b for b in cs.blockers)
    assert cs.op_count == 0


def test_plan_merge_group_blocks_missing() -> None:
    snap = _resolve_group_snapshot()
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name="nope", location="shared"),
        drop=ObjectRef(name="grp-a", location="shared"),
    )
    assert cs.is_blocked
    assert cs.op_count == 0


def test_plan_merge_group_blocks_same_object() -> None:
    snap = _resolve_group_snapshot()
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name="grp-a", location="shared"),
        drop=ObjectRef(name="grp-a", location="shared"),
    )
    assert cs.is_blocked


def test_plan_merge_group_blocks_unresolvable() -> None:
    snap = Snapshot(
        addresses=[_addr("h1", "10.0.0.1/32")],
        address_groups=[
            AddressGroup(name="grp-a", static_members=["h1", "ghost"]),
            AddressGroup(name="grp-b", static_members=["h1", "ghost"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name="grp-a", location="shared"),
        drop=ObjectRef(name="grp-b", location="shared"),
    )
    assert cs.is_blocked
    assert any("unresolvable members" in b for b in cs.blockers)


def test_plan_merge_group_blocks_keep_not_visible() -> None:
    # grp-a in DG-A shadows nothing in DG-B: a rule in DG-B referencing grp-b
    # can't be repointed onto a sibling-DG keep, so the merge must block.
    snap = Snapshot(
        addresses=[
            _addr("h1", "10.0.0.1/32"),
            _addr("h2", "10.0.0.2/32"),
        ],
        address_groups=[
            AddressGroup(name="grp-a", static_members=["h1", "h2"], location=Location.dg("DG-A")),
            AddressGroup(name="grp-b", static_members=["h1", "h2"], location=Location.dg("DG-B")),
        ],
        security_rules=[
            SecurityRule(name="r1", destination=["grp-b"], location=Location.dg("DG-B")),
        ],
        device_groups=["DG-A", "DG-B"],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name="grp-a", location="DG-A"),
        drop=ObjectRef(name="grp-b", location="DG-B"),
    )
    assert cs.is_blocked
    assert any("not visible" in b for b in cs.blockers)
    assert cs.op_count == 0


def test_plan_merge_group_clears_ops_on_block() -> None:
    # A blocked group merge carries zero executable ops, like plan_merge.
    snap = Snapshot(
        addresses=_three_hosts(),
        address_groups=[
            AddressGroup(name="grp-a", static_members=["h1", "h2"]),
            AddressGroup(name="grp-c", static_members=["h1", "h3"]),
        ],
        security_rules=[SecurityRule(name="r1", destination=["grp-c"])],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name="grp-a", location="shared"),
        drop=ObjectRef(name="grp-c", location="shared"),
    )
    assert cs.is_blocked
    assert cs.reference_edits == []
    assert cs.deletes == []


# --- FIX A: self-referential / cyclic group merges must be blocked ---------


def test_plan_merge_group_blocks_keep_directly_contains_drop() -> None:
    # keep=grp-a lists grp-b; drop=grp-b lists {h1,h2}; grp-a effectively also
    # {h1,h2} (it contains only grp-b). Repointing grp-b->grp-a inside grp-a's
    # own static list would make grp-a reference itself — PAN-OS rejects that.
    snap = Snapshot(
        addresses=_three_hosts(),
        address_groups=[
            AddressGroup(name="grp-a", static_members=["grp-b"]),
            AddressGroup(name="grp-b", static_members=["h1", "h2"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name="grp-a", location="shared"),
        drop=ObjectRef(name="grp-b", location="shared"),
    )
    assert cs.is_blocked
    assert any("nested" in b and "cyclic" in b for b in cs.blockers)
    assert cs.op_count == 0


def test_plan_merge_group_blocks_transitive_containment() -> None:
    # keep=grp-a -> grp-mid -> drop=grp-b; after repoint grp-b->grp-a, grp-mid
    # would reference grp-a which transitively contains grp-mid: a cycle.
    snap = Snapshot(
        addresses=_three_hosts(),
        address_groups=[
            AddressGroup(name="grp-a", static_members=["grp-mid"]),
            AddressGroup(name="grp-mid", static_members=["grp-b"]),
            AddressGroup(name="grp-b", static_members=["h1", "h2"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name="grp-a", location="shared"),
        drop=ObjectRef(name="grp-b", location="shared"),
    )
    assert cs.is_blocked
    assert any("nested" in b and "cyclic" in b for b in cs.blockers)
    assert cs.op_count == 0


def test_plan_merge_group_blocks_drop_contains_keep() -> None:
    # Symmetric case: drop=grp-b contains keep=grp-a. After deleting grp-b and
    # repointing its referrers onto grp-a, any group that held grp-b would point
    # at grp-a, but grp-a is *inside* grp-b's closure — still a containment cycle.
    snap = Snapshot(
        addresses=_three_hosts(),
        address_groups=[
            AddressGroup(name="grp-a", static_members=["h1", "h2"]),
            AddressGroup(name="grp-b", static_members=["grp-a"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name="grp-a", location="shared"),
        drop=ObjectRef(name="grp-b", location="shared"),
    )
    assert cs.is_blocked
    assert any("nested" in b and "cyclic" in b for b in cs.blockers)
    assert cs.op_count == 0


def test_plan_merge_group_non_nested_equivalent_still_merges() -> None:
    # Regression: the containment guard must NOT block a normal equivalent pair
    # where neither group contains the other.
    snap = Snapshot(
        addresses=_three_hosts(),
        address_groups=[
            AddressGroup(name="grp-a", static_members=["h1", "h2"]),
            AddressGroup(name="grp-b", static_members=["h1", "h2"]),
        ],
        security_rules=[SecurityRule(name="r1", destination=["grp-b"])],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name="grp-a", location="shared"),
        drop=ObjectRef(name="grp-b", location="shared"),
    )
    assert not cs.is_blocked
    assert cs.deletes[0].name == "grp-b"


def test_find_duplicate_groups_still_buckets_nested_pair() -> None:
    # DECISION: the audit STILL buckets nested-but-equivalent pairs — they
    # genuinely share an effective set, so flagging them is useful advisory.
    # Only the MERGE is blocked.
    snap = Snapshot(
        addresses=_three_hosts(),
        address_groups=[
            AddressGroup(name="grp-a", static_members=["grp-b"]),
            AddressGroup(name="grp-b", static_members=["h1", "h2"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    res = find_duplicate_groups(snap, graph)
    names = {m.name for g in res.buckets for m in g.members}
    assert {"grp-a", "grp-b"} <= names


# --- FIX B: --location shared must target the shared scope -----------------


def test_find_duplicate_groups_location_shared_returns_shared() -> None:
    # Filtering by the shared scope must return shared groups — Location.shared()
    # (device_group=None), never a DG literally named "shared".
    snap = Snapshot(
        addresses=_three_hosts(),
        address_groups=[
            AddressGroup(name="grp-a", static_members=["h1", "h2"]),
            AddressGroup(name="grp-b", static_members=["h1", "h2"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    res = find_duplicate_groups(snap, graph, Location.shared())
    names = {m.name for g in res.buckets for m in g.members}
    assert names == {"grp-a", "grp-b"}


# --- FIX D: a static group nesting a dynamic group is unresolvable ----------


def test_resolve_group_members_nested_dynamic_is_none() -> None:
    # A static group whose member is a dynamic (runtime-only) group cannot be
    # reduced to a leaf set; the parent must propagate None, never drop it.
    snap = Snapshot(
        addresses=[_addr("h1", "10.0.0.1/32")],
        address_groups=[
            AddressGroup(name="grp", static_members=["h1", "grp-dyn"]),
            AddressGroup(name="grp-dyn", static_members=None, dynamic_filter="'t-prod'"),
        ],
    )
    graph = ReferenceGraph.build(snap)
    assert resolve_group_members(snap, graph, "grp", Location.shared()) is None


def test_find_duplicate_groups_skips_nested_dynamic() -> None:
    snap = Snapshot(
        addresses=[_addr("h1", "10.0.0.1/32")],
        address_groups=[
            AddressGroup(name="grp-x", static_members=["h1", "grp-dyn"]),
            AddressGroup(name="grp-y", static_members=["h1", "grp-dyn"]),
            AddressGroup(name="grp-dyn", static_members=None, dynamic_filter="'t-prod'"),
        ],
    )
    graph = ReferenceGraph.build(snap)
    res = find_duplicate_groups(snap, graph)
    assert {m.name for g in res.buckets for m in g.members} == set()
    assert "grp-x" in res.unresolvable_skipped
    assert "grp-y" in res.unresolvable_skipped


def test_plan_merge_group_blocks_nested_dynamic_unresolvable() -> None:
    snap = Snapshot(
        addresses=[_addr("h1", "10.0.0.1/32")],
        address_groups=[
            AddressGroup(name="grp-x", static_members=["h1", "grp-dyn"]),
            AddressGroup(name="grp-y", static_members=["h1", "grp-dyn"]),
            AddressGroup(name="grp-dyn", static_members=None, dynamic_filter="'t-prod'"),
        ],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name="grp-x", location="shared"),
        drop=ObjectRef(name="grp-y", location="shared"),
    )
    assert cs.is_blocked
    assert any("unresolvable members" in b for b in cs.blockers)
    assert cs.op_count == 0


# --- group-bucket merge (issue #4): collapse a whole duplicate bucket -------


def _bucket_snapshot() -> Snapshot:
    """One 3-member bucket {d1,d2,d3} all == 10.0.0.9/32, referenced apart.

    grp holds d2+d3 (same field, BOTH dropped when keeping d1 — forces edit
    chaining); r1 destination holds d2; nat-1 source holds d3.
    """
    return Snapshot(
        addresses=[
            _addr("d1", "10.0.0.9/32"),
            _addr("d2", "10.0.0.9/32"),
            _addr("d3", "10.0.0.9/32"),
        ],
        address_groups=[AddressGroup(name="grp", static_members=["d2", "d3"])],
        security_rules=[SecurityRule(name="r1", destination=["d2"])],
        nat_rules=[NatRule(name="nat-1", source=["d3"])],
    )


def test_plan_merge_bucket_collapses_three_toward_keep() -> None:
    snap = _bucket_snapshot()
    graph = ReferenceGraph.build(snap)
    members = [ObjectRef(name=n, location="shared") for n in ("d1", "d2", "d3")]
    cs = plan_merge_bucket(
        snap, graph, members=members, keep=ObjectRef(name="d1", location="shared")
    )
    assert not cs.is_blocked
    # Both other members are deleted in one plan.
    assert {d.name for d in cs.deletes} == {"d2", "d3"}
    # grp held d2 and d3 (both dropped) — the collapsed result must be exactly
    # [d1], not [d1, d3] with the second drop lost (proves the two edits chained
    # on the same field instead of the last one clobbering the first).
    grp_edits = [e for e in cs.reference_edits if e.referrer_name == "grp"]
    assert grp_edits[-1].after == ["d1"]
    r1 = next(e for e in cs.reference_edits if e.referrer_name == "r1")
    assert r1.after == ["d1"]
    nat = next(e for e in cs.reference_edits if e.referrer_name == "nat-1")
    assert nat.after == ["d1"]


def test_plan_merge_bucket_keep_defaults_to_first_member() -> None:
    snap = _bucket_snapshot()
    graph = ReferenceGraph.build(snap)
    members = [ObjectRef(name=n, location="shared") for n in ("d1", "d2", "d3")]
    cs = plan_merge_bucket(snap, graph, members=members)
    # Deterministic default survivor = the sorted-first bucket member (d1).
    assert {d.name for d in cs.deletes} == {"d2", "d3"}


def test_plan_merge_bucket_keep_selects_survivor() -> None:
    snap = _bucket_snapshot()
    graph = ReferenceGraph.build(snap)
    members = [ObjectRef(name=n, location="shared") for n in ("d1", "d2", "d3")]
    cs = plan_merge_bucket(
        snap, graph, members=members, keep=ObjectRef(name="d2", location="shared")
    )
    assert {d.name for d in cs.deletes} == {"d1", "d3"}


def test_plan_merge_bucket_invalid_keep_is_input_error() -> None:
    snap = _bucket_snapshot()
    graph = ReferenceGraph.build(snap)
    members = [ObjectRef(name=n, location="shared") for n in ("d1", "d2", "d3")]
    with pytest.raises(PscError) as exc:
        plan_merge_bucket(
            snap, graph, members=members, keep=ObjectRef(name="not-in-bucket", location="shared")
        )
    assert exc.value.error_type is ErrorType.INPUT


def test_plan_merge_bucket_surfaces_blocker_and_gates_plan() -> None:
    # d3 is referenced by a NAT translation field (no flat member list): its
    # repoint is unmappable while d3 is being deleted -> hard blocker. The whole
    # collapsed plan is gated (zero ops), exactly like the pairwise path.
    snap = Snapshot(
        addresses=[
            _addr("d1", "10.0.0.9/32"),
            _addr("d2", "10.0.0.9/32"),
            _addr("d3", "10.0.0.9/32"),
        ],
        nat_rules=[
            NatRule(name="nat-1", source_translation=["d3"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    members = [ObjectRef(name=n, location="shared") for n in ("d1", "d2", "d3")]
    cs = plan_merge_bucket(
        snap, graph, members=members, keep=ObjectRef(name="d1", location="shared")
    )
    assert cs.is_blocked
    assert cs.op_count == 0


def test_plan_merge_bucket_apply_roundtrips() -> None:
    xml = """<?xml version="1.0"?>
<config version="11.0.0">
  <shared>
    <address>
      <entry name="d1"><ip-netmask>10.0.0.9/32</ip-netmask></entry>
      <entry name="d2"><ip-netmask>10.0.0.9/32</ip-netmask></entry>
      <entry name="d3"><ip-netmask>10.0.0.9/32</ip-netmask></entry>
    </address>
    <address-group>
      <entry name="grp"><static><member>d1</member><member>d2</member></static></entry>
    </address-group>
    <pre-rulebase>
      <security>
        <rules>
          <entry name="r1"><destination><member>d3</member></destination></entry>
        </rules>
      </security>
    </pre-rulebase>
  </shared>
</config>
"""
    snap = parse_config(xml)
    graph = ReferenceGraph.build(snap)
    members = [ObjectRef(name=n, location="shared") for n in ("d1", "d2", "d3")]
    cs = plan_merge_bucket(
        snap, graph, members=members, keep=ObjectRef(name="d1", location="shared")
    )
    new_snap = parse_config(apply_changeset(xml, cs))
    assert {a.name for a in new_snap.addresses} == {"d1"}
    grp = next(g for g in new_snap.address_groups if g.name == "grp")
    assert grp.static_members == ["d1"]
    r1 = next(r for r in new_snap.security_rules if r.name == "r1")
    assert r1.destination == ["d1"]
