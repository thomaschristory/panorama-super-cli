from __future__ import annotations

import pytest

from psc.core import crud
from psc.core.apply_xml import apply_changeset
from psc.core.changeset import ObjectKind
from psc.core.models import (
    Address,
    AddressGroup,
    AddressType,
    Location,
    Service,
    ServiceGroup,
    Snapshot,
)
from psc.core.setcmd import render_changeset
from psc.output.errors import ErrorType, PscError

SHARED = Location.shared()


def _snap() -> Snapshot:
    return Snapshot(
        addresses=[
            Address(name="h1", location=SHARED, type=AddressType.IP_NETMASK, value="1.1.1.1")
        ],
        address_groups=[AddressGroup(name="g1", location=SHARED, static_members=["h1"])],
    )


# --- name / description / tag-name validators ---------------------------------


@pytest.mark.parametrize("bad", ["", "-leading", ".leading", "x" * 64, "bad/name"])
def test_validate_name_rejects(bad: str) -> None:
    with pytest.raises(PscError) as ei:
        crud.validate_name(bad)
    assert ei.value.error_type is ErrorType.VALIDATION


def test_validate_name_accepts() -> None:
    crud.validate_name("Good_name-1.2")
    crud.validate_name("x" * 63)


def test_validate_description_too_long() -> None:
    with pytest.raises(PscError) as ei:
        crud.validate_description("d" * 256)
    assert ei.value.error_type is ErrorType.VALIDATION
    crud.validate_description("d" * 255)


def test_validate_tag_name_len() -> None:
    crud.validate_tag_name("t" * 127)
    with pytest.raises(PscError) as ei:
        crud.validate_tag_name("t" * 128)
    assert ei.value.error_type is ErrorType.VALIDATION


# --- address ------------------------------------------------------------------


