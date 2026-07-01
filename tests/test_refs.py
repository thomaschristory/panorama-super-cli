from __future__ import annotations

from psc.core.models import (
    SHARED,
    Address,
    AddressGroup,
    AddressType,
    Location,
    SecurityRule,
    Snapshot,
    Tag,
)
from psc.core.parse import parse_config
from psc.core.refs import ReferenceGraph


def test_where_used_resolves_shared(graph: ReferenceGraph) -> None:
    refs = graph.where_used("address", "web-primary", SHARED)
    referrers = {(r.referrer_kind, r.referrer_name) for r in refs}
    assert ("address-group", "grp-web") in referrers
    assert ("nat-rule", "nat-web") in referrers


def test_dg_local_shadows_shared(snapshot: Snapshot) -> None:
    # edge-rule (DG-EDGE) references local-only which is a DG-local object.
    graph = ReferenceGraph.build(snapshot)
    refs = graph.where_used("address", "local-only", Location.dg("DG-EDGE"))
    assert any(r.referrer_name == "edge-rule" for r in refs)


def test_unused_is_recursive(graph: ReferenceGraph) -> None:
    unused = {t.name for t in graph.unused("address")}
    # rng-db and fqdn-example are referenced by nothing.
    assert {"rng-db", "fqdn-example"} <= unused
    # h-web1 is used (rule + group), so not unused.
    assert "h-web1" not in unused


def test_no_dangling_in_fixture(graph: ReferenceGraph) -> None:
    assert graph.dangling() == []


def test_where_used_spans_every_new_rulebase(all_rb_graph: ReferenceGraph) -> None:
    # a1 is a source in every shared rule; the service s1 in most of them.
    a1_kinds = {r.referrer_kind for r in all_rb_graph.where_used("address", "a1", SHARED)}
    assert a1_kinds >= {
        "pbf-rule",
        "decryption-rule",
        "authentication-rule",
        "qos-rule",
        "application-override-rule",
        "dos-rule",
        "sdwan-rule",
        "tunnel-inspect-rule",
        "network-packet-broker-rule",
    }
    s1_kinds = {r.referrer_kind for r in all_rb_graph.where_used("service", "s1", SHARED)}
    assert "qos-rule" in s1_kinds and "dos-rule" in s1_kinds


def test_unused_seeds_from_all_rulebases(all_rb_graph: ReferenceGraph) -> None:
    unused = {t.name for t in all_rb_graph.unused("address")}
    # Each of these is referenced by exactly one of the new rulebases — none may
    # be reported unused (the reachability-seeding safety fix).
    assert "qos-only" not in unused  # qos-1 destination
    assert "pbf-only" not in unused  # pbf-1 destination
    assert "a2-dup" not in unused  # sdwan-1 destination
    assert "nh-host" not in unused  # pbf-1 nexthop
    # ...but a genuinely unreferenced object still is.
    assert "lonely" in unused
    assert "lonely-svc" in {t.name for t in all_rb_graph.unused("service")}


def test_pbf_nexthop_is_a_tracked_address_reference(all_rb_graph: ReferenceGraph) -> None:
    refs = all_rb_graph.where_used("address", "nh-host", SHARED)
    nexthop = [r for r in refs if r.field == "nexthop"]
    assert nexthop and nexthop[0].referrer_name == "pbf-1"
    assert nexthop[0].referrer_kind == "pbf-rule"


def test_dangling_picks_up_bad_service_in_decryption_rule(all_rb_graph: ReferenceGraph) -> None:
    missing = {(r.referrer_name, r.target_name) for r in all_rb_graph.dangling()}
    assert ("decrypt-1", "bad-svc") in missing


def test_unresolved_pbf_nexthop_is_not_flagged_dangling() -> None:
    # A literal/unknown fqdn nexthop is not necessarily an object — flagging it
    # as dangling would be noise. (A resolving nexthop still shows in where-used.)
    xml = """<config><shared>
      <pre-rulebase><pbf><rules>
        <entry name="p">
          <source><member>any</member></source>
          <action><forward><nexthop><fqdn>gw.example.com</fqdn></nexthop></forward></action>
        </entry>
      </rules></pbf></pre-rulebase>
    </shared></config>"""
    g = ReferenceGraph.build(parse_config(xml))
    assert all(r.field != "nexthop" for r in g.dangling())


