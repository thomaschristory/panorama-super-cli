from __future__ import annotations

import pytest

from psc.core.models import Location, Snapshot
from psc.core.normalize import MatchKind
from psc.core.resolve import find_ip, find_object
from psc.core.resolver import CachingResolver


class _FakeResolver:
    """Dict-backed resolver that records calls, for deterministic tests.

    Maps FQDN -> set of IP strings. An unmapped name returns the empty set,
    the resolver's contract for "could not resolve" (NXDOMAIN/timeout), which
    the engine treats as a non-fatal skip.
    """

    def __init__(self, table: dict[str, set[str]]) -> None:
        self._table = table
        self.calls: list[str] = []

    def __call__(self, fqdn: str) -> set[str]:
        self.calls.append(fqdn)
        return self._table.get(fqdn, set())


def test_find_ip_exact_and_contains(snapshot: Snapshot) -> None:
    res = find_ip(snapshot, "10.0.0.10")
    assert res.exists is True
    names = {m.name for m in res.matches}
    assert {"h-web1", "web-primary", "h-web1-slash", "net-10"} <= names
    kinds = {m.name: m.match for m in res.matches}
    assert kinds["net-10"] is MatchKind.CONTAINS
    assert kinds["h-web1"] is MatchKind.EXACT


def test_find_ip_reports_groups(snapshot: Snapshot) -> None:
    res = find_ip(snapshot, "10.0.0.10")
    grp = {g.name: g for g in res.groups}
    assert "grp-web" in grp
    assert set(grp["grp-web"].via) == {"h-web1", "web-primary"}


def test_find_ip_exact_flag_drops_contains_and_within(snapshot: Snapshot) -> None:
    # Without --exact, the host matches its own objects (EXACT) and the
    # enclosing /24 (CONTAINS). With exact=True only the EXACT ones survive.
    res = find_ip(snapshot, "10.0.0.10", exact=True)
    assert res.exists is True
    kinds = {m.match for m in res.matches}
    assert kinds == {MatchKind.EXACT}
    names = {m.name for m in res.matches}
    # /32 and bare-host forms normalize equal, so all three are EXACT...
    assert {"h-web1", "web-primary", "h-web1-slash"} <= names
    # ...and the broader /24 (CONTAINS) is excluded.
    assert "net-10" not in names


def test_find_ip_exact_flag_slash32_equals_bare(snapshot: Snapshot) -> None:
    # 10.0.0.10/32 and 10.0.0.10 are the same host; both query forms must hit
    # both object forms under --exact.
    for q in ("10.0.0.10", "10.0.0.10/32"):
        names = {m.name for m in find_ip(snapshot, q, exact=True).matches}
        assert {"h-web1", "h-web1-slash"} <= names, q


def test_find_ip_exact_flag_excludes_groups_with_only_broader(snapshot: Snapshot) -> None:
    # A CIDR query that exactly matches no object yields nothing under --exact.
    res = find_ip(snapshot, "10.0.0.0/8", exact=True)
    assert res.exists is False
    assert res.matches == []
    assert res.groups == []


def test_find_ip_no_match(snapshot: Snapshot) -> None:
    res = find_ip(snapshot, "203.0.113.1")
    assert res.exists is False
    assert res.matches == []


def test_scope_limits_to_dg_plus_shared(snapshot: Snapshot) -> None:
    res = find_ip(snapshot, "192.168.1.1", scope=Location.dg("DG-EDGE"))
    assert {m.name for m in res.matches} == {"local-only", "edge-dup"}


def test_find_ip_offline_default_does_not_resolve(snapshot: Snapshot) -> None:
    # example.com FQDN object exists in the fixture; without --resolve-fqdn it
    # must never be DNS-resolved, so an IP query cannot surface it and the
    # resolver is never consulted.
    resolver = _FakeResolver({"example.com": {"93.184.216.34"}})
    res = find_ip(snapshot, "93.184.216.34", resolver=resolver)
    assert "fqdn-example" not in {m.name for m in res.matches}
    assert resolver.calls == []


