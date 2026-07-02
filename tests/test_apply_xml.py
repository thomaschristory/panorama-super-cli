from __future__ import annotations

from psc.core.apply_xml import apply_changeset, partial_config_xml
from psc.core.changeset import (
    ChangeSet,
    ObjectDelete,
    ObjectKind,
    ObjectUpsert,
    RuleDelete,
)
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


def test_merge_apply_roundtrip_repoints_new_rulebase(all_rb_path) -> None:
    xml = all_rb_path.read_text(encoding="utf-8")
    snap = parse_config(xml)
    graph = ReferenceGraph.build(snap)
    cs = plan_merge(
        snap,
        graph,
        keep=ObjectRef(name="a2", location="shared"),
        drop=ObjectRef(name="a2-dup", location="shared"),
    )
    new_snap = parse_config(apply_changeset(xml, cs))
    assert all(a.name != "a2-dup" for a in new_snap.addresses)
    sdwan = next(r for r in new_snap.policy_rules if r.name == "sdwan-1")
    assert sdwan.destination == ["a2"]


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


# --- partial config (#92) -------------------------------------------------


def _upsert_cs(name: str, ip: str, location: str = "shared") -> ChangeSet:
    return ChangeSet(
        title="add",
        upserts=[
            ObjectUpsert(
                kind=ObjectKind.ADDRESS,
                name=name,
                location=location,
                fields={"ip-netmask": ip},
            )
        ],
    )


def test_partial_upsert_contains_only_that_entry(fixture_path) -> None:
    xml = fixture_path.read_text(encoding="utf-8")
    cs = _upsert_cs("new-host", "10.9.9.9/32")
    partial = partial_config_xml(xml, cs)

    snap = parse_config(partial)
    names = [a.name for a in snap.addresses]
    # ONLY the touched object appears — untouched siblings are excluded.
    assert names == ["new-host"]
    added = next(a for a in snap.addresses if a.name == "new-host")
    assert added.value == "10.9.9.9/32"
    # Much smaller than a full rewrite of the whole document.
    assert len(partial) < len(apply_changeset(xml, cs))


def test_partial_upsert_matches_full_apply_value(fixture_path) -> None:
    xml = fixture_path.read_text(encoding="utf-8")
    # Update an existing object; the partial must reflect the new value.
    cs = _upsert_cs("net-10", "10.0.0.0/25")
    partial = partial_config_xml(xml, cs)
    snap = parse_config(partial)
    obj = next(a for a in snap.addresses if a.name == "net-10")
    assert obj.value == "10.0.0.0/25"


def test_partial_reference_edit_shows_referrer_final_state(fixture_path) -> None:
    xml = fixture_path.read_text(encoding="utf-8")
    snap = parse_config(xml)
    graph = ReferenceGraph.build(snap)
    cs = plan_merge(
        snap,
        graph,
        keep=ObjectRef(name="h-web1", location="shared"),
        drop=ObjectRef(name="web-primary", location="shared"),
    )
    partial = partial_config_xml(xml, cs)
    psnap = parse_config(partial)
    # The repointed group appears in its FINAL state (web-primary gone).
    grp = next(g for g in psnap.address_groups if g.name == "grp-web")
    assert grp.static_members == ["h-web1"]
    # The repointed NAT rule appears too.
    nat = next(n for n in psnap.nat_rules if n.name == "nat-web")
    assert nat.source == ["h-web1"]


def test_partial_dg_scoped_change_nests_under_devices(fixture_path) -> None:
    xml = fixture_path.read_text(encoding="utf-8")
    cs = _upsert_cs("edge-new", "192.168.9.9/32", location="DG-EDGE")
    partial = partial_config_xml(xml, cs)
    # Structurally nested so PAN-OS can import it into DG-EDGE.
    assert "<devices>" in partial
    assert "device-group" in partial
    snap = parse_config(partial)
    obj = next(a for a in snap.addresses if a.name == "edge-new")
    assert obj.location.name == "DG-EDGE"
    # shared objects are NOT dragged along.
    assert not any(a.name == "h-web1" for a in snap.addresses)


def test_partial_top_level_delete_is_noted_not_silently_dropped(fixture_path) -> None:
    xml = fixture_path.read_text(encoding="utf-8")
    cs = ChangeSet(
        title="del",
        deletes=[ObjectDelete(kind=ObjectKind.ADDRESS, name="fqdn-example", location="shared")],
    )
    partial = partial_config_xml(xml, cs)
    # Additive partial import cannot express a delete — the object must NOT
    # reappear, and the removal is surfaced as a comment marker.
    snap = parse_config(partial)
    assert not any(a.name == "fqdn-example" for a in snap.addresses)
    assert "fqdn-example" in partial  # named in a comment marker
    assert "delete" in partial.lower()


def test_partial_rule_delete_is_noted_not_silently_dropped(fixture_path) -> None:
    # A rule delete (as decommission emits) can't be expressed by an additive
    # partial import either — it must be surfaced as a comment marker, not dropped.
    xml = fixture_path.read_text(encoding="utf-8")
    cs = ChangeSet(
        title="del rule",
        rule_deletes=[
            RuleDelete(
                referrer_kind="security-rule",
                name="allow-web",
                location="shared",
                rulebase="pre",
            )
        ],
    )
    partial = partial_config_xml(xml, cs)
    assert "allow-web" in partial  # named in a comment marker
    assert "delete" in partial.lower()
    # and it is NOT re-added as a live rule entry
    psnap = parse_config(partial)
    assert not any(r.name == "allow-web" for r in psnap.security_rules)


def test_partial_output_is_deterministic(fixture_path) -> None:
    xml = fixture_path.read_text(encoding="utf-8")
    cs = _upsert_cs("new-host", "10.9.9.9/32")
    assert partial_config_xml(xml, cs) == partial_config_xml(xml, cs)


def test_partial_blocked_plan_raises(fixture_path) -> None:
    xml = fixture_path.read_text(encoding="utf-8")
    cs = ChangeSet(title="bad", blockers=["nope"])
    try:
        partial_config_xml(xml, cs)
    except PscError as exc:
        assert exc.error_type.value == "conflict"
    else:  # pragma: no cover
        raise AssertionError("expected PscError")
