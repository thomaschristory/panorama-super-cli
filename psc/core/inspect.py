"""Inspect ("open") an object: expand it into a member tree + effective leaf set.

Answers *"what does this object actually contain?"* — the inverse of `refs`
(where-used). A plain address is just its value; a (possibly nested)
address-group is the whole member tree plus the deduped set of leaf addresses it
resolves to. Groups, service-groups, tags (reverse lookup) and rules
(field-grouped) all expand through one engine. Pure read: no `ChangeSet`, no
mutation.

Member resolution reuses `ReferenceGraph`, which already models PAN-OS name
shadowing, resolves dynamic-address-group tag membership, and is cycle-safe.
Every unresolvable member (dynamic filter, dangling ref, cycle) is surfaced and
flagged, never silently dropped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from psc.core.models import (
    Address,
    AddressGroup,
    Location,
    NatRule,
    PolicyRule,
    SecurityRule,
    Service,
    ServiceGroup,
    Snapshot,
    Tag,
)
from psc.core.normalize import normalize_address, service_key
from psc.core.refs import ReferenceGraph, Target, dag_filter_tags

ADDR_NS = "address"
SVC_NS = "service"


class NodeStatus(str, Enum):
    """How a tree node resolved."""

    OK = "ok"  # a resolved leaf or group
    DYNAMIC = "dynamic"  # dynamic address-group (runtime-defined membership)
    DANGLING = "dangling"  # member name resolves to no object
    CYCLE = "cycle"  # group already being expanded higher in the tree


class InspectNode(BaseModel):
    kind: str  # address | address-group | service | service-group | tag | rule | field
    name: str
    location: str
    detail: str = ""  # leaf value / filter / port spec / "" for a field grouping
    status: NodeStatus = NodeStatus.OK
    children: list[InspectNode] = Field(default_factory=list)


class ObjectView(BaseModel):
    kind: str
    name: str
    location: str
    detail: str = ""
    tree: InspectNode  # root node (the object itself)
    effective_leaves: list[str] | None = None  # deduped flat set; None when N/A
    effective_complete: bool = True  # False when a member was unresolvable
    warnings: list[str] = Field(default_factory=list)


InspectNode.model_rebuild()


@dataclass
class _Acc:
    """Accumulator threaded through a group expansion: the deduped leaf set
    (dedup key -> display value), any warnings, and whether the set is whole."""

    leaves: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    complete: bool = True


# --- snapshot lookups -------------------------------------------------------


def _find_addr(snapshot: Snapshot, name: str, loc: Location) -> Address | None:
    return next((a for a in snapshot.addresses if a.name == name and a.location == loc), None)


def _find_group(snapshot: Snapshot, name: str, loc: Location) -> AddressGroup | None:
    return next(
        (g for g in snapshot.address_groups if g.name == name and g.location == loc),
        None,
    )


def _find_service(snapshot: Snapshot, name: str, loc: Location) -> Service | None:
    return next((s for s in snapshot.services if s.name == name and s.location == loc), None)


def _find_service_group(snapshot: Snapshot, name: str, loc: Location) -> ServiceGroup | None:
    return next(
        (g for g in snapshot.service_groups if g.name == name and g.location == loc),
        None,
    )


# --- leaf display + dedup key ----------------------------------------------


def _service_display(svc: Service) -> str:
    disp = f"{svc.protocol}/{svc.destination_port}" if svc.destination_port else svc.protocol
    if svc.source_port:
        disp += f" (src {svc.source_port})"
    return disp


def _addr_leaf(snapshot: Snapshot, target: Target) -> tuple[str, str] | None:
    """`(dedup_key, display)` for an address target, or None if missing/malformed."""
    addr = _find_addr(snapshot, target.name, target.location)
    if addr is None:
        return None
    nv = normalize_address(addr)
    if nv is None:
        return None
    return (nv.exact_key(), addr.value)


def _svc_leaf(snapshot: Snapshot, target: Target) -> tuple[str, str] | None:
    svc = _find_service(snapshot, target.name, target.location)
    if svc is None:
        return None
    return (service_key(svc), _service_display(svc))


def _sorted_leaves(leaves: dict[str, str]) -> list[str]:
    return sorted(leaves.values())


# --- address-group expansion -----------------------------------------------


def _addr_group_node(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    target: Target,
    seen: frozenset[tuple[str, str]],
    acc: _Acc,
) -> InspectNode:
    g = _find_group(snapshot, target.name, target.location)
    if g is None:  # resolved to a group target that isn't present — treat as dangling
        acc.complete = False
        acc.warnings.append(f"unresolvable group '{target.name}'")
        return InspectNode(
            kind="address-group",
            name=target.name,
            location=target.location.name,
            status=NodeStatus.DANGLING,
        )
    if g.is_dynamic:
        return _dynamic_group_node(snapshot, graph, g, acc)
    key = (g.name, g.location.name)
    if key in seen:  # cycle: the outer frame already carries these members
        return InspectNode(
            kind="address-group",
            name=g.name,
            location=g.location.name,
            status=NodeStatus.CYCLE,
        )
    children = _expand_addr_members(
        snapshot, graph, g.location, g.static_members or [], seen | {key}, acc
    )
    return InspectNode(
        kind="address-group", name=g.name, location=g.location.name, children=children
    )


def _dynamic_group_node(
    snapshot: Snapshot, graph: ReferenceGraph, g: AddressGroup, acc: _Acc
) -> InspectNode:
    # A dynamic group's membership is evaluated on the device at runtime; the
    # DAG members we list are only those matched within this snapshot, so the
    # effective set can never be declared complete.
    acc.complete = False
    acc.warnings.append(
        f"dynamic group '{g.name}': membership is runtime-defined; "
        "listed members reflect this config only"
    )
    dag = Target("address-group", g.name, g.location)
    children = []
    for mt in graph.dag_members(dag):
        leaf = _addr_leaf(snapshot, mt)
        if leaf is None:  # matched a real object whose value can't be parsed
            acc.warnings.append(f"unparseable value for '{mt.name}'")
            children.append(
                InspectNode(
                    kind="address",
                    name=mt.name,
                    location=mt.location.name,
                    status=NodeStatus.DANGLING,
                )
            )
            continue
        # The members matched *in this snapshot* are indicative leaves — recorded
        # so they surface in `effective_leaves`, but `complete` stays False above
        # because the device may match more at runtime.
        acc.leaves[leaf[0]] = leaf[1]
        children.append(
            InspectNode(
                kind="address",
                name=mt.name,
                location=mt.location.name,
                detail=leaf[1],
            )
        )
    return InspectNode(
        kind="address-group",
        name=g.name,
        location=g.location.name,
        detail=g.dynamic_filter or "",
        status=NodeStatus.DYNAMIC,
        children=children,
    )


def _expand_addr_members(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    loc: Location,
    members: list[str],
    seen: frozenset[tuple[str, str]],
    acc: _Acc,
) -> list[InspectNode]:
    nodes: list[InspectNode] = []
    for m in members:
        target = graph.resolve(ADDR_NS, m, loc)
        if target is None:
            acc.complete = False
            acc.warnings.append(f"dangling member '{m}'")
            nodes.append(
                InspectNode(kind="address", name=m, location=loc.name, status=NodeStatus.DANGLING)
            )
            continue
        if target.kind == "address-group":
            nodes.append(_addr_group_node(snapshot, graph, target, seen, acc))
            continue
        leaf = _addr_leaf(snapshot, target)
        if leaf is None:
            acc.complete = False
            acc.warnings.append(f"unresolvable value for '{target.name}'")
            nodes.append(
                InspectNode(
                    kind="address",
                    name=target.name,
                    location=target.location.name,
                    status=NodeStatus.DANGLING,
                )
            )
            continue
        acc.leaves[leaf[0]] = leaf[1]
        nodes.append(
            InspectNode(
                kind="address",
                name=target.name,
                location=target.location.name,
                detail=leaf[1],
            )
        )
    return nodes


# --- service-group expansion -----------------------------------------------


def _svc_group_node(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    target: Target,
    seen: frozenset[tuple[str, str]],
    acc: _Acc,
) -> InspectNode:
    sg = _find_service_group(snapshot, target.name, target.location)
    if sg is None:
        acc.complete = False
        acc.warnings.append(f"unresolvable service-group '{target.name}'")
        return InspectNode(
            kind="service-group",
            name=target.name,
            location=target.location.name,
            status=NodeStatus.DANGLING,
        )
    key = (sg.name, sg.location.name)
    if key in seen:
        return InspectNode(
            kind="service-group",
            name=sg.name,
            location=sg.location.name,
            status=NodeStatus.CYCLE,
        )
    children = _expand_svc_members(snapshot, graph, sg.location, sg.members, seen | {key}, acc)
    return InspectNode(
        kind="service-group", name=sg.name, location=sg.location.name, children=children
    )


def _expand_svc_members(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    loc: Location,
    members: list[str],
    seen: frozenset[tuple[str, str]],
    acc: _Acc,
) -> list[InspectNode]:
    nodes: list[InspectNode] = []
    for m in members:
        target = graph.resolve(SVC_NS, m, loc)
        if target is None:
            acc.complete = False
            acc.warnings.append(f"dangling member '{m}'")
            nodes.append(
                InspectNode(kind="service", name=m, location=loc.name, status=NodeStatus.DANGLING)
            )
            continue
        if target.kind == "service-group":
            nodes.append(_svc_group_node(snapshot, graph, target, seen, acc))
            continue
        leaf = _svc_leaf(snapshot, target)
        if leaf is None:
            acc.complete = False
            acc.warnings.append(f"unresolvable value for '{target.name}'")
            nodes.append(
                InspectNode(
                    kind="service",
                    name=target.name,
                    location=target.location.name,
                    status=NodeStatus.DANGLING,
                )
            )
            continue
        acc.leaves[leaf[0]] = leaf[1]
        nodes.append(
            InspectNode(
                kind="service",
                name=target.name,
                location=target.location.name,
                detail=leaf[1],
            )
        )
    return nodes


# --- rule field expansion ---------------------------------------------------
# Rule members differ from group members: `any` and predefined names (e.g.
# `application-default`, `service-http`) are legitimate non-objects, so an
# unresolved rule member is shown plainly rather than flagged as dangling. A
# member that DOES resolve to a group still recurses (nested dangling inside
# that group is real and stays flagged).


def _expand_rule_addr_members(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    loc: Location,
    members: list[str],
    acc: _Acc,
) -> list[InspectNode]:
    nodes: list[InspectNode] = []
    for m in members:
        if m == "any":
            nodes.append(InspectNode(kind="address", name="any", location=loc.name, detail="(any)"))
            continue
        target = graph.resolve(ADDR_NS, m, loc)
        if target is None:
            nodes.append(
                InspectNode(
                    kind="address", name=m, location=loc.name, detail="(external/predefined)"
                )
            )
            continue
        if target.kind == "address-group":
            nodes.append(_addr_group_node(snapshot, graph, target, frozenset(), acc))
            continue
        leaf = _addr_leaf(snapshot, target)
        if leaf is None:  # resolved to a real object with an unparseable value
            acc.warnings.append(f"unparseable value for '{target.name}'")
            nodes.append(
                InspectNode(
                    kind="address",
                    name=target.name,
                    location=target.location.name,
                    status=NodeStatus.DANGLING,
                )
            )
            continue
        nodes.append(
            InspectNode(
                kind="address",
                name=target.name,
                location=target.location.name,
                detail=leaf[1],
            )
        )
    return nodes


def _expand_rule_svc_members(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    loc: Location,
    members: list[str],
    acc: _Acc,
) -> list[InspectNode]:
    nodes: list[InspectNode] = []
    for m in members:
        if m in ("any", "application-default"):
            nodes.append(InspectNode(kind="service", name=m, location=loc.name, detail=f"({m})"))
            continue
        target = graph.resolve(SVC_NS, m, loc)
        if target is None:
            nodes.append(
                InspectNode(
                    kind="service", name=m, location=loc.name, detail="(external/predefined)"
                )
            )
            continue
        if target.kind == "service-group":
            nodes.append(_svc_group_node(snapshot, graph, target, frozenset(), acc))
            continue
        leaf = _svc_leaf(snapshot, target)
        if leaf is None:  # resolved to a real object with an unparseable value
            acc.warnings.append(f"unparseable value for '{target.name}'")
            nodes.append(
                InspectNode(
                    kind="service",
                    name=target.name,
                    location=target.location.name,
                    status=NodeStatus.DANGLING,
                )
            )
            continue
        nodes.append(
            InspectNode(
                kind="service",
                name=target.name,
                location=target.location.name,
                detail=leaf[1],
            )
        )
    return nodes


def _rule_view(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    kind: str,
    name: str,
    loc: Location,
    fields: list[tuple[str, str, list[str]]],
) -> ObjectView:
    acc = _Acc()
    children: list[InspectNode] = []
    for label, ns, members in fields:
        if not members:
            continue
        if ns == ADDR_NS:
            fnodes = _expand_rule_addr_members(snapshot, graph, loc, members, acc)
        else:
            fnodes = _expand_rule_svc_members(snapshot, graph, loc, members, acc)
        children.append(InspectNode(kind="field", name=label, location=loc.name, children=fnodes))
    root = InspectNode(kind=kind, name=name, location=loc.name, children=children)
    return ObjectView(
        kind=kind,
        name=name,
        location=loc.name,
        tree=root,
        effective_leaves=None,
        warnings=acc.warnings,
    )


# --- per-kind views ---------------------------------------------------------


def _view_address(a: Address) -> ObjectView:
    nv = normalize_address(a)
    node = InspectNode(kind="address", name=a.name, location=a.location.name, detail=a.value)
    warnings = [] if nv is not None else [f"unparseable value '{a.value}'"]
    return ObjectView(
        kind="address",
        name=a.name,
        location=a.location.name,
        detail=a.value,
        tree=node,
        effective_leaves=[a.value],
        effective_complete=nv is not None,
        warnings=warnings,
    )


def _view_service(s: Service) -> ObjectView:
    disp = _service_display(s)
    node = InspectNode(kind="service", name=s.name, location=s.location.name, detail=disp)
    return ObjectView(
        kind="service",
        name=s.name,
        location=s.location.name,
        detail=disp,
        tree=node,
        effective_leaves=[disp],
    )


def _view_address_group(snapshot: Snapshot, graph: ReferenceGraph, g: AddressGroup) -> ObjectView:
    acc = _Acc()
    root = _addr_group_node(
        snapshot, graph, Target("address-group", g.name, g.location), frozenset(), acc
    )
    return ObjectView(
        kind="address-group",
        name=g.name,
        location=g.location.name,
        detail=root.detail,
        tree=root,
        effective_leaves=_sorted_leaves(acc.leaves),
        effective_complete=acc.complete,
        warnings=acc.warnings,
    )


def _view_service_group(snapshot: Snapshot, graph: ReferenceGraph, sg: ServiceGroup) -> ObjectView:
    acc = _Acc()
    root = _svc_group_node(
        snapshot, graph, Target("service-group", sg.name, sg.location), frozenset(), acc
    )
    return ObjectView(
        kind="service-group",
        name=sg.name,
        location=sg.location.name,
        tree=root,
        effective_leaves=_sorted_leaves(acc.leaves),
        effective_complete=acc.complete,
        warnings=acc.warnings,
    )


def _tag_carriers(snapshot: Snapshot, name: str) -> list[InspectNode]:
    nodes: list[InspectNode] = []
    seen: set[tuple[str, str]] = set()  # (name, location) — avoid double-listing a group
    kinds: list[tuple[str, Sequence[Any]]] = [
        ("address", snapshot.addresses),
        ("address-group", snapshot.address_groups),
        ("service", snapshot.services),
        ("service-group", snapshot.service_groups),
        ("security-rule", snapshot.security_rules),
        ("nat-rule", snapshot.nat_rules),
    ]
    for kind, objs in kinds:
        for o in objs:
            if name in o.tags:
                seen.add((o.name, o.location.name))
                nodes.append(InspectNode(kind=kind, name=o.name, location=o.location.name))
    for r in snapshot.policy_rules:
        if name in r.tags:
            nodes.append(InspectNode(kind=r.referrer_kind, name=r.name, location=r.location.name))
    # A tag is also "carried" when a dynamic address-group's filter selects on it
    # — the most common real use of tags in PAN-OS. `refs` counts this as usage,
    # so the inspect view must too, or `show <tag>` looks unused when it isn't.
    for ag in snapshot.address_groups:
        if (
            ag.dynamic_filter
            and name in dag_filter_tags(ag.dynamic_filter)
            and (ag.name, ag.location.name) not in seen
        ):
            nodes.append(
                InspectNode(
                    kind="address-group",
                    name=ag.name,
                    location=ag.location.name,
                    detail=ag.dynamic_filter,
                    status=NodeStatus.DYNAMIC,
                )
            )
    return nodes


def _view_tag(snapshot: Snapshot, t: Tag) -> ObjectView:
    root = InspectNode(
        kind="tag",
        name=t.name,
        location=t.location.name,
        detail=t.color or "",
        children=_tag_carriers(snapshot, t.name),
    )
    return ObjectView(
        kind="tag",
        name=t.name,
        location=t.location.name,
        detail=t.color or "",
        tree=root,
        effective_leaves=None,
    )


def _view_security_rule(snapshot: Snapshot, graph: ReferenceGraph, r: SecurityRule) -> ObjectView:
    return _rule_view(
        snapshot,
        graph,
        "security-rule",
        r.name,
        r.location,
        [
            ("source", ADDR_NS, r.source),
            ("destination", ADDR_NS, r.destination),
            ("service", SVC_NS, r.service),
        ],
    )


def _view_nat_rule(snapshot: Snapshot, graph: ReferenceGraph, r: NatRule) -> ObjectView:
    dst_xlat = [r.destination_translation] if r.destination_translation else []
    return _rule_view(
        snapshot,
        graph,
        "nat-rule",
        r.name,
        r.location,
        [
            ("source", ADDR_NS, r.source),
            ("destination", ADDR_NS, r.destination),
            ("service", SVC_NS, [r.service]),
            ("source-translation", ADDR_NS, r.source_translation),
            ("destination-translation", ADDR_NS, dst_xlat),
        ],
    )


def _view_policy_rule(snapshot: Snapshot, graph: ReferenceGraph, r: PolicyRule) -> ObjectView:
    return _rule_view(
        snapshot,
        graph,
        r.referrer_kind,
        r.name,
        r.location,
        [
            ("source", ADDR_NS, r.source),
            ("destination", ADDR_NS, r.destination),
            ("service", SVC_NS, r.service),
            ("nexthop", ADDR_NS, [r.nexthop] if r.nexthop else []),
        ],
    )


# --- entry point ------------------------------------------------------------


def inspect_object(
    snapshot: Snapshot, name: str, *, scope: Location | None = None
) -> list[ObjectView]:
    """Expanded view of every object named `name` (any kind, any visible
    location), mirroring `find_object`'s name-match semantics but also covering
    rules. One `ObjectView` per matching object; empty list when nothing
    matches. `scope` honours the same visibility rule as `find` (a device-group
    sees its ancestors and `shared`)."""
    graph = ReferenceGraph.build(snapshot)
    visible = snapshot.visible_location_names(scope)

    def vis(loc: Location) -> bool:
        return visible is None or loc.name in visible

    views: list[ObjectView] = []

    def collect(objs: Iterable[Any], view_of: Callable[[Any], ObjectView]) -> None:
        for o in objs:
            if o.name == name and vis(o.location):
                views.append(view_of(o))

    collect(snapshot.addresses, _view_address)
    collect(snapshot.address_groups, lambda g: _view_address_group(snapshot, graph, g))
    collect(snapshot.services, _view_service)
    collect(snapshot.service_groups, lambda sg: _view_service_group(snapshot, graph, sg))
    collect(snapshot.tags, lambda t: _view_tag(snapshot, t))
    collect(snapshot.security_rules, lambda r: _view_security_rule(snapshot, graph, r))
    collect(snapshot.nat_rules, lambda r: _view_nat_rule(snapshot, graph, r))
    collect(snapshot.policy_rules, lambda r: _view_policy_rule(snapshot, graph, r))
    return views