def test_plan_address_create_then_update() -> None:
    snap = _snap()
    cs = crud.plan_address(
        snap,
        "new-h",
        AddressType.IP_NETMASK,
        "2.2.2.2",
        description=None,
        tags=[],
        location=SHARED,
    )
    assert len(cs.upserts) == 1
    u = cs.upserts[0]
    assert u.exists is False
    assert u.fields == {"ip-netmask": "2.2.2.2"}

    cs2 = crud.plan_address(
        snap,
        "h1",
        AddressType.IP_NETMASK,
        "9.9.9.9",
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs2.upserts[0].exists is True


def test_plan_address_wildcard_value_not_normalized() -> None:
    cs = crud.plan_address(
        _snap(),
        "w1",
        AddressType.IP_WILDCARD,
        "10.0.0.0/0.0.0.255",
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.upserts[0].fields == {"ip-wildcard": "10.0.0.0/0.0.0.255"}


def test_plan_address_name_collision_with_group_is_blocker() -> None:
    # An address-group "g1" exists at shared; creating an address "g1" collides.
    cs = crud.plan_address(
        _snap(),
        "g1",
        AddressType.IP_NETMASK,
        "2.2.2.2",
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.blockers  # not a raise — plan stays inspectable
    assert cs.is_blocked


def test_plan_address_bad_tag_name_raises() -> None:
    with pytest.raises(PscError) as ei:
        crud.plan_address(
            _snap(),
            "ok",
            AddressType.IP_NETMASK,
            "2.2.2.2",
            description=None,
            tags=["-bad"],
            location=SHARED,
        )
    assert ei.value.error_type is ErrorType.VALIDATION


# --- address-group ------------------------------------------------------------


def test_plan_address_group_static() -> None:
    cs = crud.plan_address_group(
        _snap(),
        "ag",
        static_members=["h1"],
        dynamic_filter=None,
        description=None,
        tags=[],
        location=SHARED,
    )
    u = cs.upserts[0]
    assert u.members == ["h1"]
    assert u.fields == {}


def test_plan_address_group_dynamic_filter_path() -> None:
    cs = crud.plan_address_group(
        _snap(),
        "ag",
        static_members=None,
        dynamic_filter="'t-prod'",
        description=None,
        tags=[],
        location=SHARED,
    )
    u = cs.upserts[0]
    assert u.fields == {"dynamic/filter": "'t-prod'"}
    assert u.members == []


def test_plan_address_group_both_kinds_invalid() -> None:
    with pytest.raises(PscError) as ei:
        crud.plan_address_group(
            _snap(),
            "ag",
            static_members=["h1"],
            dynamic_filter="x",
            description=None,
            tags=[],
            location=SHARED,
        )
    assert ei.value.error_type is ErrorType.VALIDATION


def test_plan_address_group_neither_kind_invalid() -> None:
    with pytest.raises(PscError) as ei:
        crud.plan_address_group(
            _snap(),
            "ag",
            static_members=None,
            dynamic_filter=None,
            description=None,
            tags=[],
            location=SHARED,
        )
    assert ei.value.error_type is ErrorType.VALIDATION


def test_plan_address_group_collision_with_address_is_blocker() -> None:
    cs = crud.plan_address_group(
        _snap(),
        "h1",
        static_members=["h1"],
        dynamic_filter=None,
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.is_blocked


# --- service ------------------------------------------------------------------


def test_plan_service_tcp_port_path() -> None:
    cs = crud.plan_service(
        _snap(),
        "s",
        "tcp",
        destination_port="443",
        source_port=None,
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.upserts[0].fields == {"protocol/tcp/port": "443"}


def test_plan_service_udp_with_source_port() -> None:
    cs = crud.plan_service(
        _snap(),
        "s",
        "udp",
        destination_port="53",
        source_port="1024-2048",
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.upserts[0].fields == {
        "protocol/udp/port": "53",
        "protocol/udp/source-port": "1024-2048",
    }


def test_plan_service_unknown_protocol() -> None:
    with pytest.raises(PscError) as ei:
        crud.plan_service(
            _snap(),
            "s",
            "icmp",
            destination_port="1",
            source_port=None,
            description=None,
            tags=[],
            location=SHARED,
        )
    assert ei.value.error_type is ErrorType.VALIDATION


def test_plan_service_no_ports() -> None:
    with pytest.raises(PscError) as ei:
        crud.plan_service(
            _snap(),
            "s",
            "tcp",
            destination_port=None,
            source_port=None,
            description=None,
            tags=[],
            location=SHARED,
        )
    assert ei.value.error_type is ErrorType.VALIDATION


def test_plan_service_source_port_only_rejected() -> None:
    # A source-port-only service is invalid in PAN-OS: <port> is mandatory.
    with pytest.raises(PscError) as ei:
        crud.plan_service(
            _snap(),
            "s",
            "tcp",
            destination_port=None,
            source_port="1024",
            description=None,
            tags=[],
            location=SHARED,
        )
    assert ei.value.error_type is ErrorType.VALIDATION


def test_plan_service_dest_only_valid() -> None:
    cs = crud.plan_service(
        _snap(),
        "s",
        "tcp",
        destination_port="443",
        source_port=None,
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.upserts[0].fields == {"protocol/tcp/port": "443"}


def test_plan_service_dest_and_source_valid() -> None:
    cs = crud.plan_service(
        _snap(),
        "s",
        "tcp",
        destination_port="443",
        source_port="1024-2048",
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.upserts[0].fields == {
        "protocol/tcp/port": "443",
        "protocol/tcp/source-port": "1024-2048",
    }


@pytest.mark.parametrize("good", ["443", "80,443", "1024-2048", "1-65535"])
def test_plan_service_good_port(good: str) -> None:
    cs = crud.plan_service(
        _snap(),
        "s",
        "tcp",
        destination_port=good,
        source_port=None,
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.upserts[0].fields == {"protocol/tcp/port": good}


@pytest.mark.parametrize(
    "bad", ["abc", "70000", "443;22", "-", "0", "8080-80", "1-2-3", "0-100", "5-3"]
)
def test_plan_service_bad_port(bad: str) -> None:
    with pytest.raises(PscError) as ei:
        crud.plan_service(
            _snap(),
            "s",
            "tcp",
            destination_port=bad,
            source_port=None,
            description=None,
            tags=[],
            location=SHARED,
        )
    assert ei.value.error_type is ErrorType.VALIDATION


# --- service-group ------------------------------------------------------------


def test_plan_service_group() -> None:
    cs = crud.plan_service_group(
        _snap(),
        "sg",
        members=["tcp-443"],
        tags=[],
        location=SHARED,
    )
    assert cs.upserts[0].members == ["tcp-443"]


def test_plan_service_group_empty_members() -> None:
    with pytest.raises(PscError) as ei:
        crud.plan_service_group(_snap(), "sg", members=[], tags=[], location=SHARED)
    assert ei.value.error_type is ErrorType.VALIDATION


# --- service <-> service-group cross-kind name collision (F4) -----------------


def test_plan_service_collision_with_service_group_is_blocker() -> None:
    snap = Snapshot(service_groups=[ServiceGroup(name="x", location=SHARED, members=["m"])])
    cs = crud.plan_service(
        snap,
        "x",
        "tcp",
        destination_port="443",
        source_port=None,
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.is_blocked


def test_plan_service_group_collision_with_service_is_blocker() -> None:
    snap = Snapshot(
        services=[Service(name="x", location=SHARED, protocol="tcp", destination_port="443")]
    )
    cs = crud.plan_service_group(snap, "x", members=["m"], tags=[], location=SHARED)
    assert cs.is_blocked


# --- in-place type/mode switch refusal (F5) -----------------------------------


def test_plan_address_inplace_type_change_blocked() -> None:
    snap = Snapshot(
        addresses=[Address(name="h", location=SHARED, type=AddressType.IP_NETMASK, value="1.1.1.1")]
    )
    cs = crud.plan_address(
        snap,
        "h",
        AddressType.FQDN,
        "example.com",
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.is_blocked
    assert cs.upserts == []  # no dual-type upsert planned


def test_plan_address_same_type_update_ok() -> None:
    snap = Snapshot(
        addresses=[Address(name="h", location=SHARED, type=AddressType.IP_NETMASK, value="1.1.1.1")]
    )
    cs = crud.plan_address(
        snap,
        "h",
        AddressType.IP_NETMASK,
        "9.9.9.9",
        description=None,
        tags=[],
        location=SHARED,
    )
    assert not cs.is_blocked
    assert cs.upserts[0].exists is True
    assert cs.upserts[0].fields == {"ip-netmask": "9.9.9.9"}


def test_plan_address_group_static_to_dynamic_blocked() -> None:
    snap = Snapshot(address_groups=[AddressGroup(name="g", location=SHARED, static_members=["h1"])])
    cs = crud.plan_address_group(
        snap,
        "g",
        static_members=None,
        dynamic_filter="'t-prod'",
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.is_blocked
    assert cs.upserts == []


def test_plan_address_group_dynamic_to_static_blocked() -> None:
    snap = Snapshot(
        address_groups=[AddressGroup(name="g", location=SHARED, dynamic_filter="'t-prod'")]
    )
    cs = crud.plan_address_group(
        snap,
        "g",
        static_members=["h1"],
        dynamic_filter=None,
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.is_blocked
    assert cs.upserts == []


def test_plan_service_protocol_switch_blocked() -> None:
    snap = Snapshot(
        services=[Service(name="s", location=SHARED, protocol="tcp", destination_port="443")]
    )
    cs = crud.plan_service(
        snap,
        "s",
        "udp",
        destination_port="53",
        source_port=None,
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.is_blocked
    assert cs.upserts == []


# --- tag ----------------------------------------------------------------------


def test_plan_tag_color_ok() -> None:
    cs = crud.plan_tag(_snap(), "t", color="color1", comments="hi", location=SHARED)
    assert cs.upserts[0].fields == {"color": "color1", "comments": "hi"}
    assert cs.upserts[0].kind is ObjectKind.TAG


def test_plan_tag_bad_color() -> None:
    with pytest.raises(PscError) as ei:
        crud.plan_tag(_snap(), "t", color="red", comments=None, location=SHARED)
    assert ei.value.error_type is ErrorType.VALIDATION


def test_plan_tag_color43_rejected() -> None:
    with pytest.raises(PscError) as ei:
        crud.plan_tag(_snap(), "t", color="color43", comments=None, location=SHARED)
    assert ei.value.error_type is ErrorType.VALIDATION


def test_plan_tag_no_color_ok() -> None:
    cs = crud.plan_tag(_snap(), "t", color=None, comments=None, location=SHARED)
    assert cs.upserts[0].kind is ObjectKind.TAG


def test_plan_tag_comments_too_long() -> None:
    with pytest.raises(PscError) as ei:
        crud.plan_tag(_snap(), "t", color=None, comments="c" * 256, location=SHARED)
    assert ei.value.error_type is ErrorType.VALIDATION


def test_plan_tag_long_name_ok() -> None:
    # tag names allow up to 127, longer than the 63 object-name limit
    cs = crud.plan_tag(_snap(), "t" * 100, color=None, comments=None, location=SHARED)
    assert cs.upserts[0].name == "t" * 100


# --- rendering + apply round-trip ---------------------------------------------


def test_render_changeset_address_set_line() -> None:
    cs = crud.plan_address(
        _snap(),
        "new-h",
        AddressType.IP_NETMASK,
        "2.2.2.2",
        description="hi",
        tags=["t-prod"],
        location=SHARED,
    )
    lines = render_changeset(cs)
    assert any(line == "set shared address new-h ip-netmask 2.2.2.2" for line in lines)


def test_render_changeset_address_tag_line() -> None:
    cs = crud.plan_address(
        _snap(),
        "new-h",
        AddressType.IP_NETMASK,
        "2.2.2.2",
        description=None,
        tags=["t-prod"],
        location=SHARED,
    )
    lines = render_changeset(cs)
    assert any(line == "set shared address new-h tag [ t-prod ]" for line in lines)


def test_render_changeset_quotes_multiword_description() -> None:
    cs = crud.plan_address(
        _snap(),
        "new-h",
        AddressType.IP_NETMASK,
        "2.2.2.2",
        description="prod web host",
        tags=[],
        location=SHARED,
    )
    lines = render_changeset(cs)
    assert any(line == 'set shared address new-h description "prod web host"' for line in lines)


def test_render_changeset_quotes_spaced_dynamic_filter() -> None:
    cs = crud.plan_address_group(
        _snap(),
        "ag",
        static_members=None,
        dynamic_filter="'prod' and 'web'",
        description=None,
        tags=[],
        location=SHARED,
    )
    lines = render_changeset(cs)
    assert any("""dynamic filter "'prod' and 'web'\"""" in line for line in lines)


def _config_xml() -> str:
    return (
        "<config><shared>"
        "<address><entry name='h1'><ip-netmask>1.1.1.1</ip-netmask></entry></address>"
        "</shared></config>"
    )


def test_apply_xml_create_roundtrip() -> None:
    snap = _snap()
    cs = crud.plan_address(
        snap,
        "new-h",
        AddressType.IP_NETMASK,
        "2.2.2.2",
        description=None,
        tags=[],
        location=SHARED,
    )
    out = apply_changeset(_config_xml(), cs)
    assert "new-h" in out
    assert "2.2.2.2" in out


def test_apply_xml_update_roundtrip_value_changed() -> None:
    snap = _snap()
    cs = crud.plan_address(
        snap,
        "h1",
        AddressType.IP_NETMASK,
        "9.9.9.9",
        description=None,
        tags=[],
        location=SHARED,
    )
    assert cs.upserts[0].exists is True
    out = apply_changeset(_config_xml(), cs)
    assert "9.9.9.9" in out


def test_apply_xml_create_roundtrip_tags() -> None:
    cs = crud.plan_address(
        _snap(),
        "new-h",
        AddressType.IP_NETMASK,
        "2.2.2.2",
        description=None,
        tags=["t-prod"],
        location=SHARED,
    )
    out = apply_changeset(_config_xml(), cs)
    assert "<tag><member>t-prod</member></tag>" in out
