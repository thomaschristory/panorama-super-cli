from __future__ import annotations

from psc.core.models import (
    Address,
    AddressGroup,
    AddressType,
    Location,
    Service,
    ServiceGroup,
    Snapshot,
    Tag,
)
from psc.tui.session import render_value
from psc.tui.state import SelectionItem


def _item(kind: str, name: str, location: str = "shared") -> SelectionItem:
    return SelectionItem(kind=kind, name=name, location=location)


def test_render_value_address_ip_netmask() -> None:
    snap = Snapshot(addresses=[Address(name="a", type=AddressType.IP_NETMASK, value="10.0.0.0/24")])
    assert render_value(snap, _item("address", "a")) == "10.0.0.0/24"


def test_render_value_address_ip_range() -> None:
    snap = Snapshot(
        addresses=[Address(name="a", type=AddressType.IP_RANGE, value="10.0.0.1-10.0.0.9")]
    )
    assert render_value(snap, _item("address", "a")) == "10.0.0.1-10.0.0.9"


def test_render_value_address_fqdn() -> None:
    snap = Snapshot(addresses=[Address(name="a", type=AddressType.FQDN, value="example.com")])
    assert render_value(snap, _item("address", "a")) == "example.com"


def test_render_value_service_tcp_with_dest_port() -> None:
    snap = Snapshot(services=[Service(name="s", protocol="tcp", destination_port="443")])
    assert render_value(snap, _item("service", "s")) == "tcp/443"


def test_render_value_service_udp_with_source_port() -> None:
    snap = Snapshot(
        services=[
            Service(
                name="s",
                protocol="udp",
                destination_port="53",
                source_port="1024-65535",
            )
        ]
    )
    assert render_value(snap, _item("service", "s")) == "udp/53 src:1024-65535"


def test_render_value_address_group_static() -> None:
    snap = Snapshot(address_groups=[AddressGroup(name="g", static_members=["a", "b", "c"])])
    assert render_value(snap, _item("address-group", "g")) == "{3 members}"


def test_render_value_address_group_dynamic() -> None:
    snap = Snapshot(address_groups=[AddressGroup(name="g", dynamic_filter="'prod' and 'web'")])
    assert render_value(snap, _item("address-group", "g")) == "filter: 'prod' and 'web'"


def test_render_value_service_group() -> None:
    snap = Snapshot(service_groups=[ServiceGroup(name="sg", members=["s1", "s2"])])
    assert render_value(snap, _item("service-group", "sg")) == "{2 members}"


def test_render_value_tag_color() -> None:
    snap = Snapshot(tags=[Tag(name="t", color="color5")])
    assert render_value(snap, _item("tag", "t")) == "color5"


def test_render_value_none_field_is_empty() -> None:
    # Tag with no color -> empty string, never crashes.
    snap = Snapshot(tags=[Tag(name="t")])
    assert render_value(snap, _item("tag", "t")) == ""


def test_render_value_missing_object_is_empty() -> None:
    snap = Snapshot()
    assert render_value(snap, _item("address", "ghost")) == ""


def test_render_value_respects_location() -> None:
    snap = Snapshot(
        addresses=[
            Address(
                name="a",
                type=AddressType.IP_NETMASK,
                value="10.9.9.9/32",
                location=Location.dg("dg1"),
            )
        ]
    )
    assert render_value(snap, _item("address", "a", "dg1")) == "10.9.9.9/32"
    # Same name, wrong location -> not found.
    assert render_value(snap, _item("address", "a", "shared")) == ""
