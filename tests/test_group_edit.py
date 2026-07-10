from __future__ import annotations

import pytest

from psc.core.apply_xml import apply_changeset
from psc.core.group_edit import plan_group_member_edit
from psc.core.models import (
    Address,
    AddressGroup,
    AddressType,
    Location,
    Service,
    ServiceGroup,
    Snapshot,
)
from psc.core.parse import parse_config
from psc.output.errors import ErrorType, PscError


def _snap() -> Snapshot:
    return Snapshot(
        addresses=[
            Address(name="a1", type=AddressType.IP_NETMASK, value="10.0.0.1"),
            Address(name="a2", type=AddressType.IP_NETMASK, value="10.0.0.2"),
        ],
        address_groups=[
            AddressGroup(name="grp", static_members=["a1"]),
            AddressGroup(name="dyn", dynamic_filter="'web'"),
        ],
        services=[Service(name="s1", protocol="tcp", destination_port="80")],
        service_groups=[ServiceGroup(name="svc-grp", members=["s1"])],
    )


def test_add_member_to_address_group() -> None:
    cs = plan_group_member_edit(_snap(), "grp", add="a2")
    (edit,) = cs.reference_edits
    assert edit.referrer_kind == "address-group"
    assert edit.before == ["a1"]
    assert edit.after == ["a1", "a2"]
    assert not cs.blockers


def test_remove_member_from_address_group() -> None:
    cs = plan_group_member_edit(_snap(), "grp", remove="a1")
    (edit,) = cs.reference_edits
    assert edit.after == []


def test_add_is_idempotent_noop() -> None:
    cs = plan_group_member_edit(_snap(), "grp", add="a1")  # already a member
    assert cs.reference_edits == []
    assert cs.is_empty


def test_remove_absent_is_noop() -> None:
    cs = plan_group_member_edit(_snap(), "grp", remove="nope")
    assert cs.reference_edits == []


def test_add_member_to_service_group() -> None:
    cs = plan_group_member_edit(_snap(), "svc-grp", add="s1-extra")
    (edit,) = cs.reference_edits
    assert edit.referrer_kind == "service-group"
    assert edit.after == ["s1", "s1-extra"]


def test_dynamic_group_is_rejected() -> None:
    with pytest.raises(PscError) as exc:
        plan_group_member_edit(_snap(), "dyn", add="a1")
    assert exc.value.error_type is ErrorType.VALIDATION


def test_self_reference_is_rejected() -> None:
    with pytest.raises(PscError) as exc:
        plan_group_member_edit(_snap(), "grp", add="grp")
    assert exc.value.error_type is ErrorType.VALIDATION


def test_unknown_group_is_not_found() -> None:
    with pytest.raises(PscError) as exc:
        plan_group_member_edit(_snap(), "ghost", add="a1")
    assert exc.value.error_type is ErrorType.NOT_FOUND


def test_cross_kind_same_name_is_ambiguous_without_kind() -> None:
    snap = Snapshot(
        address_groups=[AddressGroup(name="dup", static_members=[])],
        service_groups=[ServiceGroup(name="dup", members=[])],
    )
    with pytest.raises(PscError) as exc:
        plan_group_member_edit(snap, "dup", add="x")
    assert exc.value.error_type is ErrorType.VALIDATION
    # --kind disambiguates.
    cs = plan_group_member_edit(snap, "dup", add="x", kind="service-group")
    assert cs.reference_edits[0].referrer_kind == "service-group"


def test_ambiguous_locations_need_location() -> None:
    dg = Location.dg("prod")
    snap = Snapshot(
        address_groups=[
            AddressGroup(name="g", static_members=["a1"]),
            AddressGroup(name="g", static_members=["a1"], location=dg),
        ],
        device_groups=["prod"],
    )
    with pytest.raises(PscError) as exc:
        plan_group_member_edit(snap, "g", add="a2")
    assert exc.value.error_type is ErrorType.VALIDATION
    # --location disambiguates.
    cs = plan_group_member_edit(snap, "g", location=dg, add="a2")
    assert cs.reference_edits[0].referrer_location == "prod"


def test_add_round_trips_through_apply_xml() -> None:
    xml = (
        "<config><shared>"
        '<address><entry name="a1"><ip-netmask>10.0.0.1/32</ip-netmask></entry>'
        '<entry name="a2"><ip-netmask>10.0.0.2/32</ip-netmask></entry></address>'
        '<address-group><entry name="grp"><static><member>a1</member></static></entry>'
        "</address-group></shared></config>"
    )
    snap = parse_config(xml)
    cs = plan_group_member_edit(snap, "grp", add="a2")
    out = apply_changeset(xml, cs)
    after = parse_config(out)
    grp = next(g for g in after.address_groups if g.name == "grp")
    assert grp.static_members == ["a1", "a2"]
