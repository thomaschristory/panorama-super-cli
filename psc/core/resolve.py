"""Resolve an IP / CIDR / range / FQDN to the objects that represent it.

Answers the headline question — *"is this IP already an object, and which
ones?"* — for a single target or a whole list. Returns exact matches,
broader objects that contain it, narrower objects inside it, and the
address-groups that would therefore carry it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from psc.core.models import Address, AddressType, Location, Snapshot
from psc.core.normalize import MatchKind, Query, match, normalize_address, parse_query
from psc.core.resolver import Resolver


class AddressMatch(BaseModel):
    name: str
    location: str
    type: str
    value: str
    match: MatchKind
    tags: list[str] = Field(default_factory=list)


class GroupMatch(BaseModel):
    name: str
    location: str
    via: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class FindResult(BaseModel):
    query: str
    kind: str  # "ip" | "cidr" | "range" | "fqdn"
    exists: bool  # at least one EXACT match
    matches: list[AddressMatch] = Field(default_factory=list)
    groups: list[GroupMatch] = Field(default_factory=list)
    fqdn_resolution_failures: int = 0
    """Count of FQDN objects skipped because DNS resolution failed (only ever
    non-zero when `--resolve-fqdn` is on); surfaced as a warning, never fatal."""

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


def _fqdn_match(query: Query, resolved: set[str]) -> MatchKind | None:
    """Best relation between an IP `query` and an FQDN's `resolved` IP set.

    Each resolved IP is reused through the same canonicalization as every other
    address (a synthetic ip-netmask object), so textual variants like `::1` and
    its expanded form compare equal. The strongest relation across the set wins,
    with EXACT preferred so a single-host query surfaces as an exact hit.
    """
    best: MatchKind | None = None
    for ip in resolved:
        nv = normalize_address(Address(name="", type=AddressType.IP_NETMASK, value=ip))
        if nv is None:
            continue
        mk = match(query, nv)
        if mk is MatchKind.EXACT:
            return MatchKind.EXACT
        if mk is not None and best is None:
            best = mk
    return best


def find_ip(
    snapshot: Snapshot,
    raw: str,
    scope: Location | None = None,
    *,
    exact: bool = False,
    resolve_fqdn: bool = False,
    resolver: Resolver | None = None,
) -> FindResult:
    """Find every address object/group matching `raw` within `scope` (which
    includes the scoped device-group's ancestors and `shared`).

    With `exact=True`, only objects whose value equals the query exactly are
    kept — broader (`CONTAINS`) and narrower (`WITHIN`) matches are dropped.
    Netmask and bare-host forms still canonicalize equal (`10.0.0.10` ==
    `10.0.0.10/32`), so those remain exact.

    With `resolve_fqdn=True` and an IP query, FQDN objects are DNS-resolved via
    `resolver` and match when their resolved A/AAAA set includes the query.
    Resolution is opt-in (offline default never touches DNS); a lookup that
    fails is skipped and tallied in `fqdn_resolution_failures`, never fatal.
    """
    if resolve_fqdn and resolver is None:
        # A silent no-op here would count every FQDN object as a resolution
        # failure and return wrong results; fail loudly instead. The CLI always
        # constructs a resolver when the flag is on.
        raise ValueError("resolver must be provided when resolve_fqdn=True")
    query = parse_query(raw)
    visible = snapshot.visible_location_names(scope)
    # Resolving FQDNs only makes sense for an IP-shaped query; an FQDN query
    # already matches FQDN objects by exact name in the main pass below.
    do_resolve = resolve_fqdn and query.is_ip
    resolve = resolver if resolve_fqdn else None
    failures = 0
    matched: list[tuple[Address, MatchKind]] = []
    for addr in snapshot.addresses:
        if visible is not None and addr.location.name not in visible:
            continue
        nv = normalize_address(addr)
        if nv is None:
            continue
        if do_resolve and addr.type is AddressType.FQDN and nv.fqdn is not None:
            resolved = resolve(nv.fqdn) if resolve is not None else set()
            if not resolved:
                failures += 1
                continue
            mk = _fqdn_match(query, resolved)
            if mk is None or (exact and mk is not MatchKind.EXACT):
                continue
            matched.append((addr, mk))
            continue
        mk = match(query, nv)
        if mk is None or (exact and mk is not MatchKind.EXACT):
            continue
        matched.append((addr, mk))

    matches = [
        AddressMatch(
            name=a.name,
            location=a.location.name,
            type=a.type.value,
            value=a.value,
            match=mk,
            tags=a.tags,
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
            groups.append(
                GroupMatch(name=ag.name, location=ag.location.name, via=via, tags=ag.tags)
            )

    return FindResult(
        query=raw,
        kind=_query_kind(query),
        exists=any(mk is MatchKind.EXACT for _, mk in matched),
        matches=matches,
        groups=sorted(groups, key=lambda g: (g.location, g.name)),
        fqdn_resolution_failures=failures,
    )


def find_ips(
    snapshot: Snapshot,
    raws: list[str],
    scope: Location | None = None,
    *,
    exact: bool = False,
    resolve_fqdn: bool = False,
    resolver: Resolver | None = None,
) -> list[FindResult]:
    return [
        find_ip(snapshot, r, scope, exact=exact, resolve_fqdn=resolve_fqdn, resolver=resolver)
        for r in raws
    ]


class ObjectHit(BaseModel):
    kind: str
    name: str
    location: str
    detail: str
    tags: list[str] = Field(default_factory=list)


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
                    tags=a.tags,
                )
            )
    for ag in snapshot.address_groups:
        if ag.name == name:
            detail = ag.dynamic_filter or f"static[{len(ag.static_members or [])}]"
            hits.append(
                ObjectHit(
                    kind="address-group",
                    name=ag.name,
                    location=ag.location.name,
                    detail=detail,
                    tags=ag.tags,
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
                    tags=s.tags,
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
                    tags=sg.tags,
                )
            )
    for t in snapshot.tags:
        if t.name == name:
            hits.append(
                ObjectHit(kind="tag", name=t.name, location=t.location.name, detail=t.color or "")
            )
    return hits
