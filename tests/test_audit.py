from __future__ import annotations

import time

from psc.core.audit import OverlapKind, OverlapPair, find_overlapping_addresses
from psc.core.models import Address, AddressType, Location, Snapshot


def _addr(
    name: str,
    value: str,
    *,
    loc: Location | None = None,
    kind: AddressType | None = None,
) -> Address:
    return Address(
        name=name,
        location=loc or Location.shared(),
        type=kind or AddressType.IP_NETMASK,
        value=value,
    )


def test_host_inside_network_is_contained() -> None:
    snap = Snapshot(addresses=[_addr("net", "10.0.0.0/24"), _addr("host", "10.0.0.10")])
    pairs = find_overlapping_addresses(snap)
    assert len(pairs) == 1
    p = pairs[0]
    assert p.relationship is OverlapKind.CONTAINS
    assert p.left_name == "net"  # broader is left
    assert p.right_name == "host"


def test_nested_cidrs() -> None:
    snap = Snapshot(
        addresses=[
            _addr("a8", "10.0.0.0/8"),
            _addr("a16", "10.0.0.0/16"),
            _addr("a24", "10.0.0.0/24"),
        ]
    )
    pairs = find_overlapping_addresses(snap)
    # /8 ⊇ /16, /8 ⊇ /24, /16 ⊇ /24 — three containment pairs, all CONTAINS.
    assert len(pairs) == 3
    assert all(p.relationship is OverlapKind.CONTAINS for p in pairs)
    rels = {(p.left_name, p.right_name) for p in pairs}
    assert rels == {("a8", "a16"), ("a8", "a24"), ("a16", "a24")}


def test_range_partial_overlap() -> None:
    # Two CIDRs can never partially overlap; ip-ranges can.
    snap = Snapshot(
        addresses=[
            _addr("r1", "10.0.0.1-10.0.0.100", kind=AddressType.IP_RANGE),
            _addr("r2", "10.0.0.50-10.0.0.200", kind=AddressType.IP_RANGE),
        ]
    )
    pairs = find_overlapping_addresses(snap)
    assert len(pairs) == 1
    assert pairs[0].relationship is OverlapKind.OVERLAPS
    # OVERLAPS contract: left = lower-starting interval. Guards an endpoint swap.
    assert pairs[0].left_name == "r1"  # starts at .1 (lower)
    assert pairs[0].right_name == "r2"  # starts at .50 (higher)


def test_range_equals_cidr_is_contains() -> None:
    # Fix 3a: ip-range and ip-netmask spanning the identical interval. Equal
    # intervals collapse to a single CONTAINS with deterministic broader-left.
    snap = Snapshot(
        addresses=[
            _addr("rng", "10.0.0.0-10.0.0.255", kind=AddressType.IP_RANGE),
            _addr("cidr", "10.0.0.0/24"),
        ]
    )
    pairs = find_overlapping_addresses(snap)
    assert len(pairs) == 1
    assert pairs[0].relationship is OverlapKind.CONTAINS
    # Equal interval → deterministic left by (location, name): "cidr" < "rng".
    assert pairs[0].left_name == "cidr"
    assert pairs[0].right_name == "rng"


def test_range_inside_cidr_is_contains() -> None:
    # Fix 3b: an ip-range fully inside a CIDR. The /24 is broader → left.
    snap = Snapshot(
        addresses=[
            _addr("inner", "10.0.0.10-10.0.0.20", kind=AddressType.IP_RANGE),
            _addr("outer", "10.0.0.0/24"),
        ]
    )
    pairs = find_overlapping_addresses(snap)
    assert len(pairs) == 1
    assert pairs[0].relationship is OverlapKind.CONTAINS
    assert pairs[0].left_name == "outer"
    assert pairs[0].right_name == "inner"


def test_range_straddling_cidr_boundary_overlaps() -> None:
    # Fix 3c: an ip-range straddling a CIDR boundary — neither contains the
    # other → OVERLAPS. The range starts lower (.0.200 < .1.0) so it is left.
    snap = Snapshot(
        addresses=[
            _addr("cidr", "10.0.1.0/24"),
            _addr("rng", "10.0.0.200-10.0.1.50", kind=AddressType.IP_RANGE),
        ]
    )
    pairs = find_overlapping_addresses(snap)
    assert len(pairs) == 1
    assert pairs[0].relationship is OverlapKind.OVERLAPS
    assert pairs[0].left_name == "rng"  # starts lower (10.0.0.200)
    assert pairs[0].right_name == "cidr"


def test_cidrs_never_partially_overlap() -> None:
    # Adjacent /25s nest into the /24 but are disjoint from each other.
    snap = Snapshot(
        addresses=[
            _addr("lo", "10.0.0.0/25"),
            _addr("hi", "10.0.0.128/25"),
        ]
    )
    assert find_overlapping_addresses(snap) == []


def test_disjoint_no_pair() -> None:
    snap = Snapshot(addresses=[_addr("a", "10.0.0.0/24"), _addr("b", "192.168.0.0/24")])
    assert find_overlapping_addresses(snap) == []


