from __future__ import annotations

from psc.core.models import Location, Snapshot
from psc.core.normalize import MatchKind
from psc.core.resolve import find_ip, find_object


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


def test_find_ip_no_match(snapshot: Snapshot) -> None:
    res = find_ip(snapshot, "203.0.113.1")
    assert res.exists is False
    assert res.matches == []


def test_scope_limits_to_dg_plus_shared(snapshot: Snapshot) -> None:
    res = find_ip(snapshot, "192.168.1.1", scope=Location.dg("DG-EDGE"))
    assert {m.name for m in res.matches} == {"local-only", "edge-dup"}


def test_find_object_across_kinds(snapshot: Snapshot) -> None:
    hits = find_object(snapshot, "grp-web")
    assert len(hits) == 1
    assert hits[0].kind == "address-group"
