"""Audit address objects for overlapping or contained IP ranges.

Answers *"which address objects step on each other?"* — a /32 that sits inside
a /24 that is also an object, two ip-ranges that partially overlap, or the same
network defined twice under different names/locations. This is the read-only
companion to `dedup`: dedup groups byte-identical values, audit surfaces the
looser containment/overlap relationships that dedup deliberately ignores.

Only objects with a comparable integer interval participate — ip-netmask and
ip-range. FQDN and ip-wildcard have no interval (`value_bounds` returns `None`)
and are skipped. Families never mix: an IPv4 and an IPv6 object are
incomparable even when both span "everything".
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from psc.core.models import Address, Location, Snapshot
from psc.core.normalize import AddrValue, normalize_address, value_bounds


class WellKnownKind(str, Enum):
    """Whether a matched (protocol, port) names a real PAN-OS object or a port
    number the community treats as reserved."""

    PREDEFINED = "predefined"
    """A predefined PAN-OS *service object* (e.g. `service-http`) — a custom
    service can be deleted and every reference repointed onto this name."""
    WELL_KNOWN = "well-known"
    """An IANA well-known port with a conventional name (e.g. `ssh`), but *not*
    a shipped PAN-OS object — the flag is advisory (name/document consistently),
    there is no predefined object to consolidate onto."""


# Curated (protocol, port) → (canonical name, kind) table.
#
# Only two entries are true predefined PAN-OS *service objects* that ship in
# every config — service-http (tcp/80) and service-https (tcp/443) — so a custom
# tcp/80 object is genuinely redundant and can be consolidated onto the
# predefined name. The rest are IANA well-known ports: there is no shipped object
# to fold onto, but a bespoke "ssh"-on-tcp/22 object is still worth surfacing so
# teams name/document it consistently. Kept deliberately small and conventional
# (one canonical name per port) rather than exhaustive, to avoid noisy or
# arguable matches. udp/67 (dhcp server) is the single canonical name for the
# 67/68 pair; we match the server port a service object would realistically use.
_WELLKNOWN: dict[tuple[str, int], tuple[str, WellKnownKind]] = {
    # Predefined PAN-OS service objects.
    ("tcp", 80): ("service-http", WellKnownKind.PREDEFINED),
    ("tcp", 443): ("service-https", WellKnownKind.PREDEFINED),
    # IANA well-known ports (conventional names, no predefined object).
    ("tcp", 21): ("ftp", WellKnownKind.WELL_KNOWN),
    ("tcp", 22): ("ssh", WellKnownKind.WELL_KNOWN),
    ("tcp", 23): ("telnet", WellKnownKind.WELL_KNOWN),
    ("tcp", 25): ("smtp", WellKnownKind.WELL_KNOWN),
    ("tcp", 53): ("dns", WellKnownKind.WELL_KNOWN),
    ("udp", 53): ("dns", WellKnownKind.WELL_KNOWN),
    ("udp", 67): ("dhcp", WellKnownKind.WELL_KNOWN),
    ("tcp", 110): ("pop3", WellKnownKind.WELL_KNOWN),
    ("udp", 123): ("ntp", WellKnownKind.WELL_KNOWN),
    ("tcp", 143): ("imap", WellKnownKind.WELL_KNOWN),
    ("tcp", 389): ("ldap", WellKnownKind.WELL_KNOWN),
    ("tcp", 636): ("ldaps", WellKnownKind.WELL_KNOWN),
    ("tcp", 993): ("imaps", WellKnownKind.WELL_KNOWN),
    ("tcp", 995): ("pop3s", WellKnownKind.WELL_KNOWN),
    ("tcp", 3389): ("rdp", WellKnownKind.WELL_KNOWN),
    ("tcp", 8080): ("http-alt", WellKnownKind.WELL_KNOWN),
}


class WellKnownMatch(BaseModel):
    """One custom service whose single destination port duplicates a predefined
    PAN-OS service or an IANA well-known port."""

    service_name: str
    service_location: str
    protocol: str
    port: str
    canonical_name: str
    """The predefined/well-known name this service duplicates."""
    kind: WellKnownKind


class OverlapKind(str, Enum):
    """How the *left* object relates to the *right* in an emitted pair."""

    CONTAINS = "contains"
    """Left's interval fully spans right's (left is broader, or equal)."""
    OVERLAPS = "overlaps"
    """Intervals intersect but neither contains the other (ip-range only)."""


class OverlapPair(BaseModel):
    """One unordered relationship between two address objects, emitted once.

    For containment the broader object is always `left`; for a partial overlap
    `left` is the lower-starting interval. Never both directions of a pair.
    """

    left_name: str
    left_location: str
    left_value: str
    right_name: str
    right_location: str
    right_value: str
    relationship: OverlapKind


class _Interval:
    """An address reduced to its integer span, kept with enough identity to
    emit a pair. Tie-broken for deterministic `left` selection."""

    __slots__ = ("addr", "family", "hi", "lo")

    def __init__(self, addr: Address, value: AddrValue, lo: int, hi: int) -> None:
        self.addr = addr
        self.family = value.family
        self.lo = lo
        self.hi = hi

    @property
    def order_key(self) -> tuple[str, str]:
        return (self.addr.location.name, self.addr.name)


def _pair(broad: _Interval, narrow: _Interval, kind: OverlapKind) -> OverlapPair:
    return OverlapPair(
        left_name=broad.addr.name,
        left_location=broad.addr.location.name,
        left_value=broad.addr.value,
        right_name=narrow.addr.name,
        right_location=narrow.addr.location.name,
        right_value=narrow.addr.value,
        relationship=kind,
    )


def find_overlapping_addresses(
    snapshot: Snapshot, scope: Location | None = None
) -> list[OverlapPair]:
    """Every pair of address objects whose IP intervals overlap or contain.

    Pairs are unordered and emitted at most once. Containment (one interval
    inside the other, or equal) yields `CONTAINS` with the broader object as
    `left`; a partial intersection yields `OVERLAPS`. Disjoint intervals and
    cross-family pairs yield nothing.

    Sort-then-sweep, not O(n²) all-pairs: intervals are partitioned by address
    family first, then each family group is sorted by `(lo asc, hi desc)` and
    swept independently. Within a group, when interval `cur` starts every
    interval still "active" (its `hi` not yet passed) either contains `cur` or
    partially overlaps it — never the reverse — so each comparison is decided in
    O(1) and only genuinely related objects are touched. Partitioning keeps the
    active list per-family, so a wide IPv6 object never holds unrelated IPv4
    intervals active. Worst case (n hosts in one broad net) is ~n pairs.
    """
    visible = snapshot.visible_location_names(scope)
    by_family: dict[int | None, list[_Interval]] = {}
    for addr in snapshot.addresses:
        if visible is not None and addr.location.name not in visible:
            continue
        nv = normalize_address(addr)
        if nv is None:
            continue
        bounds = value_bounds(nv)
        if bounds is None:  # FQDN / ip-wildcard — no comparable interval
            continue
        lo, hi = bounds
        iv = _Interval(addr, nv, lo, hi)
        by_family.setdefault(iv.family, []).append(iv)

    pairs: list[OverlapPair] = []
    for intervals in by_family.values():
        # Earlier-starting first; among equal starts the wider (larger hi) first
        # so a container is already active when its contained peers begin.
        # order_key breaks remaining ties for a stable, deterministic `left` on
        # equal spans.
        intervals.sort(key=lambda iv: (iv.lo, -iv.hi, iv.order_key))
        active: list[_Interval] = []
        for cur in intervals:
            # Drop intervals that ended before `cur` starts: they can't relate to
            # `cur` or anything after it (everything after starts >= cur.lo).
            active = [iv for iv in active if iv.hi >= cur.lo]
            for prev in active:
                # `prev` starts at or before `cur` (sort order). If it also ends
                # at or after `cur`, it spans `cur` → containment, broader is
                # `prev`.
                if prev.hi >= cur.hi:
                    pairs.append(_pair(prev, cur, OverlapKind.CONTAINS))
                else:
                    # prev.lo <= cur.lo <= prev.hi < cur.hi → partial overlap.
                    pairs.append(_pair(prev, cur, OverlapKind.OVERLAPS))
            active.append(cur)

    pairs.sort(key=lambda p: (p.left_location, p.left_name, p.right_location, p.right_name))
    return pairs


def _single_port(port: str | None) -> int | None:
    """The one integer this destination-port spec denotes, or `None`.

    Conservative on purpose: a range (`"79-81"`), a list (`"80,443"`), or an
    empty/absent value denotes no single port and must never be folded onto a
    well-known name. Only a bare integer qualifies."""
    if port is None:
        return None
    text = port.strip()
    if not text.isdigit():  # ranges ("-"), lists (","), empty → not a single port
        return None
    return int(text)


def find_wellknown_duplicate_services(
    snapshot: Snapshot, scope: Location | None = None
) -> list[WellKnownMatch]:
    """Custom services whose single destination port duplicates a predefined
    PAN-OS service or an IANA well-known port.

    A service matches only when its destination port is a *single* integer (not
    a range or list) equalling a `_WELLKNOWN` entry for the *same* protocol —
    deliberately conservative to avoid false-positives on multi-port objects.
    Source ports are ignored. Honours `--device-group` scope and sorts
    deterministically by `(location, name)`.
    """
    visible = snapshot.visible_location_names(scope)
    matches: list[WellKnownMatch] = []
    for svc in snapshot.services:
        if visible is not None and svc.location.name not in visible:
            continue
        port = _single_port(svc.destination_port)
        if port is None:
            continue
        entry = _WELLKNOWN.get((svc.protocol, port))
        if entry is None:
            continue
        canonical, kind = entry
        matches.append(
            WellKnownMatch(
                service_name=svc.name,
                service_location=svc.location.name,
                protocol=svc.protocol,
                port=str(port),
                canonical_name=canonical,
                kind=kind,
            )
        )
    matches.sort(key=lambda m: (m.service_location, m.service_name))
    return matches
