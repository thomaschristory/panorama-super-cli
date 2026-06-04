from __future__ import annotations

from pathlib import Path

import pytest

from psc.core.apply_live import plan_xapi_ops
from psc.core.apply_xml import apply_changeset
from psc.core.changeset import ChangeSet, ReferenceEdit, reference_edit_is_mappable
from psc.core.models import Location, PolicyRule, Rulebase, RuleType, Snapshot
from psc.core.parse import parse_config_file
from psc.core.rule_edit import plan_rule_member_edit
from psc.core.setcmd import render_changeset
from psc.output.errors import ErrorType, PscError

SHARED = Location.shared()


def plan(
    snap: Snapshot,
    rule: str,
    field: str,
    *,
    location: Location | None = SHARED,
    rulebase: Rulebase = Rulebase.PRE,
    add: str | None = None,
    remove: str | None = None,
) -> ChangeSet:
    return plan_rule_member_edit(snap, rule, location, rulebase, field, add=add, remove=remove)


def _edit(cs: ChangeSet) -> ReferenceEdit:
    assert len(cs.reference_edits) == 1, cs.reference_edits
    return cs.reference_edits[0]


# --- add ----------------------------------------------------------------


def test_add_member_builds_reference_edit(snapshot: Snapshot) -> None:
    e = _edit(plan(snapshot, "allow-web", "destination", add="net-10b"))
    assert e.referrer_kind == "security-rule"
    assert e.referrer_name == "allow-web"
    assert e.field == "destination"
    assert e.rulebase == "pre"
    assert e.before == ["h-web1", "net-10"]
    assert e.after == ["h-web1", "net-10", "net-10b"]


def test_add_already_present_is_noop(snapshot: Snapshot) -> None:
    cs = plan(snapshot, "allow-web", "destination", add="net-10")
    assert cs.is_empty
    assert not cs.reference_edits


# --- remove -------------------------------------------------------------


def test_remove_member_builds_reference_edit(snapshot: Snapshot) -> None:
    e = _edit(plan(snapshot, "allow-web", "destination", remove="net-10"))
    assert e.before == ["h-web1", "net-10"]
    assert e.after == ["h-web1"]


def test_remove_absent_is_noop(snapshot: Snapshot) -> None:
    assert plan(snapshot, "allow-web", "destination", remove="nope").is_empty


def test_remove_last_member_yields_empty_after_not_blocked(snapshot: Snapshot) -> None:
    cs = plan(snapshot, "allow-web", "service", remove="tcp-443")
    e = _edit(cs)
    assert e.before == ["tcp-443"]
    assert e.after == []
    assert not cs.is_blocked


# --- NAT guards ---------------------------------------------------------


def test_nat_scalar_service_is_blocked(snapshot: Snapshot) -> None:
    cs = plan(snapshot, "nat-web", "service", add="udp-53")
    assert cs.is_blocked
    assert not cs.reference_edits
    assert any("scalar" in b for b in cs.blockers)


def test_nat_application_field_raises_validation(snapshot: Snapshot) -> None:
    with pytest.raises(PscError) as ei:
        plan(snapshot, "nat-web", "application", add="web")
    assert ei.value.error_type is ErrorType.VALIDATION


def test_nat_source_member_edit_ok(snapshot: Snapshot) -> None:
    e = _edit(plan(snapshot, "nat-web", "source", add="net-10"))
    assert e.referrer_kind == "nat-rule"
    assert e.before == ["web-primary"]
    assert e.after == ["web-primary", "net-10"]


# --- not found / validation --------------------------------------------


def test_unknown_rule_not_found(snapshot: Snapshot) -> None:
    with pytest.raises(PscError) as ei:
        plan(snapshot, "ghost", "source", add="x")
    assert ei.value.error_type is ErrorType.NOT_FOUND


def test_unknown_field_validation(snapshot: Snapshot) -> None:
    with pytest.raises(PscError) as ei:
        plan(snapshot, "allow-web", "protocol", add="x")
    assert ei.value.error_type is ErrorType.VALIDATION


# --- application on security rule --------------------------------------


