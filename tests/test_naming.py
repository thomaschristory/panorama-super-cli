from __future__ import annotations

from psc.core.changeset import ObjectKind
from psc.core.models import (
    SHARED,
    Address,
    AddressGroup,
    AddressType,
    Location,
    SecurityRule,
    Service,
    Snapshot,
)
from psc.core.naming import NamingScheme, lint, plan_apply_scheme, plan_rename, sanitize_name
from psc.core.parse import parse_config
from psc.core.refs import ReferenceGraph


def _addr(name: str, value: str, loc: Location = SHARED) -> Address:
    return Address(name=name, location=loc, type=AddressType.IP_NETMASK, value=value)


def test_rename_tag_preserves_other_tags_on_a_security_rule() -> None:
    # A security rule tagged [t-old, t-keep]: renaming t-old must rewrite that
    # one member and leave t-keep intact — not wipe the rule's tag list.
    xml = """<config><shared>
      <tag><entry name="t-old"/><entry name="t-keep"/></tag>
      <pre-rulebase><security><rules>
        <entry name="r">
          <source><member>any</member></source>
          <tag><member>t-old</member><member>t-keep</member></tag>
        </entry>
      </rules></security></pre-rulebase>
    </shared></config>"""
    snap = parse_config(xml)
    graph = ReferenceGraph.build(snap)
    cs = plan_rename(
        snap,
        graph,
        kind=ObjectKind.TAG,
        location_name="shared",
        old_name="t-old",
        new_name="T-OLD",
    )
    assert not cs.is_blocked
    edit = next(e for e in cs.reference_edits if e.referrer_name == "r")
    assert edit.after == ["T-OLD", "t-keep"]


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


def test_rename_repoints_across_new_rulebases(all_rb_snapshot: Snapshot) -> None:
    # a1 is a source in every new rulebase; a rename must repoint them all.
    graph = ReferenceGraph.build(all_rb_snapshot)
    cs = plan_rename(
        all_rb_snapshot,
        graph,
        kind=ObjectKind.ADDRESS,
        location_name="shared",
        old_name="a1",
        new_name="H-10.1.0.1",
    )
    assert not cs.is_blocked
    by_kind = {(e.referrer_kind, e.referrer_name): e.after for e in cs.reference_edits}
    assert "H-10.1.0.1" in by_kind[("tunnel-inspect-rule", "ti-1")]
    assert "H-10.1.0.1" in by_kind[("dos-rule", "dos-1")]


def test_rename_blocks_when_repoint_hits_pbf_nexthop(all_rb_snapshot: Snapshot) -> None:
    # Renaming a PBF next-hop object can't be repointed (nested field) → block.
    graph = ReferenceGraph.build(all_rb_snapshot)
    cs = plan_rename(
        all_rb_snapshot,
        graph,
        kind=ObjectKind.ADDRESS,
        location_name="shared",
        old_name="nh-host",
        new_name="GW-1",
    )
    assert cs.is_blocked
    assert any("nh-host" in b and "pbf-1" in b for b in cs.blockers)
    assert cs.op_count == 0


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


# --- name apply --all (bulk scheme rename, issue #15) ---------------------