def test_find_ip_resolve_fqdn_surfaces_matching_object(snapshot: Snapshot) -> None:
    resolver = _FakeResolver({"example.com": {"93.184.216.34"}})
    res = find_ip(snapshot, "93.184.216.34", resolve_fqdn=True, resolver=resolver)
    match = next(m for m in res.matches if m.name == "fqdn-example")
    assert match.match is MatchKind.EXACT
    assert match.type == "fqdn"


def test_find_ip_resolve_fqdn_ignores_nonmatching_object(snapshot: Snapshot) -> None:
    resolver = _FakeResolver({"example.com": {"198.51.100.7"}})
    res = find_ip(snapshot, "93.184.216.34", resolve_fqdn=True, resolver=resolver)
    assert "fqdn-example" not in {m.name for m in res.matches}


def test_find_ip_resolve_fqdn_canonicalizes_ipv6(snapshot: Snapshot) -> None:
    # ::1 and its fully-expanded form must match — the resolver may return
    # either textual form.
    resolver = _FakeResolver({"example.com": {"0:0:0:0:0:0:0:1"}})
    res = find_ip(snapshot, "::1", resolve_fqdn=True, resolver=resolver)
    assert "fqdn-example" in {m.name for m in res.matches}


def test_find_ip_resolve_fqdn_failure_is_non_fatal(snapshot: Snapshot) -> None:
    # An unmapped name raises inside the fake resolver; find must skip the
    # object, still return the numeric IP matches, and count the failure.
    resolver = _FakeResolver({})  # every lookup fails
    res = find_ip(snapshot, "10.0.0.10", resolve_fqdn=True, resolver=resolver)
    assert "h-web1" in {m.name for m in res.matches}
    assert res.fqdn_resolution_failures == 1


def test_find_ip_resolve_fqdn_caches_per_unique_name(snapshot: Snapshot) -> None:
    inner = _FakeResolver({"example.com": {"93.184.216.34"}})
    resolver = CachingResolver(lookup=inner)
    # Two targets over one snapshot share the resolver: the same FQDN must hit
    # the underlying lookup exactly once thanks to the cache.
    find_ip(snapshot, "93.184.216.34", resolve_fqdn=True, resolver=resolver)
    find_ip(snapshot, "1.1.1.1", resolve_fqdn=True, resolver=resolver)
    assert inner.calls == ["example.com"]


def test_caching_resolver_caches_failures() -> None:
    inner = _FakeResolver({})  # always fails
    resolver = CachingResolver(lookup=inner)
    assert resolver("nope.example") == set()
    assert resolver("nope.example") == set()
    # Negative results are cached too, so a dead name isn't re-queried per row.
    assert inner.calls == ["nope.example"]


def test_find_ip_resolve_fqdn_requires_resolver(snapshot: Snapshot) -> None:
    # Guard: enabling resolution without a resolver would silently miscount
    # every FQDN as a failure, so it must raise rather than return wrong data.
    with pytest.raises(ValueError, match="resolver must be provided"):
        find_ip(snapshot, "10.0.0.10", resolve_fqdn=True)


def test_find_object_across_kinds(snapshot: Snapshot) -> None:
    hits = find_object(snapshot, "grp-web")
    assert len(hits) == 1
    assert hits[0].kind == "address-group"


def test_find_ip_match_carries_tags(snapshot: Snapshot) -> None:
    # h-web1 carries tag t-prod in the fixture; the match must surface it.
    res = find_ip(snapshot, "10.0.0.10")
    match = next(m for m in res.matches if m.name == "h-web1")
    assert match.tags == ["t-prod"]


def test_find_object_carries_tags(snapshot: Snapshot) -> None:
    hits = find_object(snapshot, "grp-web")
    assert hits[0].tags == ["t-prod"]


def test_find_object_tag_kind_has_empty_tags(snapshot: Snapshot) -> None:
    # A tag definition has no tags of its own — empty list, never populated.
    hits = find_object(snapshot, "t-prod")
    assert [h.kind for h in hits] == ["tag"]
    assert hits[0].tags == []
