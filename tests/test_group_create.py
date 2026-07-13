"""Building a group out of a set of already-chosen objects (#146).

`plan_group_create` is the engine behind the workbench's `N` spoke: the operator
points at concrete objects, so unlike `crud.plan_address_group` (which takes bare
names) this planner knows each member's *provenance* and can judge whether the
group's location can actually reach it.

Two ways a member reference goes wrong, both of which PAN-OS accepts silently:
the member sits outside the group's visibility cone (dangling), or its name is
shadowed from the group's location by a nearer definition (binds to the wrong
object). Member names are bare and resolved upward from the referrer, so neither
is expressible away — both are blockers.
"""

from __future__ import annotations

import pytest

from psc.core.apply_xml import apply_changeset
from psc.core.group_edit import plan_group_create, suggest_group_location
from psc.core.models import (
    SHARED,
    Address,
    AddressGroup,
    AddressType,
    Location,
    Service,
    ServiceGroup,
    Snapshot,
)
from psc.core.parse import parse_config
from psc.core.refs import Target
from psc.output.errors import ErrorType, PscError

DG_A = Location.dg("dg-a")
DG_B = Location.dg("dg-b")
PARENT = Location.dg("dg-parent")
CHILD = Location.dg("dg-child")


def addr(name: str, loc: Location = SHARED, *, value: str = "10.0.0.1/32") -> Address:
    return Address(name=name, location=loc, type=AddressType.IP_NETMASK, value=value)


def t(kind: str, name: str, loc: Location = SHARED) -> Target:
    return Target(kind=kind, name=name, location=loc)


@pytest.fixture
def snap() -> Snapshot:
    """shared → {dg-a, dg-b}, and shared → dg-parent → dg-child."""
    return Snapshot(
        device_groups=["dg-a", "dg-b", "dg-parent", "dg-child"],
        device_group_parents={"dg-child": "dg-parent"},
        addresses=[
            addr("web", value="10.0.0.1/32"),
            addr("db", value="10.0.0.2/32"),
            addr("local-a", DG_A, value="10.1.0.1/32"),
            addr("local-b", DG_B, value="10.2.0.1/32"),
            addr("p", PARENT, value="10.3.0.1/32"),
            addr("c", CHILD, value="10.4.0.1/32"),
        ],
        address_groups=[AddressGroup(name="existing", static_members=["web"])],
        services=[
            Service(name="http", protocol="tcp", destination_port="80"),
            Service(name="https", protocol="tcp", destination_port="443"),
        ],
        service_groups=[ServiceGroup(name="svc-existing", members=["http"])],
    )


# --- the happy paths --------------------------------------------------------


def test_creates_static_address_group_from_addresses(snap: Snapshot) -> None:
    cs = plan_group_create(snap, "web-tier", SHARED, [t("address", "web"), t("address", "db")])
    assert not cs.is_blocked, cs.blockers
    (upsert,) = cs.upserts
    assert upsert.kind.value == "address-group"
    assert upsert.name == "web-tier"
    assert upsert.location == "shared"
    assert upsert.members == ["web", "db"]
    assert upsert.exists is False


def test_creates_service_group_from_services(snap: Snapshot) -> None:
    cs = plan_group_create(snap, "web-ports", SHARED, [t("service", "http"), t("service", "https")])
    assert not cs.is_blocked, cs.blockers
    (upsert,) = cs.upserts
    assert upsert.kind.value == "service-group"
    assert upsert.members == ["http", "https"]


def test_address_group_may_nest_an_address_group(snap: Snapshot) -> None:
    # A group inside a group is valid PAN-OS; the selection is allowed to mix
    # addresses and address-groups because they share one namespace.
    cs = plan_group_create(
        snap, "outer", SHARED, [t("address", "web"), t("address-group", "existing")]
    )
    assert not cs.is_blocked, cs.blockers
    assert cs.upserts[0].members == ["web", "existing"]


def test_service_group_may_nest_a_service_group(snap: Snapshot) -> None:
    cs = plan_group_create(
        snap, "svc-outer", SHARED, [t("service", "http"), t("service-group", "svc-existing")]
    )
    assert not cs.is_blocked, cs.blockers
    assert cs.upserts[0].kind.value == "service-group"


def test_description_and_tags_reach_the_upsert(snap: Snapshot) -> None:
    cs = plan_group_create(
        snap,
        "web-tier",
        SHARED,
        [t("address", "web")],
        description="front end",
        tags=["prod"],
    )
    assert cs.upserts[0].fields["description"] == "front end"
    assert cs.upserts[0].tags == ["prod"]


