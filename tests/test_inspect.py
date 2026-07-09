from __future__ import annotations

from psc.core.inspect import NodeStatus, inspect_object
from psc.core.models import (
    Address,
    AddressGroup,
    AddressType,
    Location,
    NatRule,
    SecurityRule,
    Service,
    ServiceGroup,
    Snapshot,
    Tag,
)


def _node_by_name(nodes, name):
    return next(n for n in nodes if n.name == name)


def test_address_leaf() -> None:
    snap = Snapshot(addresses=[Address(name="h", type=AddressType.IP_NETMASK, value="10.0.0.1")])
    (view,) = inspect_object(snap, "h")
    assert view.kind == "address"
    assert view.tree.detail == "10.0.0.1"
    assert view.tree.children == []
    assert view.effective_leaves == ["10.0.0.1"]
    assert view.effective_complete is True


def test_service_leaf() -> None:
    snap = Snapshot(services=[Service(name="web", protocol="tcp", destination_port="443")])
    (view,) = inspect_object(snap, "web")
    assert view.kind == "service"
    assert view.effective_leaves == ["tcp/443"]


def test_static_group_tree_and_effective_leaves() -> None:
    snap = Snapshot(
        addresses=[
            Address(name="a", type=AddressType.IP_NETMASK, value="10.0.0.1"),
            Address(name="b", type=AddressType.IP_NETMASK, value="10.0.0.2"),
        ],
        address_groups=[AddressGroup(name="g", static_members=["a", "b"])],
    )
    (view,) = inspect_object(snap, "g")
    assert view.kind == "address-group"
    assert {c.name for c in view.tree.children} == {"a", "b"}
    assert view.effective_leaves == ["10.0.0.1", "10.0.0.2"]
    assert view.effective_complete is True


def test_nested_group_dedups_across_branches() -> None:
    # `outer` contains `inner` and `a`; `inner` also contains `a`. The effective
    # leaf set must dedup `a` even though it is reached via two branches.
    snap = Snapshot(
        addresses=[
            Address(name="a", type=AddressType.IP_NETMASK, value="10.0.0.1"),
            Address(name="c", type=AddressType.IP_NETMASK, value="10.0.0.3"),
        ],
        address_groups=[
            AddressGroup(name="inner", static_members=["a"]),
            AddressGroup(name="outer", static_members=["inner", "a", "c"]),
        ],
    )
    (view,) = inspect_object(snap, "outer")
    inner = _node_by_name(view.tree.children, "inner")
    assert inner.kind == "address-group"
    assert view.effective_leaves == ["10.0.0.1", "10.0.0.3"]


def test_cycle_is_flagged_not_infinite() -> None:
    snap = Snapshot(
        addresses=[Address(name="a", type=AddressType.IP_NETMASK, value="10.0.0.1")],
        address_groups=[
            AddressGroup(name="g1", static_members=["a", "g2"]),
            AddressGroup(name="g2", static_members=["g1"]),
        ],
    )
    (view,) = inspect_object(snap, "g1")
    g2 = _node_by_name(view.tree.children, "g2")
    back = _node_by_name(g2.children, "g1")
    assert back.status is NodeStatus.CYCLE
    assert back.children == []
    # A cycle is not a data error; the reachable leaf set is still known.
    assert view.effective_leaves == ["10.0.0.1"]
    assert view.effective_complete is True


def test_dangling_member_flagged_and_incomplete() -> None:
    snap = Snapshot(
        addresses=[Address(name="a", type=AddressType.IP_NETMASK, value="10.0.0.1")],
        address_groups=[AddressGroup(name="g", static_members=["a", "ghost"])],
    )
    (view,) = inspect_object(snap, "g")
    ghost = _node_by_name(view.tree.children, "ghost")
    assert ghost.status is NodeStatus.DANGLING
    assert view.effective_complete is False
    assert any("ghost" in w for w in view.warnings)
    assert view.effective_leaves == ["10.0.0.1"]


def test_dynamic_group_lists_dag_members_but_incomplete() -> None:
    snap = Snapshot(
        addresses=[
            Address(name="a", type=AddressType.IP_NETMASK, value="10.0.0.1", tags=["web"]),
        ],
        address_groups=[AddressGroup(name="dag", dynamic_filter="'web'")],
        tags=[Tag(name="web")],
    )
    (view,) = inspect_object(snap, "dag")
    assert view.tree.status is NodeStatus.DYNAMIC
    assert view.tree.detail == "'web'"
    assert {c.name for c in view.tree.children} == {"a"}
    # The snapshot-matched member is indicative but still surfaces as a leaf...
    assert view.effective_leaves == ["10.0.0.1"]
    # ...while completeness stays False (the device may match more at runtime).
    assert view.effective_complete is False
    assert any("dynamic" in w.lower() for w in view.warnings)


