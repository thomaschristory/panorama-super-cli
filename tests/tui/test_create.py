from __future__ import annotations

import pytest

from psc.core.changeset import ObjectKind
from psc.core.source import OfflineSource
from psc.output.errors import PscError
from psc.tui.screens.create import location_options, plan_create
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _session(workbench_xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


# --- plan_create: happy path per kind --------------------------------------


def test_plan_create_address_ip_netmask(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    cs = plan_create(
        sess,
        "address",
        {"name": "new-host", "type": "ip-netmask", "value": "10.9.9.9/32"},
        "shared",
    )
    assert not cs.is_blocked
    assert len(cs.upserts) == 1
    up = cs.upserts[0]
    assert up.kind is ObjectKind.ADDRESS
    assert up.name == "new-host"
    assert up.fields["ip-netmask"] == "10.9.9.9/32"


def test_plan_create_address_fqdn(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    cs = plan_create(
        sess,
        "address",
        {"name": "cdn", "type": "fqdn", "value": "example.com"},
        "shared",
    )
    assert not cs.is_blocked
    assert cs.upserts[0].fields["fqdn"] == "example.com"


def test_plan_create_service_tcp_port(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    cs = plan_create(
        sess,
        "service",
        {"name": "tcp-9000", "protocol": "tcp", "dest-port": "9000"},
        "shared",
    )
    assert not cs.is_blocked
    up = cs.upserts[0]
    assert up.kind is ObjectKind.SERVICE
    assert up.fields["protocol/tcp/port"] == "9000"


def test_plan_create_address_group_with_members(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    cs = plan_create(
        sess,
        "address-group",
        {"name": "web-pool", "members": "web-srv-01, db-gw"},
        "shared",
    )
    assert not cs.is_blocked
    up = cs.upserts[0]
    assert up.kind is ObjectKind.ADDRESS_GROUP
    assert up.members == ["web-srv-01", "db-gw"]


def test_plan_create_service_group(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    cs = plan_create(
        sess,
        "service-group",
        {"name": "svc-bundle", "members": "tcp-8443"},
        "shared",
    )
    assert not cs.is_blocked
    up = cs.upserts[0]
    assert up.kind is ObjectKind.SERVICE_GROUP
    assert up.members == ["tcp-8443"]


def test_plan_create_tag_color(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    cs = plan_create(
        sess,
        "tag",
        {"name": "prod", "color": "color5", "comments": "production"},
        "shared",
    )
    assert not cs.is_blocked
    up = cs.upserts[0]
    assert up.kind is ObjectKind.TAG
    assert up.fields["color"] == "color5"


# --- plan_create: validation / blockers ------------------------------------


def test_plan_create_invalid_value_raises(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    # A malformed port is a hard VALIDATION error in crud (raised, not blocker).
    with pytest.raises(PscError):
        plan_create(
            sess,
            "service",
            {"name": "bad-svc", "protocol": "tcp", "dest-port": "99999"},
            "shared",
        )


def test_plan_create_bad_type_raises(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    with pytest.raises(PscError):
        plan_create(
            sess,
            "address",
            {"name": "x", "type": "not-a-type", "value": "1.2.3.4/32"},
            "shared",
        )


def test_plan_create_cross_kind_collision_is_blocked(workbench_xml: str) -> None:
    # Stage an address-group named "clash", then create an address of the same
    # name/location: crud records a cross-kind namespace collision blocker.
    sess = _session(workbench_xml)
    grp = plan_create(sess, "address-group", {"name": "clash", "members": "db-gw"}, "shared")
    sess.stage("create clash group", grp)
    cs = plan_create(
        sess,
        "address",
        {"name": "clash", "type": "ip-netmask", "value": "10.0.0.1/32"},
        "shared",
    )
    assert cs.is_blocked


def test_staging_a_blocked_create_is_refused(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    grp = plan_create(sess, "address-group", {"name": "clash", "members": "db-gw"}, "shared")
    sess.stage("create clash group", grp)
    cs = plan_create(
        sess,
        "address",
        {"name": "clash", "type": "ip-netmask", "value": "10.0.0.1/32"},
        "shared",
    )
    with pytest.raises(PscError):
        sess.stage("create clash address", cs)


def test_location_options_lists_shared_and_dgs(workbench_xml_two_dg: str) -> None:
    sess = _session(workbench_xml_two_dg)
    assert location_options(sess) == ["shared", "dg1", "dg2"]
