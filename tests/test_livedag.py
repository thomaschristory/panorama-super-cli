"""The pure live-DAG parser: op-command XML to a membership map (#183).

No device, no SDK, no I/O. Every fixture below is a hand-authored string that
shows the shape psc expects from PAN-OS.
"""

from __future__ import annotations

import pytest

from psc.core.livedag import (
    CONNECTED_DEVICES_CMD,
    REGISTERED_IP_CMD,
    ConnectedDevices,
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


def test_an_empty_result_gives_an_empty_map() -> None:
    # A device that holds no registration is a valid answer.
    assert parse_registered_ip('<response status="success"><result/></response>').by_value == {}


def test_a_success_answer_without_a_result_element_is_refused() -> None:
    # CRITICAL (#183): a shape psc cannot read must never read as "nothing is
    # registered". That reads absent data as full coverage, and it puts a live
    # host on the delete list. A successful op answer always holds <result>.
    for text in ('<response status="success"/>', "<response/>"):
        with pytest.raises(PscError) as exc:
            parse_registered_ip(text)
        assert exc.value.error_type is ErrorType.TRANSPORT
        assert REGISTERED_IP_CMD in exc.value.message


def test_a_count_that_disagrees_with_the_rows_is_refused() -> None:
    # CRITICAL (#183): the device counts 1200 registrations, and psc reads no
    # row. psc refuses, so the firewall counts as a firewall that did not
    # answer, and the caveat cannot claim full coverage.
    with pytest.raises(PscError) as exc:
        parse_registered_ip(
            '<response status="success"><result><count>1200</count></result></response>'
        )
    assert exc.value.error_type is ErrorType.TRANSPORT
    assert "1200" in exc.value.message

    with pytest.raises(PscError):
        parse_registered_ip(
            '<response status="success"><result>'
            '<entry ip="10.1.1.5"/><count>500</count>'
            "</result></response>"
        )


def test_a_count_that_agrees_with_the_rows_parses() -> None:
    reg = parse_registered_ip(_REGISTERED)  # <count>2</count>, two entries
    assert len(reg.by_value) == 2


def test_an_absent_count_still_reads_the_rows() -> None:
    # psc has nothing to compare, and it read a row, so the shape is known.
    assert parse_registered_ip(
        '<response status="success"><result><entry ip="10.1.1.5"/></result></response>'
    ).by_value == {"10.1.1.5": frozenset()}


def test_a_count_that_is_not_a_number_is_refused() -> None:
    # psc cannot check the rows against such a count, so the shape is unknown.
    with pytest.raises(PscError) as exc:
        parse_registered_ip(
            '<response status="success"><result>'
            '<entry ip="10.1.1.5"/><count>many</count>'
            "</result></response>"
        )
    assert exc.value.error_type is ErrorType.TRANSPORT
    assert "not a number" in exc.value.message


def test_an_empty_result_is_a_valid_empty_answer() -> None:
    # A firewall that holds no registration is a normal answer.
    assert parse_registered_ip('<response status="success"><result/></response>').by_value == {}
    assert (
        parse_registered_ip(
            '<response status="success"><result><count>0</count></result></response>'
        ).by_value
        == {}
    )


def test_rows_at_an_unknown_depth_are_refused() -> None:
    # CRITICAL (#183): the rows sit one level deeper than psc reads. The
    # `<count>` guard shares the depth of the row xpath, so it misses too. psc
    # must never read this answer as "the firewall holds no registration".
    with pytest.raises(PscError) as exc:
        parse_registered_ip(
            '<response status="success"><result><registered-ip>'
            "<count>2</count>"
            '<entry ip="10.1.1.5"><tag><member>prod</member></tag></entry>'
            '<entry ip="10.1.1.6"/>'
            "</registered-ip></result></response>"
        )
    assert exc.value.error_type is ErrorType.TRANSPORT
    assert "registered-ip" in exc.value.message


def test_rows_at_an_unknown_depth_with_no_count_are_refused() -> None:
    with pytest.raises(PscError) as exc:
        parse_registered_ip(
            '<response status="success"><result><entries>'
            '<entry ip="10.1.1.5"/>'
            "</entries></result></response>"
        )
    assert exc.value.error_type is ErrorType.TRANSPORT


def test_rows_at_an_unknown_depth_with_a_bad_count_are_refused() -> None:
    with pytest.raises(PscError) as exc:
        parse_registered_ip(
            '<response status="success"><result><registered-ip>'
            "<count>many</count>"
            '<entry ip="10.1.1.5"/>'
            "</registered-ip></result></response>"
        )
    assert exc.value.error_type is ErrorType.TRANSPORT


def test_a_row_with_no_ip_attribute_still_counts_as_a_row() -> None:
    # The device counts the row, and psc skips its value. A skipped row must not
    # look like a short read, because the warning already names it.
    reg = parse_registered_ip(
        '<response status="success"><result>'
        '<entry ip="10.1.1.5"/><entry/><count>2</count>'
        "</result></response>"
    )
    assert reg.by_value == {"10.1.1.5": frozenset()}
    assert len(reg.warnings) == 1


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


def test_malformed_xml_raises_a_typed_transport_error() -> None:
    # An answer that psc cannot read is a failed query, so the exit code is 7.
    with pytest.raises(PscError) as exc:
        parse_registered_ip("<response")
    assert exc.value.error_type is ErrorType.TRANSPORT
    assert REGISTERED_IP_CMD in exc.value.message


def test_error_status_response_raises_a_typed_transport_error() -> None:
    with pytest.raises(PscError) as exc:
        parse_registered_ip('<response status="error"><msg>Invalid command</msg></response>')
    assert exc.value.error_type is ErrorType.TRANSPORT


# --- device-list parsing --------------------------------------------------


def test_parse_connected_devices_keeps_a_row_that_psc_cannot_query() -> None:
    # CRITICAL (#183): Panorama named firewall 002 and the row with no serial
    # number. psc cannot read either one. That is a gap in the coverage, and a
    # dropped row would read as an absence.
    devices = parse_connected_devices(_DEVICES)
    assert devices.devices == [ManagedDevice(serial="001", hostname="fw-a")]
    assert [d.label for d in devices.unreadable] == ["002", "fw-c"]
    assert "002" in devices.unreadable[0].reason
    assert "fw-c" in devices.unreadable[1].reason


def test_a_serial_less_row_with_no_hostname_still_gets_a_label() -> None:
    devices = parse_connected_devices(
        '<response status="success"><result><devices>'
        "<entry><connected>yes</connected></entry>"
        "</devices></result></response>"
    )
    assert devices.devices == []
    assert [d.label for d in devices.unreadable] == ["row 1"]


def test_a_device_row_without_a_connected_child_counts_as_connected() -> None:
    # `show devices connected` returns connected devices already, and it can
    # omit <connected>. A missing element must not empty the device list.
    devices = parse_connected_devices(
        '<response status="success"><result><devices>'
        "<entry><serial>009</serial><hostname>fw-x</hostname></entry>"
        "</devices></result></response>"
    )
    assert devices.devices == [ManagedDevice(serial="009", hostname="fw-x")]
    assert devices.unreadable == []


def test_absent_device_list_gives_an_empty_list() -> None:
    answer = parse_connected_devices('<response status="success"><result/></response>')
    assert answer == ConnectedDevices()


def test_a_device_answer_without_a_result_element_is_refused() -> None:
    with pytest.raises(PscError) as exc:
        parse_connected_devices('<response status="success"/>')
    assert exc.value.error_type is ErrorType.TRANSPORT


def test_device_parse_error_names_the_command() -> None:
    with pytest.raises(PscError) as exc:
        parse_connected_devices("<response")
    assert CONNECTED_DEVICES_CMD in exc.value.message


# --- the membership index -------------------------------------------------


def test_build_membership_keeps_one_tag_set_per_device() -> None:
    # CRITICAL (#183): the sets stay apart. A filter runs against each set on
    # its own, so a negated tag of one firewall cannot cancel a match on
    # another firewall.
    m = _membership(**{"001": {"10.1.1.5": {"prod"}}, "002": {"10.1.1.5": {"web"}}})
    assert m.devices == ["001", "002"]
    assert m.indexed_values == 1
    assert m.tag_sets_for(_addr("10.1.1.5")) == [frozenset({"prod"}), frozenset({"web"})]
    # The joined set is for a listing, not for a filter.
    assert m.tags_for(_addr("10.1.1.5")) == frozenset({"prod", "web"})


def test_tag_sets_for_an_address_that_does_not_normalize_is_empty() -> None:
    m = _membership(**{"001": {"10.1.1.5": {"prod"}}})
    assert m.tag_sets_for(_addr("not-an-ip")) == []
    assert m.tags_for(_addr("not-an-ip")) == frozenset()


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


def test_a_failed_device_gives_a_warning_as_well() -> None:
    # `--no-caveat` silences the caveat. The warning channel always runs, so the
    # failed firewall reaches the operator on every run (#183).
    m = build_membership({"001": RegisteredIps()}, failed=["002"])
    assert any("002" in w for w in m.warnings)


def test_an_unreadable_firewall_counts_as_a_failed_firewall() -> None:
    # CRITICAL (#183): Panorama named the firewall, and psc could not query it.
    # The membership must carry that fact, exactly like a firewall that raises.
    unreadable = parse_connected_devices(_DEVICES).unreadable
    m = build_membership({"001": RegisteredIps()}, unreadable=unreadable)
    assert m.failed_devices == ["002", "fw-c"]
    assert m.is_partial is True
    assert any("002" in w for w in m.warnings)
    assert any("fw-c" in w for w in m.warnings)
    assert m.coverage_gap() == "002, fw-c did not answer"


def test_an_unreadable_row_makes_the_coverage_partial() -> None:
    # The subject of the row is unknown, so psc cannot rule out that the row
    # holds the registration of the traced object (#183).
    reg = parse_registered_ip(
        '<response status="success"><result>'
        '<entry ip="10.1.1.5"/><entry/><count>2</count>'
        "</result></response>"
    )
    assert reg.unreadable_rows == 1
    m = build_membership({"001": reg})
    assert m.unreadable_rows == 1
    assert m.is_partial is True
    assert m.coverage_gap() == "psc could not read 1 registered row"


def test_a_value_psc_cannot_normalize_keeps_the_coverage_complete() -> None:
    # psc read the value, and it names the value on the warning channel. The
    # operator can see that the value is not the value of the traced object.
    m = _membership(**{"001": {"not-an-ip": {"prod"}}})
    assert m.is_partial is False
    assert m.coverage_gap() == ""


def test_coverage_gap_names_both_kinds_of_gap() -> None:
    reg = parse_registered_ip(
        '<response status="success"><result><entry/><count>1</count></result></response>'
    )
    m = build_membership({"001": reg}, failed=["002"])
    assert m.coverage_gap() == "002 did not answer; psc could not read 1 registered row"


def test_a_complete_membership_has_no_coverage_gap() -> None:
    assert build_membership({"001": RegisteredIps()}).coverage_gap() == ""