def test_apply_all_renames_every_noncompliant_and_repoints() -> None:
    # Two non-compliant hosts, both members of one group and a rule; the batch
    # renames both and rewrites the shared referrer fields to the FINAL names.
    snap = Snapshot(
        addresses=[_addr("h-a", "10.0.0.1/32"), _addr("h-b", "10.0.0.2/32")],
        address_groups=[
            AddressGroup(name="grp", location=SHARED, static_members=["h-a", "h-b"]),
        ],
        security_rules=[
            SecurityRule(name="r", location=SHARED, source=["h-a"], destination=["h-b"]),
        ],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_apply_scheme(snap, graph, NamingScheme())
    assert not cs.is_blocked
    new_names = {r.old_name: r.new_name for r in cs.renames}
    assert new_names == {"h-a": "H-10.0.0.1", "h-b": "H-10.0.0.2"}
    # The group referrer (one field naming BOTH renamed objects) is rewritten to
    # both final names in one edit — chaining is order-independent.
    grp_edit = next(e for e in cs.reference_edits if e.referrer_name == "grp")
    assert grp_edit.after == ["H-10.0.0.1", "H-10.0.0.2"]
    rule_src = next(e for e in cs.reference_edits if e.referrer_name == "r" and e.field == "source")
    assert rule_src.after == ["H-10.0.0.1"]


def test_apply_all_leaves_compliant_object_untouched() -> None:
    snap = Snapshot(
        addresses=[_addr("H-10.0.0.1", "10.0.0.1/32"), _addr("h-b", "10.0.0.2/32")],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_apply_scheme(snap, graph, NamingScheme())
    assert not cs.is_blocked
    renamed = {r.old_name for r in cs.renames}
    assert renamed == {"h-b"}  # the already-compliant host is not touched


def test_apply_all_blocks_when_scheme_name_collides_with_existing() -> None:
    # h-a would become H-10.0.0.1, but that name already exists on another
    # object → a collision attributed to h-a; the whole batch is gated.
    snap = Snapshot(
        addresses=[
            _addr("h-a", "10.0.0.1/32"),
            _addr("H-10.0.0.1", "10.9.9.9/32"),  # occupies the target name
        ],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_apply_scheme(snap, graph, NamingScheme())
    assert cs.is_blocked
    assert any("H-10.0.0.1" in b for b in cs.blockers)
    assert cs.op_count == 0


def test_apply_all_blocks_when_two_objects_share_a_scheme_name() -> None:
    # Two different objects whose values imply the SAME scheme name must not
    # silently overwrite each other — block, attributing both old names.
    snap = Snapshot(
        addresses=[
            _addr("dup-1", "10.0.0.1/32"),
            _addr("dup-2", "10.0.0.1/32"),  # same value → same H-10.0.0.1
        ],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_apply_scheme(snap, graph, NamingScheme())
    assert cs.is_blocked
    assert any("dup-1" in b and "dup-2" in b for b in cs.blockers)
    assert cs.op_count == 0


def test_apply_all_refuses_shadow_inducing_rename() -> None:
    # A shared object whose scheme name is already defined in a child DG would
    # shadow it across the hierarchy — refuse (REQUIRED shadow test).
    snap = Snapshot(
        addresses=[
            _addr("h-a", "10.0.0.1/32", SHARED),
            _addr("H-10.0.0.1", "2.2.2.2/32", Location.dg("DG1")),
        ],
        device_groups=["DG1"],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_apply_scheme(snap, graph, NamingScheme())
    assert cs.is_blocked
    assert any("DG1" in b and "shadow" in b for b in cs.blockers)
    assert cs.op_count == 0


def test_apply_all_scope_filter_limits_to_selected_location() -> None:
    # Only the DG1 object is in scope; the shared one is left alone.
    snap = Snapshot(
        addresses=[
            _addr("h-shared", "10.0.0.1/32", SHARED),
            _addr("h-dg", "10.0.0.2/32", Location.dg("DG1")),
        ],
        device_groups=["DG1"],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_apply_scheme(snap, graph, NamingScheme(), scope=Location.dg("DG1"))
    assert not cs.is_blocked
    # A bulk rename scoped to DG1 renames only DG1-local objects; the inherited
    # `shared` object is deliberately left alone (mutations don't sweep up shared).
    assert {r.old_name for r in cs.renames} == {"h-dg"}


def test_apply_all_repoints_same_name_at_different_locations_independently() -> None:
    # Two objects share the name "myhost" but live at different scopes with
    # different values, so they get different scheme names. A group at each scope
    # references its own "myhost"; each must repoint to the right new name — a
    # location-agnostic rename map would corrupt one of them.
    snap = Snapshot(
        addresses=[
            _addr("myhost", "10.0.0.1/32", SHARED),
            _addr("myhost", "10.0.0.2/32", Location.dg("DG1")),
        ],
        address_groups=[
            AddressGroup(name="g-shared", location=SHARED, static_members=["myhost"]),
            AddressGroup(name="g-dg", location=Location.dg("DG1"), static_members=["myhost"]),
        ],
        device_groups=["DG1"],
    )
    graph = ReferenceGraph.build(snap)
    cs = plan_apply_scheme(snap, graph, NamingScheme())
    assert not cs.is_blocked
    by_referrer = {e.referrer_name: e.after for e in cs.reference_edits}
    # g-dg's "myhost" binds to the DG1 object (10.0.0.2) → H-10.0.0.2; g-shared's
    # binds to the shared object (10.0.0.1) → H-10.0.0.1. No cross-contamination.
    assert by_referrer["g-dg"] == ["H-10.0.0.2"]
    assert by_referrer["g-shared"] == ["H-10.0.0.1"]


def test_apply_all_empty_when_all_compliant() -> None:
    snap = Snapshot(addresses=[_addr("H-10.0.0.1", "10.0.0.1/32")])
    graph = ReferenceGraph.build(snap)
    cs = plan_apply_scheme(snap, graph, NamingScheme())
    assert not cs.is_blocked
    assert cs.is_empty