def test_nat_rule_tags_are_scanned() -> None:
    # A tag used only on a NAT rule must be reachable in where-used and must not
    # be reported unused — NAT was the lone rulebase whose tags were skipped.
    xml = """<config><shared>
      <tag><entry name="t-nat"/></tag>
      <pre-rulebase><nat><rules>
        <entry name="n">
          <source><member>any</member></source>
          <destination><member>any</member></destination>
          <tag><member>t-nat</member></tag>
        </entry>
      </rules></nat></pre-rulebase>
    </shared></config>"""
    g = ReferenceGraph.build(parse_config(xml))
    used = {(r.referrer_kind, r.referrer_name) for r in g.where_used("tag", "t-nat", SHARED)}
    assert ("nat-rule", "n") in used
    assert "t-nat" not in {t.name for t in g.unused("tag")}


def test_predefined_any_not_dangling() -> None:
    xml = """<config><shared>
      <pre-rulebase><security><rules>
        <entry name="r"><source><member>any</member></source>
          <destination><member>any</member></destination></entry>
      </rules></security></pre-rulebase>
    </shared></config>"""
    g = ReferenceGraph.build(parse_config(xml))
    assert g.dangling() == []


# --- dynamic address-group (DAG) membership (#60) ---------------------------


def _addr(name: str, tags: list[str], loc: Location = SHARED) -> Address:
    return Address(
        name=name, location=loc, type=AddressType.IP_NETMASK, value="10.0.0.1/32", tags=tags
    )


def test_address_matched_only_via_rule_referenced_dag_is_not_unused() -> None:
    # h-prod's only "use" is being tag-matched into a DAG that a rule consumes.
    # Before #60 this read as unused → deleting it silently drops a host.
    snap = Snapshot(
        addresses=[_addr("h-prod", ["prod", "web"]), _addr("h-other", ["dev"])],
        address_groups=[AddressGroup(name="dag-prod-web", dynamic_filter="'prod' and 'web'")],
        security_rules=[SecurityRule(name="r", destination=["dag-prod-web"])],
    )
    g = ReferenceGraph.build(snap)
    unused = {t.name for t in g.unused("address")}
    assert "h-prod" not in unused
    # h-other does not match the filter and nothing else uses it → still unused.
    assert "h-other" in unused


def test_where_used_surfaces_dag_and_rule_path() -> None:
    snap = Snapshot(
        addresses=[_addr("h-prod", ["prod"])],
        address_groups=[AddressGroup(name="dag-prod", dynamic_filter="'prod'")],
        security_rules=[SecurityRule(name="r", destination=["dag-prod"])],
    )
    g = ReferenceGraph.build(snap)
    refs = g.where_used("address", "h-prod", SHARED)
    # the DAG appears as an (indirect) referrer of the matched address...
    dag = [r for r in refs if r.referrer_kind == "address-group" and r.field == "dynamic"]
    assert dag and dag[0].referrer_name == "dag-prod"
    # ...and the rule→DAG edge is reachable from where-used on the DAG itself.
    dag_refs = {r.referrer_name for r in g.where_used("address-group", "dag-prod", SHARED)}
    assert "r" in dag_refs


def test_dag_membership_respects_scope() -> None:
    # A DAG in DG-A may match addresses in DG-A and its ancestors (shared), but
    # not a sibling device-group's objects.
    snap = Snapshot(
        addresses=[
            _addr("a-prod", ["prod"], Location.dg("DG-A")),
            _addr("b-prod", ["prod"], Location.dg("DG-B")),
            _addr("shared-prod", ["prod"], SHARED),
        ],
        address_groups=[
            AddressGroup(name="dag", location=Location.dg("DG-A"), dynamic_filter="'prod'")
        ],
        security_rules=[SecurityRule(name="r", location=Location.dg("DG-A"), destination=["dag"])],
        device_groups=["DG-A", "DG-B"],
    )
    g = ReferenceGraph.build(snap)
    unused = {(t.location.name, t.name) for t in g.unused("address")}
    assert ("DG-A", "a-prod") not in unused  # in DAG's own scope
    assert ("shared", "shared-prod") not in unused  # inherited ancestor scope
    assert ("DG-B", "b-prod") in unused  # sibling DG, out of scope