def test_application_on_security_rule_is_mappable_edit(snapshot: Snapshot) -> None:
    e = _edit(plan(snapshot, "allow-web", "application", add="ssl"))
    assert e.field == "application"
    assert e.before == ["web-browsing"]
    assert e.after == ["web-browsing", "ssl"]
    assert reference_edit_is_mappable(e)


# --- policy rules -------------------------------------------------------


def test_policy_rule_destination_add(all_rb_snapshot: Snapshot) -> None:
    e = _edit(plan(all_rb_snapshot, "qos-1", "destination", add="a2"))
    assert e.referrer_kind == "qos-rule"
    assert e.before == ["qos-only"]
    assert e.after == ["qos-only", "a2"]


def test_post_rulebase_policy_rule_found(all_rb_snapshot: Snapshot) -> None:
    cs = plan(
        all_rb_snapshot,
        "dg-qos",
        "destination",
        location=Location.dg("DG1"),
        rulebase=Rulebase.POST,
        add="a1",
    )
    e = _edit(cs)
    assert e.referrer_kind == "qos-rule"
    assert e.rulebase == "post"
    assert e.after == ["dg-host", "a1"]


def test_application_override_has_no_service_field(all_rb_snapshot: Snapshot) -> None:
    with pytest.raises(PscError) as ei:
        plan(all_rb_snapshot, "appov-1", "service", add="s1")
    assert ei.value.error_type is ErrorType.VALIDATION


# --- ambiguity ----------------------------------------------------------


def test_ambiguous_rule_across_locations_raises_validation() -> None:
    snap = Snapshot(
        policy_rules=[
            PolicyRule(name="dup", location=SHARED, rule_type=RuleType.QOS),
            PolicyRule(name="dup", location=Location.dg("DG1"), rule_type=RuleType.QOS),
        ],
        device_groups=["DG1"],
    )
    with pytest.raises(PscError) as ei:
        plan(snap, "dup", "source", location=None, add="x")
    assert ei.value.error_type is ErrorType.VALIDATION
    assert ei.value.details["candidates"]


# --- setcmd render ------------------------------------------------------


def test_setcmd_add_renders_delete_then_set(snapshot: Snapshot) -> None:
    cs = plan(snapshot, "allow-web", "destination", add="net-10b")
    body = [ln for ln in render_changeset(cs) if not ln.startswith("#")]
    delete_idx = next(
        i for i, ln in enumerate(body) if ln.startswith("delete") and "destination" in ln
    )
    set_idx = next(i for i, ln in enumerate(body) if ln.startswith("set") and "destination" in ln)
    assert delete_idx < set_idx
    assert "net-10b" in body[set_idx]


def test_setcmd_remove_renders_delete_then_set(snapshot: Snapshot) -> None:
    cs = plan(snapshot, "allow-web", "destination", remove="net-10")
    body = [ln for ln in render_changeset(cs) if not ln.startswith("#")]
    assert any(ln.startswith("delete") and "destination" in ln for ln in body)
    set_line = next(ln for ln in body if ln.startswith("set") and "destination" in ln)
    assert "net-10" not in set_line.split("[", 1)[1]  # gone from the member list
    assert "h-web1" in set_line


# --- offline apply round-trip ------------------------------------------


def test_offline_apply_add_roundtrip(fixture_path: Path, tmp_path: Path) -> None:
    snap = parse_config_file(fixture_path)
    cs = plan(snap, "allow-web", "source", add="net-10")
    new_xml = apply_changeset(fixture_path.read_text(encoding="utf-8"), cs)
    p = tmp_path / "out.xml"
    p.write_text(new_xml, encoding="utf-8")
    snap2 = parse_config_file(p)
    rule = next(r for r in snap2.security_rules if r.name == "allow-web")
    assert "net-10" in rule.source


# --- live apply shape ---------------------------------------------------


def test_live_apply_remove_op_shape(snapshot: Snapshot) -> None:
    cs = plan(snapshot, "allow-web", "destination", remove="net-10")
    ops = plan_xapi_ops(cs)
    assert len(ops) == 1
    op = ops[0]
    assert op.action == "edit"
    assert op.xpath.endswith("/destination")
    assert op.element is not None
    assert "h-web1" in op.element
    assert "net-10" not in op.element
