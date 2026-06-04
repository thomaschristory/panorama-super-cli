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


class OverlapKind(str, Enum):
    """How the *left* object relates to the *right* in an emitted pair."""

    CONTAINS = "contains"
    """Left's interval fully spans right's (left is broader, or equal)."""
    CONTAINED_BY = "contained_by"
    """Reserved for symmetry; not emitted — pairs normalize to broader-left."""
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


def _visible_names(snapshot: Snapshot, scope: Location | None) -> set[str] | None:
    """Location names visible from `scope` (the DG, its ancestors, and shared).
    `None` means unscoped. Mirrors `resolve._visible_names` so audit honours
    `--device-group` exactly like `find`."""
    if scope is None:
        return None
    return {loc.name for loc in snapshot.ancestors(scope)}


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

    Sort-then-sweep, not O(n²) all-pairs: sorting by `(lo asc, hi desc)` means
    that when interval `cur` starts, every interval still "active" (its `hi`
    not yet passed) either contains `cur` or partially overlaps it — never the
    reverse — so each comparison is decided in O(1) and only genuinely related
    objects are touched. Worst case (n hosts in one broad net) is ~n pairs.
    """
    visible = _visible_names(snapshot, scope)
    intervals: list[_Interval] = []
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
        intervals.append(_Interval(addr, nv, lo, hi))

    # Earlier-starting first; among equal starts the wider (larger hi) first so
    # a container is already active when its contained peers begin. order_key
    # breaks remaining ties for a stable, deterministic `left` on equal spans.
    intervals.sort(key=lambda iv: (iv.lo, -iv.hi, iv.order_key))

    pairs: list[OverlapPair] = []
    active: list[_Interval] = []
    for cur in intervals:
        # Drop intervals that ended before `cur` starts: they can't relate to
        # `cur` or anything after it (everything after starts >= cur.lo).
        active = [iv for iv in active if iv.hi >= cur.lo]
        for prev in active:
            if prev.family != cur.family:
                continue  # never pair across address families
            # `prev` starts at or before `cur` (sort order). If it also ends at
            # or after `cur`, it spans `cur` → containment, broader is `prev`.
            if prev.hi >= cur.hi:
                pairs.append(_pair(prev, cur, OverlapKind.CONTAINS))
            else:
                # prev.lo <= cur.lo <= prev.hi < cur.hi → genuine partial overlap.
                pairs.append(_pair(prev, cur, OverlapKind.OVERLAPS))
        active.append(cur)

    pairs.sort(key=lambda p: (p.left_location, p.left_name, p.right_location, p.right_name))
    return pairs
