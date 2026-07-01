from __future__ import annotations

import pytest

from psc.core.diff import (
    ChangedItem,
    KindDiff,
    SnapshotDiff,
    diff_snapshots,
)
from psc.core.models import (
    Address,
    AddressGroup,
    AddressType,
    Location,
    NatRule,
    Rulebase,
    SecurityRule,
    Service,
    ServiceGroup,
    Snapshot,
    Tag,
)


def _addr(name: str, value: str, *, loc: Location | None = None) -> Address:
    return Address(
        name=name,
        location=loc or Location.shared(),
        type=AddressType.IP_NETMASK,
        value=value,
    )


def test_identical_snapshots_have_empty_diff() -> None:
    snap = Snapshot(addresses=[_addr("a", "10.0.0.1"), _addr("b", "10.0.0.2")])
    other = Snapshot(addresses=[_addr("a", "10.0.0.1"), _addr("b", "10.0.0.2")])
    diff = diff_snapshots(snap, other)
    assert diff.is_empty
    assert diff.addresses.added == []
    assert diff.addresses.removed == []
    assert diff.addresses.changed == []


def test_added_and_removed_addresses() -> None:
    base = Snapshot(addresses=[_addr("keep", "10.0.0.1"), _addr("gone", "10.0.0.9")])
    other = Snapshot(addresses=[_addr("keep", "10.0.0.1"), _addr("fresh", "10.0.0.5")])
    diff = diff_snapshots(base, other)
    assert not diff.is_empty
    assert [a.name for a in diff.addresses.added] == ["fresh"]
    assert [a.name for a in diff.addresses.removed] == ["gone"]
    assert diff.addresses.changed == []


def test_changed_address_value() -> None:
    base = Snapshot(addresses=[_addr("web", "10.0.0.1")])
    other = Snapshot(addresses=[_addr("web", "10.0.0.99")])
    diff = diff_snapshots(base, other)
    assert diff.addresses.added == []
    assert diff.addresses.removed == []
    assert len(diff.addresses.changed) == 1
    ch = diff.addresses.changed[0]
    assert isinstance(ch, ChangedItem)
    assert ch.name == "web"
    assert ch.location == "shared"
    assert ch.before["value"] == "10.0.0.1"
    assert ch.after["value"] == "10.0.0.99"


def test_same_name_different_location_is_not_changed() -> None:
    # Identity is (name, location): a shared 'x' and a DG 'x' are distinct.
    base = Snapshot(addresses=[_addr("x", "10.0.0.1")])
    other = Snapshot(addresses=[_addr("x", "10.0.0.1", loc=Location.dg("DG1"))])
    diff = diff_snapshots(base, other)
    assert [a.name for a in diff.addresses.removed] == ["x"]
    assert [a.name for a in diff.addresses.added] == ["x"]
    assert diff.addresses.changed == []


def test_changed_address_group_members() -> None:
    base = Snapshot(address_groups=[AddressGroup(name="g", static_members=["a", "b"])])
    other = Snapshot(address_groups=[AddressGroup(name="g", static_members=["a", "c"])])
    diff = diff_snapshots(base, other)
    assert len(diff.address_groups.changed) == 1
    ch = diff.address_groups.changed[0]
    assert ch.before["static_members"] == ["a", "b"]
    assert ch.after["static_members"] == ["a", "c"]


def test_reordered_group_members_are_not_changed() -> None:
    # PAN-OS membership is a set; a re-export that merely reorders members must
    # not be reported as a change.
    base = Snapshot(address_groups=[AddressGroup(name="g", static_members=["a", "b", "c"])])
    other = Snapshot(address_groups=[AddressGroup(name="g", static_members=["c", "a", "b"])])
    diff = diff_snapshots(base, other)
    assert diff.address_groups.changed == []
    assert diff.is_empty


def test_same_name_pre_and_post_rules_are_distinct() -> None:
    # A rule name can exist in both pre and post rulebases; identity must include
    # the rulebase so they don't collapse and silently drop one.
    base = Snapshot(
        security_rules=[
            SecurityRule(name="r", rulebase=Rulebase.PRE, action="allow"),
            SecurityRule(name="r", rulebase=Rulebase.POST, action="allow"),
        ]
    )
    other = Snapshot(
        security_rules=[
            SecurityRule(name="r", rulebase=Rulebase.PRE, action="allow"),
            SecurityRule(name="r", rulebase=Rulebase.POST, action="deny"),
        ]
    )
    diff = diff_snapshots(base, other)
    # Only the post rule changed; the pre rule is untouched, neither added/removed.
    assert len(diff.security_rules.changed) == 1
    assert diff.security_rules.added == []
    assert diff.security_rules.removed == []
    assert diff.security_rules.changed[0].after["action"] == "deny"


def test_one_sided_scope_is_rejected() -> None:
    with pytest.raises(ValueError, match="both scopes"):
        diff_snapshots(Snapshot(), Snapshot(), scope_base=Location.dg("A"))