def test_tag_used_only_in_dag_filter_lists_the_group() -> None:
    # A tag referenced solely by a dynamic address-group's filter (never carried
    # directly) must still show that group as a carrier — matching `refs`.
    snap = Snapshot(
        address_groups=[AddressGroup(name="dag", dynamic_filter="'prod'")],
        tags=[Tag(name="prod")],
    )
    (view,) = inspect_object(snap, "prod")
    assert view.kind == "tag"
    dag = _node_by_name(view.tree.children, "dag")
    assert dag.kind == "address-group"
    assert dag.status is NodeStatus.DYNAMIC


def test_rule_member_with_malformed_value_is_flagged() -> None:
    # A rule references a real address object whose value can't be parsed: it
    # must be flagged (dangling), not shown as a healthy empty-detail node.
    snap = Snapshot(
        addresses=[Address(name="bad", type=AddressType.IP_NETMASK, value="10.0.0.0/33")],
        security_rules=[SecurityRule(name="r1", source=["bad"], destination=["any"])],
    )
    (view,) = inspect_object(snap, "r1")
    src = _node_by_name(view.tree.children, "source")
    bad = _node_by_name(src.children, "bad")
    assert bad.status is NodeStatus.DANGLING
    assert any("bad" in w for w in view.warnings)


def test_shadowing_resolves_local_over_shared() -> None:
    dg = Location.dg("prod")
    snap = Snapshot(
        addresses=[
            Address(name="h", type=AddressType.IP_NETMASK, value="10.0.0.1"),
            Address(name="h", type=AddressType.IP_NETMASK, value="10.9.9.9", location=dg),
        ],
        address_groups=[AddressGroup(name="g", static_members=["h"], location=dg)],
        device_groups=["prod"],
    )
    (view,) = inspect_object(snap, "g")
    # `g` in `prod` referencing `h` must resolve to the local h (10.9.9.9),
    # not the shared one.
    assert view.effective_leaves == ["10.9.9.9"]


def test_service_group_nested_dedups() -> None:
    snap = Snapshot(
        services=[
            Service(name="s1", protocol="tcp", destination_port="80"),
            Service(name="s2", protocol="tcp", destination_port="443"),
        ],
        service_groups=[
            ServiceGroup(name="inner", members=["s1"]),
            ServiceGroup(name="outer", members=["inner", "s1", "s2"]),
        ],
    )
    (view,) = inspect_object(snap, "outer")
    assert view.effective_leaves == ["tcp/443", "tcp/80"]


def test_tag_reverse_lookup_lists_carriers() -> None:
    snap = Snapshot(
        addresses=[
            Address(name="a", type=AddressType.IP_NETMASK, value="10.0.0.1", tags=["web"]),
            Address(name="b", type=AddressType.IP_NETMASK, value="10.0.0.2"),
        ],
        address_groups=[AddressGroup(name="g", static_members=["a"], tags=["web"])],
        tags=[Tag(name="web", color="red")],
    )
    (view,) = inspect_object(snap, "web")
    assert view.kind == "tag"
    assert view.effective_leaves is None
    assert {c.name for c in view.tree.children} == {"a", "g"}


def test_security_rule_expands_fields() -> None:
    snap = Snapshot(
        addresses=[Address(name="a", type=AddressType.IP_NETMASK, value="10.0.0.1")],
        services=[Service(name="web", protocol="tcp", destination_port="443")],
        security_rules=[
            SecurityRule(name="r1", source=["a"], destination=["any"], service=["web"])
        ],
    )
    (view,) = inspect_object(snap, "r1")
    assert view.kind == "security-rule"
    src = _node_by_name(view.tree.children, "source")
    assert src.kind == "field"
    assert {c.name for c in src.children} == {"a"}
    svc = _node_by_name(view.tree.children, "service")
    assert {c.name for c in svc.children} == {"web"}
    assert view.effective_leaves is None


def test_nat_rule_found_and_expanded() -> None:
    snap = Snapshot(
        addresses=[Address(name="a", type=AddressType.IP_NETMASK, value="10.0.0.1")],
        nat_rules=[NatRule(name="n1", source=["a"], destination=["any"])],
    )
    (view,) = inspect_object(snap, "n1")
    assert view.kind == "nat-rule"
    src = _node_by_name(view.tree.children, "source")
    assert {c.name for c in src.children} == {"a"}


def test_name_matching_multiple_objects_yields_multiple_views() -> None:
    dg = Location.dg("prod")
    snap = Snapshot(
        addresses=[
            Address(name="x", type=AddressType.IP_NETMASK, value="10.0.0.1"),
            Address(name="x", type=AddressType.IP_NETMASK, value="10.9.9.9", location=dg),
        ],
        device_groups=["prod"],
    )
    views = inspect_object(snap, "x")
    assert len(views) == 2
    assert {v.location for v in views} == {"shared", "prod"}


def test_no_match_returns_empty() -> None:
    assert inspect_object(Snapshot(), "nope") == []
