from __future__ import annotations

from psc.core.models import AddressType, Rulebase, RuleType, Snapshot
from psc.core.parse import parse_config


def test_parses_shared_and_device_groups(snapshot: Snapshot) -> None:
    assert {a.name for a in snapshot.addresses} >= {"h-web1", "web-primary", "local-only"}
    assert snapshot.device_groups == ["DG-EDGE"]


def test_address_types_and_values(snapshot: Snapshot) -> None:
    by_name = {a.name: a for a in snapshot.addresses}
    assert by_name["h-web1"].type is AddressType.IP_NETMASK
    assert by_name["rng-db"].type is AddressType.IP_RANGE
    assert by_name["fqdn-example"].type is AddressType.FQDN
    assert by_name["fqdn-example"].value == "example.com"


def test_static_vs_dynamic_groups(snapshot: Snapshot) -> None:
    by_name = {g.name: g for g in snapshot.address_groups}
    assert by_name["grp-web"].static_members == ["h-web1", "web-primary"]
    assert by_name["grp-web"].is_dynamic is False
    assert by_name["grp-dyn"].is_dynamic is True
    assert "t-prod" in (by_name["grp-dyn"].dynamic_filter or "")


def test_rules_locations_and_rulebases(snapshot: Snapshot) -> None:
    sec = {r.name: r for r in snapshot.security_rules}
    assert sec["allow-web"].location.is_shared
    assert sec["allow-web"].rulebase is Rulebase.PRE
    assert sec["edge-rule"].location.name == "DG-EDGE"
    assert sec["edge-rule"].rulebase is Rulebase.POST
    assert sec["edge-rule"].disabled is True


def test_nat_translation_addresses_captured(snapshot: Snapshot) -> None:
    nat = {n.name: n for n in snapshot.nat_rules}
    assert nat["nat-web"].source == ["web-primary"]
    assert nat["nat-web"].source_translation == ["net-10"]


def test_parses_every_policy_rulebase(all_rb_snapshot: Snapshot) -> None:
    by_type: dict[RuleType, list[str]] = {}
    for r in all_rb_snapshot.policy_rules:
        by_type.setdefault(r.rule_type, []).append(r.name)
    # Every new rulebase contributed at least its shared rule.
    assert {t.value for t in by_type} == {
        "pbf",
        "decryption",
        "authentication",
        "qos",
        "application-override",
        "dos",
        "sdwan",
        "tunnel-inspect",
        "network-packet-broker",
    }
    assert "pbf-1" in by_type[RuleType.PBF]
    assert "pbf-2" in by_type[RuleType.PBF]


def test_policy_rule_reference_fields(all_rb_snapshot: Snapshot) -> None:
    by_name = {r.name: r for r in all_rb_snapshot.policy_rules}
    qos = by_name["qos-1"]
    assert qos.source == ["a1"]
    assert qos.destination == ["qos-only"]
    assert qos.service == ["s1"]
    assert qos.tags == ["t1"]
    assert qos.referrer_kind == "qos-rule"
    # application-override is port-based: no service member list.
    assert by_name["appov-1"].service == []


def test_pbf_nexthop_object_captured_literal_ignored(all_rb_snapshot: Snapshot) -> None:
    by_name = {r.name: r for r in all_rb_snapshot.policy_rules}
    # An fqdn nexthop names an address object; a literal ip-address does not.
    assert by_name["pbf-1"].nexthop == "nh-host"
    assert by_name["pbf-2"].nexthop is None


def test_policy_rule_location_and_rulebase(all_rb_snapshot: Snapshot) -> None:
    dg_qos = next(r for r in all_rb_snapshot.policy_rules if r.name == "dg-qos")
    assert dg_qos.location.name == "DG1"
    assert dg_qos.rulebase is Rulebase.POST
    assert dg_qos.rule_type is RuleType.QOS


def test_api_envelope_unwrapped() -> None:
    xml = """<response status="success"><result><config><shared>
      <address><entry name="a"><ip-netmask>1.1.1.1/32</ip-netmask></entry></address>
    </shared></config></result></response>"""
    snap = parse_config(xml)
    assert [a.name for a in snap.addresses] == ["a"]
