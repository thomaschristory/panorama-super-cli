"""Resolve an IP / CIDR / range / FQDN to the objects that represent it.

Answers the headline question — *"is this IP already an object, and which
ones?"* — for a single target or a whole list. Returns exact matches,
broader objects that contain it, narrower objects inside it, and the
address-groups that would therefore carry it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from psc.core.models import Address, Location, Snapshot
from psc.core.normalize import MatchKind, Query, match, normalize_address, parse_query


class AddressMatch(BaseModel):
    name: str
    location: str
    type: str
    value: str
    match: MatchKind


class GroupMatch(BaseModel):
    name: str
    location: str
    via: list[str] = Field(default_factory=list)


class FindResult(BaseModel):
    query: str
    kind: str  # "ip" | "cidr" | "range" | "fqdn"
    exists: bool  # at least one EXACT match
    matches: list[AddressMatch] = Field(default_factory=list)
    groups: list[GroupMatch] = Field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.matches)


def _query_kind(q: Query) -> str:
    if q.fqdn is not None:
        return "fqdn"
    if q.range is not None:
        return "range"
    if q.network is not None and q.network.prefixlen in (32, 128):
        return "ip"
    return "cidr"


def _visible_names(snapshot: Snapshot, scope: Location | None) -> set[str] | None:
    """Location names visible from `scope`: the device-group, every ancestor,
    and `shared`. `None` means unscoped (every location)."""
    if scope is None:
        return None
    return {loc.name for loc in snapshot.ancestors(scope)}


def find_ip(snapshot: Snapshot, raw: str, scope: Location | None = None) -> FindResult:
    """Find every address object/group matching `raw` within `scope` (which
    includes the scoped device-group's ancestors and `shared`)."""
    query = parse_query(raw)
    visible = _visible_names(snapshot, scope)
    matched: list[tuple[Address, MatchKind]] = []
    for addr in snapshot.addresses:
        if visible is not None and addr.location.name not in visible:
            continue
        nv = normalize_address(addr)
        if nv is None:
            continue
        mk = match(query, nv)
        if mk is not None:
            matched.append((addr, mk))

    matches = [
        AddressMatch(
            name=a.name,
            location=a.location.name,
            type=a.type.value,
            value=a.value,
            match=mk,
        )
        for a, mk in sorted(matched, key=lambda t: (t[1].value, t[0].location.name, t[0].name))
    ]

    # Identity (not just name) of every matched object, plus a per-location name
    # index so a group member can be resolved to the object it actually denotes.
    matched_keys = {(a.location.name, a.name) for a, _ in matched}
    names_by_loc: dict[str, set[str]] = {}
    for a in snapshot.addresses:
        names_by_loc.setdefault(a.location.name, set()).add(a.name)

    def _resolve_member(group_loc: Location, member: str) -> tuple[str, str] | None:
        # PAN-OS name resolution: a name binds to its closest definition up the
        # device-group chain (local shadows ancestors shadow shared). Without
        # this, a group in DG `prod` listing `H-web` could falsely match a
        # *shared* `H-web` of a different value when `prod` defines its own.
        for loc in snapshot.ancestors(group_loc):
            if member in names_by_loc.get(loc.name, set()):
                return (loc.name, member)
        return None  # nested group or dangling — not a direct address here

    groups: list[GroupMatch] = []
    for ag in snapshot.address_groups:
        if (visible is not None and ag.location.name not in visible) or not ag.static_members:
            continue
        via = [
            m
            for m in ag.static_members
            if (_resolve_member(ag.location, m) or ("", "")) in matched_keys
        ]
        if via:
            groups.append(GroupMatch(name=ag.name, location=ag.location.name, via=via))

    return FindResult(
        query=raw,
        kind=_query_kind(query),
        exists=any(mk is MatchKind.EXACT for _, mk in matched),
        matches=matches,
        groups=sorted(groups, key=lambda g: (g.location, g.name)),
    )


def find_ips(
    snapshot: Snapshot, raws: list[str], scope: Location | None = None
) -> list[FindResult]:
    return [find_ip(snapshot, r, scope) for r in raws]


class ObjectHit(BaseModel):
    kind: str
    name: str
    location: str
    detail: str


def find_object(snapshot: Snapshot, name: str) -> list[ObjectHit]:
    """Find objects named `name` across all kinds and locations (exact name)."""
    hits: list[ObjectHit] = []
    for a in snapshot.addresses:
        if a.name == name:
            hits.append(
                ObjectHit(
                    kind="address",
                    name=a.name,
                    location=a.location.name,
                    detail=f"{a.type.value} {a.value}",
                )
            )
    for ag in snapshot.address_groups:
        if ag.name == name:
            detail = ag.dynamic_filter or f"static[{len(ag.static_members or [])}]"
            hits.append(
                ObjectHit(
                    kind="address-group", name=ag.name, location=ag.location.name, detail=detail
                )
            )
    for s in snapshot.services:
        if s.name == name:
            hits.append(
                ObjectHit(
                    kind="service",
                    name=s.name,
                    location=s.location.name,
                    detail=f"{s.protocol}/{s.destination_port}",
                )
            )
    for sg in snapshot.service_groups:
        if sg.name == name:
            hits.append(
                ObjectHit(
                    kind="service-group",
                    name=sg.name,
                    location=sg.location.name,
                    detail=f"members[{len(sg.members)}]",
                )
            )
    for t in snapshot.tags:
        if t.name == name:
            hits.append(
                ObjectHit(kind="tag", name=t.name, location=t.location.name, detail=t.color or "")
            )
    return hits