def test_identical_value_different_location_is_contains() -> None:
    snap = Snapshot(
        addresses=[
            _addr("a", "10.0.0.0/24", loc=Location.dg("DG1")),
            _addr("b", "10.0.0.0/24", loc=Location.shared()),
        ],
        device_groups=["DG1"],
    )
    pairs = find_overlapping_addresses(snap)
    assert len(pairs) == 1
    # Equal intervals collapse to CONTAINS; left is deterministic by (location, name).
    assert pairs[0].relationship is OverlapKind.CONTAINS
    assert (pairs[0].left_location, pairs[0].left_name) < (
        pairs[0].right_location,
        pairs[0].right_name,
    )


def test_ipv6_containment() -> None:
    snap = Snapshot(
        addresses=[
            _addr("v6net", "2001:db8::/32"),
            _addr("v6host", "2001:db8::1"),
        ]
    )
    pairs = find_overlapping_addresses(snap)
    assert len(pairs) == 1
    assert pairs[0].left_name == "v6net"
    assert pairs[0].relationship is OverlapKind.CONTAINS


def test_no_cross_family_pairs() -> None:
    # An IPv4 /0 and an IPv6 ::/0 both cover "everything" in their family but
    # must never pair — different families are incomparable.
    snap = Snapshot(addresses=[_addr("v4", "0.0.0.0/0"), _addr("v6", "::/0")])
    assert find_overlapping_addresses(snap) == []


def test_fqdn_and_wildcard_skipped() -> None:
    snap = Snapshot(
        addresses=[
            _addr("net", "10.0.0.0/24"),
            _addr("fq", "example.com", kind=AddressType.FQDN),
            _addr("wc", "10.0.0.0/0.0.0.255", kind=AddressType.IP_WILDCARD),
        ]
    )
    # Only the netmask object is comparable; nothing to pair it with.
    assert find_overlapping_addresses(snap) == []


def test_scope_filtering() -> None:
    snap = Snapshot(
        addresses=[
            _addr("shared-net", "10.0.0.0/24", loc=Location.shared()),
            _addr("dg-host", "10.0.0.10", loc=Location.dg("DG1")),
            _addr("other-host", "10.0.0.20", loc=Location.dg("DG2")),
        ],
        device_groups=["DG1", "DG2"],
    )
    # Scoped to DG1: only shared + DG1 visible, so other-host (DG2) drops out.
    pairs = find_overlapping_addresses(snap, scope=Location.dg("DG1"))
    names = {(p.left_name, p.right_name) for p in pairs}
    assert names == {("shared-net", "dg-host")}


def test_empty_snapshot() -> None:
    assert find_overlapping_addresses(Snapshot()) == []


def test_single_object() -> None:
    assert find_overlapping_addresses(Snapshot(addresses=[_addr("a", "10.0.0.0/24")])) == []


def test_deterministic_ordering() -> None:
    snap = Snapshot(
        addresses=[
            _addr("z-net", "10.0.0.0/24"),
            _addr("a-host", "10.0.0.10"),
            _addr("m-host", "10.0.0.20"),
        ]
    )
    pairs = find_overlapping_addresses(snap)
    keys = [(p.left_location, p.left_name, p.right_location, p.right_name) for p in pairs]
    assert keys == sorted(keys)


def test_large_n_hosts_in_one_network() -> None:
    # 1000 /32 hosts inside one /24-equivalent broad net: ~1000 pairs, fast.
    hosts = [_addr(f"h{i}", f"10.0.{i // 256}.{i % 256}") for i in range(1000)]
    broad = _addr("big", "10.0.0.0/8")
    snap = Snapshot(addresses=[broad, *hosts])
    start = time.perf_counter()
    pairs = find_overlapping_addresses(snap)
    elapsed = time.perf_counter() - start
    assert len(pairs) == 1000  # big contains each host exactly once
    assert all(p.left_name == "big" and p.relationship is OverlapKind.CONTAINS for p in pairs)
    assert elapsed < 1.0


def test_wide_ipv6_does_not_keep_ipv4_active() -> None:
    # Fix 5: many wide IPv6 nets interleaved with many disjoint IPv4 hosts. The
    # per-family partition means the wide IPv6 intervals never hold unrelated
    # IPv4 hosts "active" — zero cross-family pairs, and it stays fast.
    v6 = [_addr(f"v6_{i}", f"2001:db8:{i:x}::/48") for i in range(500)]
    v4 = [_addr(f"v4_{i}", f"10.{i // 256}.{i % 256}.1") for i in range(500)]
    # Interleave so a naive single sweep would keep IPv6 intervals active across
    # all the IPv4 hosts (and vice versa).
    addrs: list[Address] = []
    for a, b in zip(v6, v4, strict=True):
        addrs.append(a)
        addrs.append(b)
    snap = Snapshot(addresses=addrs)
    start = time.perf_counter()
    pairs = find_overlapping_addresses(snap)
    elapsed = time.perf_counter() - start
    # Distinct IPv4 hosts and distinct IPv6 /48s are pairwise disjoint within
    # their family, and never pair across families → nothing emitted.
    assert pairs == []
    assert elapsed < 1.0


def test_overlap_pair_model_roundtrips() -> None:
    p = OverlapPair(
        left_name="net",
        left_location="shared",
        left_value="10.0.0.0/24",
        right_name="host",
        right_location="shared",
        right_value="10.0.0.10",
        relationship=OverlapKind.CONTAINS,
    )
    assert p.model_dump(mode="json")["relationship"] == "contains"
