from __future__ import annotations

from psc.core.models import SHARED, Address, AddressType, Service
from psc.core.normalize import MatchKind, match, normalize_address, parse_query, service_key


def _addr(value: str, kind: AddressType = AddressType.IP_NETMASK) -> Address:
    return Address(name="x", location=SHARED, type=kind, value=value)


def test_host_and_slash32_normalize_equal() -> None:
    a = normalize_address(_addr("10.0.0.10"))
    b = normalize_address(_addr("10.0.0.10/32"))
    assert a is not None and b is not None
    assert a.overlaps_key() == b.overlaps_key()


def test_exact_key_preserves_host_bits() -> None:
    # Same masked network, different host bits: the loose key collapses them,
    # the strict (exact) key keeps them apart.
    host = normalize_address(_addr("10.1.1.50/24"))
    net = normalize_address(_addr("10.1.1.0/24"))
    assert host is not None and net is not None
    assert host.overlaps_key() == net.overlaps_key()
    assert host.exact_key() != net.exact_key()


def test_exact_key_unifies_host_and_slash32() -> None:
    # A bare host and its /32 are genuinely identical under the strict key too.
    a = normalize_address(_addr("10.0.0.10"))
    b = normalize_address(_addr("10.0.0.10/32"))
    assert a is not None and b is not None
    assert a.exact_key() == b.exact_key()


def test_exact_key_canonicalizes_ipv6_forms() -> None:
    # Strict must still group genuinely identical objects written differently:
    # `0:0:0:0:0:0:0:1` and `::1` are the same host.
    a = normalize_address(_addr("0:0:0:0:0:0:0:1"))
    b = normalize_address(_addr("::1"))
    assert a is not None and b is not None
    assert a.exact_key() == b.exact_key()


def test_exact_contains_within() -> None:
    host = normalize_address(_addr("10.0.0.10/32"))
    net = normalize_address(_addr("10.0.0.0/24"))
    assert host is not None and net is not None
    assert match(parse_query("10.0.0.10"), host) is MatchKind.EXACT
    assert match(parse_query("10.0.0.10"), net) is MatchKind.CONTAINS
    assert match(parse_query("10.0.0.0/24"), host) is MatchKind.WITHIN


def test_range_membership() -> None:
    rng = normalize_address(_addr("10.0.0.50-10.0.0.60", AddressType.IP_RANGE))
    assert rng is not None
    assert match(parse_query("10.0.0.55"), rng) is MatchKind.CONTAINS
    assert match(parse_query("10.0.0.99"), rng) is None


def test_family_mismatch_no_match() -> None:
    v4 = normalize_address(_addr("10.0.0.0/24"))
    assert v4 is not None
    assert match(parse_query("2001:db8::1"), v4) is None


def test_fqdn_only_matches_same_fqdn() -> None:
    fq = normalize_address(_addr("Example.com.", AddressType.FQDN))
    assert fq is not None
    assert match(parse_query("example.com"), fq) is MatchKind.EXACT
    assert match(parse_query("10.0.0.1"), fq) is None


def test_service_key_port_order_independent() -> None:
    a = Service(name="a", protocol="tcp", destination_port="443,80")
    b = Service(name="b", protocol="tcp", destination_port="80,443")
    assert service_key(a) == service_key(b)