def test_address_in_unreferenced_dag_is_still_unused() -> None:
    # The DAG matches h-prod, but no rule consumes the DAG → nothing reaches the
    # address; it must still be reported unused.
    snap = Snapshot(
        addresses=[_addr("h-prod", ["prod"])],
        address_groups=[AddressGroup(name="dag-prod", dynamic_filter="'prod'")],
    )
    g = ReferenceGraph.build(snap)
    assert "h-prod" in {t.name for t in g.unused("address")}


def test_unparseable_dag_filter_warns_and_matches_nothing() -> None:
    # A malformed filter must never crash the audit; psc declines to guess its
    # membership (match-nothing) and records a warning naming the DAG (#60 Q2).
    snap = Snapshot(
        addresses=[_addr("h-prod", ["prod"])],
        address_groups=[AddressGroup(name="dag-bad", dynamic_filter="'prod' and")],
        security_rules=[SecurityRule(name="r", destination=["dag-bad"])],
    )
    g = ReferenceGraph.build(snap)
    assert "h-prod" in {t.name for t in g.unused("address")}
    assert any("dag-bad" in w for w in g.warnings)


def test_disabled_only_address_used_by_default_unused_with_flag() -> None:
    # An address whose sole reference is a DISABLED rule counts as used today
    # (default), but surfaces under ignore_disabled=True (#9).
    snap = Snapshot(
        addresses=[Address(name="h-off", value="10.0.0.1/32", type=AddressType.IP_NETMASK)],
        security_rules=[SecurityRule(name="r", destination=["h-off"], disabled=True)],
    )
    g = ReferenceGraph.build(snap)
    assert "h-off" not in {t.name for t in g.unused("address")}
    assert "h-off" in {t.name for t in g.unused("address", ignore_disabled=True)}


def test_tag_on_disabled_rule_stays_used_under_flag() -> None:
    # Intentional asymmetry (#9): --ignore-disabled gates only object reachability
    # through rules. A tag carried by a disabled rule is still a real config
    # setting on that rule, so it stays "used" even under the flag.
    snap = Snapshot(
        tags=[Tag(name="t-off")],
        security_rules=[SecurityRule(name="r", tags=["t-off"], disabled=True)],
    )
    g = ReferenceGraph.build(snap)
    assert "t-off" not in {t.name for t in g.unused("tag")}
    assert "t-off" not in {t.name for t in g.unused("tag", ignore_disabled=True)}


def test_enabled_rule_keeps_address_used_under_flag() -> None:
    snap = Snapshot(
        addresses=[Address(name="h-on", value="10.0.0.2/32", type=AddressType.IP_NETMASK)],
        security_rules=[SecurityRule(name="r", destination=["h-on"], disabled=False)],
    )
    g = ReferenceGraph.build(snap)
    assert "h-on" not in {t.name for t in g.unused("address", ignore_disabled=True)}


def test_address_in_enabled_and_disabled_rules_stays_used_under_flag() -> None:
    snap = Snapshot(
        addresses=[Address(name="h", value="10.0.0.3/32", type=AddressType.IP_NETMASK)],
        security_rules=[
            SecurityRule(name="off", destination=["h"], disabled=True),
            SecurityRule(name="on", destination=["h"], disabled=False),
        ],
    )
    g = ReferenceGraph.build(snap)
    assert "h" not in {t.name for t in g.unused("address", ignore_disabled=True)}


def test_transitive_group_reachable_only_via_disabled_rule_surfaces() -> None:
    # An address is a member of a group that is referenced ONLY by a disabled
    # rule: it is effectively unused once the rule goes (transitive #9).
    snap = Snapshot(
        addresses=[Address(name="mem", value="10.0.0.4/32", type=AddressType.IP_NETMASK)],
        address_groups=[AddressGroup(name="grp", static_members=["mem"])],
        security_rules=[SecurityRule(name="r", destination=["grp"], disabled=True)],
    )
    g = ReferenceGraph.build(snap)
    assert "mem" not in {t.name for t in g.unused("address")}
    unused_flag = {t.name for t in g.unused("address", ignore_disabled=True)}
    assert "mem" in unused_flag
    assert "grp" in {t.name for t in g.unused("address-group", ignore_disabled=True)}


