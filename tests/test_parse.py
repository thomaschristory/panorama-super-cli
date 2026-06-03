from __future__ import annotations

from psc.core.models import AddressType, Rulebase, Snapshot
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


def test_api_envelope_unwrapped() -> None:
    xml = """<response status="success"><result><config><shared>
      <address><entry name="a"><ip-netmask>1.1.1.1/32</ip-netmask></entry></address>
    </shared></config></result></response>"""
    snap = parse_config(xml)
    assert [a.name for a in snap.addresses] == ["a"]
