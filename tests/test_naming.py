from __future__ import annotations

from psc.core.changeset import ObjectKind
from psc.core.models import SHARED, Address, AddressType, Location, Service, Snapshot
from psc.core.naming import NamingScheme, lint, plan_rename, sanitize_name
from psc.core.refs import ReferenceGraph


def test_scheme_host_and_network_names() -> None:
    s = NamingScheme()
    assert (
        s.address_name(Address(name="x", type=AddressType.IP_NETMASK, value="10.0.0.10/32"))
        == "H-10.0.0.10"
    )
    assert (
        s.address_name(Address(name="x", type=AddressType.IP_NETMASK, value="10.0.0.0/24"))
        == "N-10.0.0.0_24"
    )


def test_scheme_service_name() -> None:
    s = NamingScheme()
    assert s.service_name(Service(name="x", protocol="tcp", destination_port="443")) == "tcp-443"


def test_sanitize_enforces_pan_rules() -> None:
    assert sanitize_name("1bad/name!") == "1bad_name_"  # leading digit is alphanumeric → kept
    assert sanitize_name("-leading-dash") == "x-leading-dash"  # non-alnum start → prefixed
    assert len(sanitize_name("a" * 100)) == 63


def test_lint_flags_drift(snapshot: Snapshot) -> None:
    findings = {f.current: f for f in lint(snapshot, NamingScheme())}
    assert findings["h-web1"].suggested == "H-10.0.0.10"
    assert findings["h-web1"].compliant is False


def test_rename_repoints_references(snapshot: Snapshot) -> None:
    graph = ReferenceGraph.build(snapshot)
    cs = plan_rename(
        snapshot,
        graph,
        kind=ObjectKind.ADDRESS,
        location_name="shared",
        old_name="h-web1",
        new_name="H-10.0.0.10",
    )
    assert not cs.is_blocked
    edits = {(e.referrer_name, e.field): e.after for e in cs.reference_edits}
    assert "H-10.0.0.10" in edits[("grp-web", "static")]
    assert cs.renames[0].new_name == "H-10.0.0.10"


def test_rename_blocks_on_existing_name(snapshot: Snapshot) -> None:
    graph = ReferenceGraph.build(snapshot)
    cs = plan_rename(
        snapshot,
        graph,
        kind=ObjectKind.ADDRESS,
        location_name="shared",
        old_name="h-web1",
        new_name="web-primary",
    )
    assert cs.is_blocked


def test_rename_blocks_when_repoint_hits_nat_translation(snapshot: Snapshot) -> None:
    """net-10 is referenced by nat-web's source-translation (a nested field with
    no flat member list). The rename can repoint the security-rule destination
    but not the translation field — so applying it would delete the old name out
    from under a dangling reference. Block it (#28)."""
    graph = ReferenceGraph.build(snapshot)
    cs = plan_rename(
        snapshot,
        graph,
        kind=ObjectKind.ADDRESS,
        location_name="shared",
        old_name="net-10",
        new_name="N-10.0.0.0_24",
    )
    assert cs.is_blocked
    assert any("net-10" in b and "nat-web" in b for b in cs.blockers)
    assert cs.op_count == 0


def test_rename_blocks_shared_dg_shadow() -> None:
    # Renaming a shared object to a name a DG already defines is refused.
    snap = Snapshot(
        addresses=[
            Address(name="src", location=SHARED, type=AddressType.IP_NETMASK, value="1.1.1.1/32"),
            Address(
                name="clash",
                location=Location.dg("DG1"),
                type=AddressType.IP_NETMASK,
                value="2.2.2.2/32",
            ),
        ],
        device_groups=["DG1"],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_rename(
        snap,
        graph,
        kind=ObjectKind.ADDRESS,
        location_name="shared",
        old_name="src",
        new_name="clash",
    )
    assert cs.is_blocked
    assert any("DG1" in b for b in cs.blockers)
