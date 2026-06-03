"""Canonicalize and compare address/service values.

This is the numeric heart of `find` (does an IP match this object?) and
`dedup` (do these two objects mean the same thing?). Both reduce an object's
human-written value to a canonical form so that `10.0.0.10` and `10.0.0.10/32`
are recognised as identical, and so containment (`10.0.0.10` ∈ `10.0.0.0/24`)
is a set operation rather than string-matching.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import Enum

from psc.core.models import Address, AddressType, Service

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class MatchKind(str, Enum):
    """How a query relates to an object's address value."""

    EXACT = "exact"
    """Object value equals the query exactly (same canonical form)."""
    CONTAINS = "contains"
    """Object is broader and contains the query (e.g. /24 contains a host)."""
    WITHIN = "within"
    """Object is narrower and falls inside the query (host inside a queried /24)."""


@dataclass(frozen=True)
class AddrValue:
    """A normalized address value with both a dedup key and match capability."""

    kind: AddressType
    key: str
    """Canonical string; equal keys (same kind) => duplicate objects."""
    network: IPNetwork | None = None
    range: tuple[int, int] | None = None
    family: int | None = None
    fqdn: str | None = None

    def overlaps_key(self) -> str:
        """Kind-qualified key for grouping exact duplicates."""
        return f"{self.kind.value}:{self.key}"


def _as_network(value: str) -> IPNetwork | None:
    try:
        return ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        return None


def _range_bounds(value: str) -> tuple[int, int, int] | None:
    """`a-b` -> (start_int, end_int, family); None if unparseable."""
    if "-" not in value:
        return None
    lo_s, _, hi_s = value.partition("-")
    try:
        lo = ipaddress.ip_address(lo_s.strip())
        hi = ipaddress.ip_address(hi_s.strip())
    except ValueError:
        return None
    if lo.version != hi.version:
        return None
    return (int(lo), int(hi), lo.version)


def normalize_address(addr: Address) -> AddrValue | None:
    """Reduce an address object to a comparable `AddrValue`, or `None` if its
    value can't be parsed (kept out of numeric matching, still listable).
    """
    v = addr.value.strip()
    if addr.type is AddressType.IP_NETMASK:
        net = _as_network(v)
        if net is None:
            return None
        return AddrValue(kind=addr.type, key=str(net), network=net, family=net.version)
    if addr.type is AddressType.IP_RANGE:
        bounds = _range_bounds(v)
        if bounds is None:
            return None
        lo, hi, fam = bounds
        return AddrValue(kind=addr.type, key=f"{lo}-{hi}", range=(lo, hi), family=fam)
    if addr.type is AddressType.IP_WILDCARD:
        return AddrValue(kind=addr.type, key=" ".join(v.split()))
    # FQDN
    fqdn = v.rstrip(".").lower()
    return AddrValue(kind=addr.type, key=fqdn, fqdn=fqdn)


@dataclass(frozen=True)
class Query:
    """A parsed `find` target: a host, a CIDR, a range, or an FQDN."""

    raw: str
    network: IPNetwork | None = None
    range: tuple[int, int] | None = None
    family: int | None = None
    fqdn: str | None = None

    @property
    def is_ip(self) -> bool:
        return self.network is not None or self.range is not None


def parse_query(raw: str) -> Query:
    """Parse a user-supplied target into a `Query`. Falls back to FQDN."""
    s = raw.strip()
    bounds = _range_bounds(s)
    if bounds is not None:
        lo, hi, fam = bounds
        return Query(raw=raw, range=(lo, hi), family=fam)
    net = _as_network(s)
    if net is not None:
        return Query(raw=raw, network=net, family=net.version)
    return Query(raw=raw, fqdn=s.rstrip(".").lower())


def _query_bounds(q: Query) -> tuple[int, int] | None:
    if q.network is not None:
        return (int(q.network.network_address), int(q.network.broadcast_address))
    return q.range


def _value_bounds(a: AddrValue) -> tuple[int, int] | None:
    if a.network is not None:
        return (int(a.network.network_address), int(a.network.broadcast_address))
    return a.range


def match(query: Query, value: AddrValue) -> MatchKind | None:  # noqa: PLR0911 — interval cases
    """Return how `value` relates to `query`, or `None` for no match.

    FQDN objects match only an identical FQDN query in v0.1 (no DNS); IP
    objects and IP queries are compared as integer intervals so netmask,
    range, and host forms interoperate. Address families must agree.
    """
    if query.fqdn is not None:
        if value.fqdn is not None and value.fqdn == query.fqdn:
            return MatchKind.EXACT
        return None
    if value.fqdn is not None or value.kind is AddressType.IP_WILDCARD:
        return None  # can't numerically compare an FQDN/wildcard to an IP query

    qb = _query_bounds(query)
    vb = _value_bounds(value)
    if qb is None or vb is None:
        return None
    if query.family is not None and value.family is not None and query.family != value.family:
        return None

    q_lo, q_hi = qb
    v_lo, v_hi = vb
    if q_lo == v_lo and q_hi == v_hi:
        return MatchKind.EXACT
    if v_lo <= q_lo and q_hi <= v_hi:
        return MatchKind.CONTAINS  # object spans the query
    if q_lo <= v_lo and v_hi <= q_hi:
        return MatchKind.WITHIN  # object sits inside the query
    return None


def service_key(svc: Service) -> str:
    """Canonical key for service dedup: protocol + dest + source ports.

    Port lists are order-normalized (`443,80` == `80,443`) but ranges are left
    as written — `1024-65535` and an explicit enumeration are not unified.
    """

    def norm_ports(p: str | None) -> str:
        if not p:
            return ""
        parts = [seg.strip() for seg in p.split(",") if seg.strip()]
        return ",".join(sorted(parts))

    return (
        f"{svc.protocol.lower()}/"
        f"dst={norm_ports(svc.destination_port)}/"
        f"src={norm_ports(svc.source_port)}"
    )