def test_service_and_service_group_diffs() -> None:
    base = Snapshot(
        services=[Service(name="ssh", protocol="tcp", destination_port="22")],
        service_groups=[ServiceGroup(name="g", members=["ssh"])],
    )
    other = Snapshot(
        services=[Service(name="ssh", protocol="tcp", destination_port="2222")],
        service_groups=[ServiceGroup(name="g", members=["ssh", "http"])],
    )
    diff = diff_snapshots(base, other)
    assert len(diff.services.changed) == 1
    assert diff.services.changed[0].before["destination_port"] == "22"
    assert diff.services.changed[0].after["destination_port"] == "2222"
    assert len(diff.service_groups.changed) == 1


def test_tag_added_removed() -> None:
    base = Snapshot(tags=[Tag(name="prod")])
    other = Snapshot(tags=[Tag(name="prod"), Tag(name="dev")])
    diff = diff_snapshots(base, other)
    assert [t.name for t in diff.tags.added] == ["dev"]
    assert diff.tags.removed == []


def test_changed_security_rule_fields() -> None:
    base = Snapshot(security_rules=[SecurityRule(name="r1", source=["a"], destination=["b"])])
    other = Snapshot(
        security_rules=[SecurityRule(name="r1", source=["a"], destination=["c"], action="deny")]
    )
    diff = diff_snapshots(base, other)
    assert len(diff.security_rules.changed) == 1
    ch = diff.security_rules.changed[0]
    assert ch.before["destination"] == ["b"]
    assert ch.after["destination"] == ["c"]
    assert ch.before["action"] == "allow"
    assert ch.after["action"] == "deny"


def test_nat_rule_added() -> None:
    base = Snapshot(nat_rules=[])
    other = Snapshot(nat_rules=[NatRule(name="n1", source=["a"], destination=["b"])])
    diff = diff_snapshots(base, other)
    assert [n.name for n in diff.nat_rules.added] == ["n1"]


def test_deterministic_ordering() -> None:
    base = Snapshot(addresses=[])
    other = Snapshot(
        addresses=[_addr("zeta", "10.0.0.3"), _addr("alpha", "10.0.0.1"), _addr("mid", "10.0.0.2")]
    )
    diff = diff_snapshots(base, other)
    assert [a.name for a in diff.addresses.added] == ["alpha", "mid", "zeta"]


def test_snapshotdiff_serializes_to_json() -> None:
    base = Snapshot(addresses=[_addr("web", "10.0.0.1")])
    other = Snapshot(addresses=[_addr("web", "10.0.0.2"), _addr("new", "10.0.0.3")])
    diff = diff_snapshots(base, other)
    payload = diff.model_dump(mode="json")
    assert payload["addresses"]["added"][0]["name"] == "new"
    assert payload["addresses"]["changed"][0]["name"] == "web"
    assert "is_empty" not in payload  # property, not a field


def test_kind_diff_is_empty() -> None:
    kd: KindDiff[Address] = KindDiff()
    assert kd.is_empty
    kd2: KindDiff[Address] = KindDiff(added=[_addr("x", "1.1.1.1")])
    assert not kd2.is_empty


# --- device-group vs device-group (effective visible object sets) ---------


def test_dg_vs_dg_effective_sets() -> None:
    # Two device-groups in one config. Compare each DG's *effective* visible
    # object set (its own objects + inherited shared/ancestors). Shared objects
    # visible to both cancel out; DG-local objects differ.
    snap = Snapshot(
        addresses=[
            _addr("shared-host", "10.0.0.1"),  # visible to both DGs
            _addr("only-a", "10.1.0.1", loc=Location.dg("A")),
            _addr("only-b", "10.2.0.1", loc=Location.dg("B")),
        ],
        device_groups=["A", "B"],
    )
    diff = diff_snapshots(snap, snap, scope_base=Location.dg("A"), scope_other=Location.dg("B"))
    # only-a is in A but not B => removed; only-b is in B not A => added.
    assert [a.name for a in diff.addresses.removed] == ["only-a"]
    assert [a.name for a in diff.addresses.added] == ["only-b"]
    # shared-host is visible to both, identical => neither.
    names = {a.name for a in diff.addresses.added} | {a.name for a in diff.addresses.removed}
    assert "shared-host" not in names


def test_dg_vs_dg_changed_shadowing() -> None:
    # Same name defined in both DGs with different values => changed.
    snap = Snapshot(
        addresses=[
            _addr("svc", "10.1.0.1", loc=Location.dg("A")),
            _addr("svc", "10.2.0.1", loc=Location.dg("B")),
        ],
        device_groups=["A", "B"],
    )
    diff = diff_snapshots(snap, snap, scope_base=Location.dg("A"), scope_other=Location.dg("B"))
    assert len(diff.addresses.changed) == 1
    ch = diff.addresses.changed[0]
    assert ch.name == "svc"
    assert ch.before["value"] == "10.1.0.1"
    assert ch.after["value"] == "10.2.0.1"


def test_dg_vs_dg_identical_effective_set_is_empty() -> None:
    snap = Snapshot(
        addresses=[_addr("shared-host", "10.0.0.1")],
        device_groups=["A", "B"],
    )
    diff = diff_snapshots(snap, snap, scope_base=Location.dg("A"), scope_other=Location.dg("B"))
    assert diff.is_empty


def test_snapshotdiff_type_signature() -> None:
    diff = SnapshotDiff()
    assert diff.is_empty
