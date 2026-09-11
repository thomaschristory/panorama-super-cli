"""The pure live-DAG parser: op-command XML to a membership map (#183).

No device, no SDK, no I/O. Every fixture below is a hand-authored string that
shows the shape psc expects from PAN-OS.
"""

from __future__ import annotations

import pytest

from psc.core.livedag import (
    CONNECTED_DEVICES_CMD,
    REGISTERED_IP_CMD,
    LiveDagMembership,
    ManagedDevice,
    RegisteredIps,
    build_membership,
    parse_connected_devices,
    parse_registered_ip,
)
from psc.core.models import SHARED, Address, AddressType, Location
from psc.output.errors import ErrorType, PscError

_REGISTERED = """
<response status="success"><result>
  <entry ip="10.1.1.5" from_agent="0" persistent="1">
    <tag><member>prod</member><member>web</member></tag>
  </entry>
  <entry ip="10.1.1.6"><tag><member>prod</member></tag></entry>
  <count>2</count>
</result></response>
"""

_DEVICES = """
<response status="success"><result><devices>
  <entry name="001"><serial>001</serial><hostname>fw-a</hostname><connected>yes</connected></entry>
  <entry name="002"><serial>002</serial><hostname>fw-b</hostname><connected>no</connected></entry>
  <entry name="003"><hostname>fw-c</hostname><connected>yes</connected></entry>
</devices></result></response>
"""


def _addr(
    value: str, kind: AddressType = AddressType.IP_NETMASK, loc: Location = SHARED
) -> Address:
    return Address(name="probe", location=loc, type=kind, value=value)


def _membership(**per_device: dict[str, set[str]]) -> LiveDagMembership:
    return build_membership(
        {
            serial: RegisteredIps(by_value={v: frozenset(t) for v, t in rows.items()})
            for serial, rows in per_device.items()
        }
    )


# --- registered-ip parsing ------------------------------------------------


def test_parses_registered_ip_entries_into_a_value_map() -> None:
    reg = parse_registered_ip(_REGISTERED)
    assert reg.by_value == {
        "10.1.1.5": frozenset({"prod", "web"}),
        "10.1.1.6": frozenset({"prod"}),
    }
    assert reg.warnings == []


def test_registered_entry_without_a_tag_child_gives_an_empty_tag_set() -> None:
    reg = parse_registered_ip(
        '<response status="success"><result><entry ip="10.1.1.7"/></result></response>'
    )
    assert reg.by_value == {"10.1.1.7": frozenset()}


def test_registered_entry_with_an_empty_member_list_gives_an_empty_tag_set() -> None:
    reg = parse_registered_ip(
        '<response status="success"><result><entry ip="10.1.1.8"><tag/></entry></result></response>'
    )
    assert reg.by_value == {"10.1.1.8": frozenset()}


def test_empty_or_absent_result_gives_an_empty_map() -> None:
    assert parse_registered_ip('<response status="success"><result/></response>').by_value == {}
    assert parse_registered_ip('<response status="success"/>').by_value == {}


def test_entry_without_an_ip_attribute_is_a_warning_not_a_failure() -> None:
    reg = parse_registered_ip(
        '<response status="success"><result>'
        '<entry ip="10.1.1.5"><tag><member>prod</member></tag></entry>'
        "<entry><tag><member>x</member></tag></entry>"
        "</result></response>"
    )
    assert reg.by_value == {"10.1.1.5": frozenset({"prod"})}
    assert len(reg.warnings) == 1
    assert reg.warnings[0]


def test_unexpected_child_element_does_not_stop_the_scan() -> None:
    reg = parse_registered_ip(
        '<response status="success"><result>'
        '<entry ip="10.1.1.5"><future-field>1</future-field>'
        "<tag><member>prod</member></tag></entry>"
        "</result></response>"
    )
    assert reg.by_value == {"10.1.1.5": frozenset({"prod"})}


def test_a_bare_result_root_parses_too() -> None:
    # Some SDK versions hand back the <result> element alone.
    reg = parse_registered_ip('<result><entry ip="10.1.1.5"/></result>')
    assert reg.by_value == {"10.1.1.5": frozenset()}


def test_malformed_xml_raises_a_typed_input_error() -> None:
    with pytest.raises(PscError) as exc:
        parse_registered_ip("<response")
    assert exc.value.error_type is ErrorType.INPUT
    assert REGISTERED_IP_CMD in exc.value.message


def test_error_status_response_raises_a_typed_input_error() -> None:
    with pytest.raises(PscError) as exc:
        parse_registered_ip('<response status="error"><msg>Invalid command</msg></response>')
    assert exc.value.error_type is ErrorType.INPUT


# --- device-list parsing --------------------------------------------------


def test_parse_connected_devices_drops_a_disconnected_or_serial_less_row() -> None:
    devices = parse_connected_devices(_DEVICES)
    assert devices == [ManagedDevice(serial="001", hostname="fw-a")]