def test_mixed_reachability_disabled_and_enabled_groups() -> None:
    # grp-on is reached by an enabled rule; grp-off only by a disabled rule.
    # Their members follow suit under the flag.
    snap = Snapshot(
        addresses=[
            Address(name="a-on", value="10.0.0.5/32", type=AddressType.IP_NETMASK),
            Address(name="a-off", value="10.0.0.6/32", type=AddressType.IP_NETMASK),
        ],
        address_groups=[
            AddressGroup(name="grp-on", static_members=["a-on"]),
            AddressGroup(name="grp-off", static_members=["a-off"]),
        ],
        security_rules=[
            SecurityRule(name="on", destination=["grp-on"], disabled=False),
            SecurityRule(name="off", destination=["grp-off"], disabled=True),
        ],
    )
    g = ReferenceGraph.build(snap)
    unused_flag = {t.name for t in g.unused("address", ignore_disabled=True)}
    assert "a-on" not in unused_flag
    assert "a-off" in unused_flag


def test_unused_tags_shadowed_copy_bound_by_filter_is_used_shared_is_unused() -> None:
    # A filter in DG-A referencing 'prod' binds to prod@DG-A (closest). prod@shared
    # is shadowed and — if nothing else references it — must be reported UNUSED.
    snap = Snapshot(
        tags=[Tag(name="prod", location=SHARED), Tag(name="prod", location=Location.dg("DG-A"))],
        address_groups=[
            AddressGroup(name="dag", location=Location.dg("DG-A"), dynamic_filter="'prod'")
        ],
        device_groups=["DG-A"],
    )
    g = ReferenceGraph.build(snap)
    unused = {(t.location.name, t.name) for t in g.unused("tag")}
    assert ("DG-A", "prod") not in unused  # the copy the filter actually binds to
    assert ("shared", "prod") in unused  # shadowed, referenced by nothing else


def test_unused_tags_sibling_dg_same_name_is_unused() -> None:
    # prod@DG-B exists, but the only filter referencing 'prod' is in DG-A, which
    # cannot see DG-B. prod@DG-B is out of scope → UNUSED.
    snap = Snapshot(
        tags=[
            Tag(name="prod", location=Location.dg("DG-A")),
            Tag(name="prod", location=Location.dg("DG-B")),
        ],
        address_groups=[
            AddressGroup(name="dag", location=Location.dg("DG-A"), dynamic_filter="'prod'")
        ],
        device_groups=["DG-A", "DG-B"],
    )
    g = ReferenceGraph.build(snap)
    unused = {(t.location.name, t.name) for t in g.unused("tag")}
    assert ("DG-A", "prod") not in unused  # the copy the filter binds to
    assert ("DG-B", "prod") in unused  # sibling DG, out of scope


def test_unused_tags_filter_binds_inherited_shared_tag() -> None:
    # A filter in DG-A referencing 'prod' where only prod@shared exists; shared is
    # visible via inheritance, so it is USED.
    snap = Snapshot(
        tags=[Tag(name="prod", location=SHARED)],
        address_groups=[
            AddressGroup(name="dag", location=Location.dg("DG-A"), dynamic_filter="'prod'")
        ],
        device_groups=["DG-A"],
    )
    g = ReferenceGraph.build(snap)
    unused = {(t.location.name, t.name) for t in g.unused("tag")}
    assert ("shared", "prod") not in unused


def test_unused_tags_filter_token_resolving_to_nothing_marks_nothing() -> None:
    # A filter referencing a tag that resolves to no visible tag marks nothing and
    # must not crash; an unrelated shared tag is still visible/used.
    snap = Snapshot(
        tags=[Tag(name="web", location=SHARED)],
        address_groups=[
            AddressGroup(
                name="dag", location=Location.dg("DG-A"), dynamic_filter="'web' and 'missing'"
            )
        ],
        device_groups=["DG-A"],
    )
    g = ReferenceGraph.build(snap)
    unused = {(t.location.name, t.name) for t in g.unused("tag")}
    assert ("shared", "web") not in unused  # resolved via inheritance
