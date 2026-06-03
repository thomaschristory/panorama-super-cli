from __future__ import annotations

from psc.core.apply_xml import apply_changeset
from psc.core.dedup import ObjectRef, plan_merge
from psc.core.parse import parse_config
from psc.core.refs import ReferenceGraph
from psc.output.errors import PscError


def test_merge_apply_roundtrip(fixture_path) -> None:
    xml = fixture_path.read_text(encoding="utf-8")
    snap = parse_config(xml)
    graph = ReferenceGraph.build(snap)
    cs = plan_merge(
        snap,
        graph,
        keep=ObjectRef(name="h-web1", location="shared"),
        drop=ObjectRef(name="web-primary", location="shared"),
    )
    new_xml = apply_changeset(xml, cs)
    new_snap = parse_config(new_xml)

    # web-primary is gone; grp-web no longer references it; nat-web repointed.
    assert all(a.name != "web-primary" for a in new_snap.addresses)
    grp = next(g for g in new_snap.address_groups if g.name == "grp-web")
    assert grp.static_members == ["h-web1"]
    nat = next(n for n in new_snap.nat_rules if n.name == "nat-web")
    assert nat.source == ["h-web1"]


def test_apply_blocked_plan_raises(fixture_path) -> None:
    xml = fixture_path.read_text(encoding="utf-8")
    snap = parse_config(xml)
    graph = ReferenceGraph.build(snap)
    cs = plan_merge(
        snap,
        graph,
        keep=ObjectRef(name="net-10", location="shared"),
        drop=ObjectRef(name="local-only", location="DG-EDGE"),
    )
    assert cs.is_blocked
    try:
        apply_changeset(xml, cs)
    except PscError as exc:
        assert exc.error_type.value == "conflict"
    else:  # pragma: no cover
        raise AssertionError("expected PscError")


def test_apply_dg_local_merge(fixture_path) -> None:
    xml = fixture_path.read_text(encoding="utf-8")
    snap = parse_config(xml)
    graph = ReferenceGraph.build(snap)
    cs = plan_merge(
        snap,
        graph,
        keep=ObjectRef(name="local-only", location="DG-EDGE"),
        drop=ObjectRef(name="edge-dup", location="DG-EDGE"),
    )
    new_snap = parse_config(apply_changeset(xml, cs))
    rule = next(r for r in new_snap.security_rules if r.name == "edge-rule")
    assert "edge-dup" not in rule.destination
    assert "local-only" in rule.destination
