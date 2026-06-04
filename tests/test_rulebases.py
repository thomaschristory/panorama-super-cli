from __future__ import annotations

from psc.core.rulebases import POLICY_RULE_TYPES, rule_container


def test_rule_container_maps_referrer_kind_to_xml_tag() -> None:
    # The whole design rests on this: referrer_kind is "{tag}-rule" and the tag
    # is the XML container / set keyword / xpath segment for every rulebase.
    assert rule_container("security-rule") == "security"
    assert rule_container("nat-rule") == "nat"
    assert rule_container("pbf-rule") == "pbf"
    assert rule_container("application-override-rule") == "application-override"
    assert rule_container("network-packet-broker-rule") == "network-packet-broker"


def test_rule_container_none_for_non_rule_referrers() -> None:
    assert rule_container("address-group") is None
    assert rule_container("service-group") is None
    assert rule_container("address") is None
    # A "-rule" suffix on an unknown base is still not a known rulebase.
    assert rule_container("made-up-rule") is None


def test_all_nine_new_rulebases_present() -> None:
    assert set(POLICY_RULE_TYPES) == {
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