def test_duplicate_member_names_collapse(snap: Snapshot) -> None:
    # The member list is a set of names on the device; a name repeated in the
    # selection must not be written twice.
    cs = plan_group_create(
        snap, "web-tier", SHARED, [t("address", "web"), t("address", "web"), t("address", "db")]
    )
    assert cs.upserts[0].members == ["web", "db"]


def test_shared_member_is_visible_from_a_device_group(snap: Snapshot) -> None:
    # Upward resolution: a dg-a group reaching a shared object is the whole point.
    cs = plan_group_create(
        snap, "mixed", DG_A, [t("address", "web"), t("address", "local-a", DG_A)]
    )
    assert not cs.is_blocked, cs.blockers


def test_ancestor_member_is_visible_from_a_child_device_group(snap: Snapshot) -> None:
    cs = plan_group_create(
        snap, "nested", CHILD, [t("address", "p", PARENT), t("address", "c", CHILD)]
    )
    assert not cs.is_blocked, cs.blockers


# --- malformed input (refused before a plan exists) -------------------------


def test_empty_selection_is_an_error(snap: Snapshot) -> None:
    with pytest.raises(PscError) as exc:
        plan_group_create(snap, "empty", SHARED, [])
    assert exc.value.error_type is ErrorType.VALIDATION


def test_mixed_namespaces_are_an_error(snap: Snapshot) -> None:
    # There is no group kind that holds both an address and a service.
    with pytest.raises(PscError) as exc:
        plan_group_create(snap, "mixed", SHARED, [t("address", "web"), t("service", "http")])
    assert exc.value.error_type is ErrorType.VALIDATION
    assert "both" in str(exc.value) or "mix" in str(exc.value)


def test_a_tag_cannot_be_a_group_member(snap: Snapshot) -> None:
    with pytest.raises(PscError) as exc:
        plan_group_create(snap, "grp", SHARED, [t("tag", "prod")])
    assert exc.value.error_type is ErrorType.VALIDATION


def test_a_group_cannot_contain_itself(snap: Snapshot) -> None:
    with pytest.raises(PscError) as exc:
        plan_group_create(snap, "web", SHARED, [t("address", "web")])
    assert exc.value.error_type is ErrorType.VALIDATION


def test_a_group_cannot_contain_its_own_name_from_another_location(snap: Snapshot) -> None:
    # `web`@dg-a's member `web` would resolve to the group itself once written.
    with pytest.raises(PscError) as exc:
        plan_group_create(snap, "web", DG_A, [t("address", "web")])
    assert exc.value.error_type is ErrorType.VALIDATION


def test_service_groups_have_no_description(snap: Snapshot) -> None:
    with pytest.raises(PscError) as exc:
        plan_group_create(snap, "svc", SHARED, [t("service", "http")], description="nope")
    assert exc.value.error_type is ErrorType.VALIDATION


# --- blockers ---------------------------------------------------------------


def test_device_group_member_blocks_a_shared_group(snap: Snapshot) -> None:
    # A shared group naming `local-a` would dangle: shared cannot see into dg-a.
    cs = plan_group_create(snap, "grp", SHARED, [t("address", "local-a", DG_A)])
    assert cs.is_blocked
    assert "not visible" in cs.blockers[0]


def test_sibling_device_group_member_blocks(snap: Snapshot) -> None:
    cs = plan_group_create(snap, "grp", DG_A, [t("address", "local-b", DG_B)])
    assert cs.is_blocked
    assert "not visible" in cs.blockers[0]


def test_descendant_member_blocks_an_ancestor_group(snap: Snapshot) -> None:
    # Visibility runs upward only: dg-parent cannot see dg-child's objects.
    cs = plan_group_create(snap, "grp", PARENT, [t("address", "c", CHILD)])
    assert cs.is_blocked
    assert "not visible" in cs.blockers[0]


def test_shadowed_member_name_blocks() -> None:
    # `web` exists in shared AND dg-a. A dg-a group naming `web` binds to dg-a's
    # copy, not the shared object the operator selected — and PAN-OS has no
    # syntax to say "the shared one".
    snap = Snapshot(
        device_groups=["dg-a"],
        addresses=[addr("web"), addr("web", DG_A, value="10.9.9.9/32")],
    )
    cs = plan_group_create(snap, "grp", DG_A, [t("address", "web", SHARED)])
    assert cs.is_blocked
    assert "shadow" in cs.blockers[0]