def test_a_device_row_without_a_connected_child_counts_as_connected() -> None:
    # `show devices connected` returns connected devices already, and it can
    # omit <connected>. A missing element must not empty the device list.
    devices = parse_connected_devices(
        '<response status="success"><result><devices>'
        "<entry><serial>009</serial><hostname>fw-x</hostname></entry>"
        "</devices></result></response>"
    )
    assert devices == [ManagedDevice(serial="009", hostname="fw-x")]


def test_absent_device_list_gives_an_empty_list() -> None:
    assert parse_connected_devices('<response status="success"><result/></response>') == []


def test_device_parse_error_names_the_command() -> None:
    with pytest.raises(PscError) as exc:
        parse_connected_devices("<response")
    assert CONNECTED_DEVICES_CMD in exc.value.message


# --- the membership index -------------------------------------------------


def test_build_membership_unions_the_tags_of_one_value_across_devices() -> None:
    m = _membership(**{"001": {"10.1.1.5": {"prod"}}, "002": {"10.1.1.5": {"web"}}})
    assert m.devices == ["001", "002"]
    assert m.indexed_values == 1
    assert m.tags_for(_addr("10.1.1.5")) == frozenset({"prod", "web"})


def test_membership_matches_an_address_by_exact_value_only() -> None:
    # SAFETY: containment must never put a registered host tag on a broader object.
    m = _membership(**{"001": {"10.1.1.5": {"prod"}}})
    assert m.tags_for(_addr("10.1.1.5")) == frozenset({"prod"})
    assert m.tags_for(_addr("10.1.1.5/32")) == frozenset({"prod"})
    assert m.tags_for(_addr("10.1.1.0/24")) == frozenset()
    assert m.tags_for(_addr("10.1.1.1-10.1.1.9", AddressType.IP_RANGE)) == frozenset()


def test_membership_never_matches_an_fqdn_or_a_wildcard_address() -> None:
    # psc does no DNS, so an FQDN object never takes a registered tag.
    m = _membership(**{"001": {"10.1.1.5": {"prod"}}})
    assert m.tags_for(_addr("10.1.1.5", AddressType.FQDN)) == frozenset()
    assert m.tags_for(_addr("10.1.1.5", AddressType.IP_WILDCARD)) == frozenset()


def test_membership_matches_a_registered_range_to_an_ip_range_object() -> None:
    m = _membership(**{"001": {"10.1.1.5-10.1.1.9": {"prod"}}})
    assert m.tags_for(_addr("10.1.1.5-10.1.1.9", AddressType.IP_RANGE)) == frozenset({"prod"})
    assert m.tags_for(_addr("10.1.1.5")) == frozenset()


def test_a_registered_host_does_not_match_a_degenerate_ip_range_object() -> None:
    # A documented false negative in the safe direction: psc keys on the value
    # kind, so a host registration never reaches an `ip-range` object.
    m = _membership(**{"001": {"10.1.1.5": {"prod"}}})
    assert m.tags_for(_addr("10.1.1.5-10.1.1.5", AddressType.IP_RANGE)) == frozenset()


def test_membership_matches_an_ipv6_value_whatever_the_letter_case() -> None:
    m = _membership(**{"001": {"2001:db8::1": {"prod"}}})
    assert m.tags_for(_addr("2001:DB8::1/128")) == frozenset({"prod"})


def test_an_unparseable_registered_value_is_counted_and_warned() -> None:
    m = _membership(**{"001": {"not-an-ip": {"prod"}}})
    assert m.by_key == {}
    assert m.indexed_values == 0
    assert m.skipped_values == 1
    assert len(m.warnings) == 1


def test_a_parse_warning_survives_the_membership_build() -> None:
    reg = parse_registered_ip(
        '<response status="success"><result>'
        "<entry><tag><member>x</member></tag></entry>"
        "</result></response>"
    )
    m = build_membership({"001": reg})
    assert len(m.warnings) == 1


def test_membership_counts_the_registered_values_no_address_object_matches() -> None:
    m = _membership(**{"001": {"10.1.1.5": {"prod"}, "10.9.9.9": {"prod"}}})
    assert m.unmatched_values([_addr("10.1.1.5")]) == 1
    assert m.unmatched_values([]) == 2


def test_unmatched_values_ignores_an_address_that_does_not_normalize() -> None:
    m = _membership(**{"001": {"10.1.1.5": {"prod"}}})
    assert m.unmatched_values([_addr("not-an-ip")]) == 1


def test_build_membership_records_the_devices_that_failed() -> None:
    m = build_membership({"001": RegisteredIps()}, failed=["002"])
    assert m.devices == ["001"]
    assert m.failed_devices == ["002"]
    assert m.is_partial is True


def test_a_complete_membership_is_not_partial() -> None:
    assert build_membership({"001": RegisteredIps()}).is_partial is False