def test_selecting_the_shadow_itself_is_fine() -> None:
    # Same config, but the operator picked dg-a's own `web` — which is exactly
    # what a dg-a group resolves to. Nothing surprising, nothing blocked.
    snap = Snapshot(
        device_groups=["dg-a"],
        addresses=[addr("web"), addr("web", DG_A, value="10.9.9.9/32")],
    )
    cs = plan_group_create(snap, "grp", DG_A, [t("address", "web", DG_A)])
    assert not cs.is_blocked, cs.blockers


def test_existing_group_at_the_same_location_blocks(snap: Snapshot) -> None:
    # `N` creates. Growing a group is `G` / `psc group edit-member --add`; a
    # silent member-list overwrite would be the surprising read of this key.
    cs = plan_group_create(snap, "existing", SHARED, [t("address", "db")])
    assert cs.is_blocked
    assert any("already exists" in b for b in cs.blockers)


def test_existing_service_group_at_the_same_location_blocks(snap: Snapshot) -> None:
    cs = plan_group_create(snap, "svc-existing", SHARED, [t("service", "https")])
    assert cs.is_blocked
    assert any("already exists" in b for b in cs.blockers)


def test_cross_kind_collision_still_blocks(snap: Snapshot) -> None:
    # crud's namespace-collision blocker is reused verbatim: an address and an
    # address-group cannot share a name at one location.
    cs = plan_group_create(snap, "db", SHARED, [t("address", "web")])
    assert cs.is_blocked


# --- warnings ---------------------------------------------------------------


def test_same_name_elsewhere_warns_not_blocks(snap: Snapshot) -> None:
    # A dg-a group named `existing` shadows the shared one for dg-a's rules.
    # Legal PAN-OS, easy to do by accident — say so, but let it through.
    cs = plan_group_create(snap, "existing", DG_A, [t("address", "web")])
    assert not cs.is_blocked, cs.blockers
    assert any("existing" in w and "shared" in w for w in cs.warnings)


def test_no_warning_when_the_name_is_unique(snap: Snapshot) -> None:
    cs = plan_group_create(snap, "brand-new", SHARED, [t("address", "web")])
    assert cs.warnings == []


# --- location suggestion ----------------------------------------------------


def test_suggest_all_shared(snap: Snapshot) -> None:
    assert suggest_group_location(snap, ["shared", "shared"]) == "shared"


def test_suggest_single_device_group(snap: Snapshot) -> None:
    assert suggest_group_location(snap, ["dg-a"]) == "dg-a"


def test_suggest_device_group_over_shared(snap: Snapshot) -> None:
    # Narrowest cone that sees both: dg-a (which also sees shared).
    assert suggest_group_location(snap, ["dg-a", "shared"]) == "dg-a"


def test_suggest_child_for_a_parent_child_mix(snap: Snapshot) -> None:
    assert suggest_group_location(snap, ["dg-parent", "dg-child"]) == "dg-child"


def test_suggest_none_for_siblings(snap: Snapshot) -> None:
    # No location can see into two sibling device-groups at once.
    assert suggest_group_location(snap, ["dg-a", "dg-b"]) is None


def test_suggest_shared_for_no_members(snap: Snapshot) -> None:
    assert suggest_group_location(snap, []) == "shared"


# --- round trip -------------------------------------------------------------


def test_plan_round_trips_through_apply_xml() -> None:
    xml = (
        "<config><shared>"
        '<address><entry name="a1"><ip-netmask>10.0.0.1/32</ip-netmask></entry>'
        '<entry name="a2"><ip-netmask>10.0.0.2/32</ip-netmask></entry></address>'
        "</shared></config>"
    )
    snap = parse_config(xml)
    cs = plan_group_create(
        snap, "pair", SHARED, [t("address", "a1"), t("address", "a2")], description="both"
    )
    after = parse_config(apply_changeset(xml, cs))
    grp = next(g for g in after.address_groups if g.name == "pair")
    assert grp.static_members == ["a1", "a2"]
    assert grp.description == "both"
    assert grp.location.name == "shared"


def test_service_group_plan_round_trips_through_apply_xml() -> None:
    xml = (
        "<config><shared><service>"
        '<entry name="http"><protocol><tcp><port>80</port></tcp></protocol></entry>'
        '<entry name="https"><protocol><tcp><port>443</port></tcp></protocol></entry>'
        "</service></shared></config>"
    )
    snap = parse_config(xml)
    cs = plan_group_create(snap, "web-ports", SHARED, [t("service", "http"), t("service", "https")])
    after = parse_config(apply_changeset(xml, cs))
    sg = next(g for g in after.service_groups if g.name == "web-ports")
    assert sg.members == ["http", "https"]
