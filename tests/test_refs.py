from __future__ import annotations

from psc.core.livedag import LiveDagMembership, RegisteredIps, build_membership
from psc.core.models import (
    SHARED,
    Address,
    AddressGroup,
    AddressType,
    Location,
    NatRule,
    PolicyRule,
    Rulebase,
    RuleType,
    SecurityRule,
    Service,
    ServiceGroup,
    Snapshot,
    Tag,
)
from psc.core.parse import parse_config
from psc.core.refs import ReferenceGraph, Target, fall_through_note, reference_breaks

# --- tags_for: the tag column behind the unused/where-used listings (#180) ---


def test_tags_for_returns_object_tags_across_kinds() -> None:
    # An address pulled into a DAG only via an externally-registered IP looks
    # unused here, but its tags are what an operator must see before deleting.
    snap = Snapshot(
        addresses=[_addr("h-prod", ["prod", "web"])],
        address_groups=[AddressGroup(name="ag", static_members=[], tags=["group-tag"])],
        services=[Service(name="svc", protocol="tcp", destination_port="443", tags=["svc-tag"])],
        service_groups=[ServiceGroup(name="sg", members=[], tags=["sg-tag"])],
    )
    g = ReferenceGraph.build(snap)
    tags = {t: g.tags_for(t) for t in g.unused("address") + g.unused("address-group")}
    tags |= {t: g.tags_for(t) for t in g.unused("service") + g.unused("service-group")}
    by_name = {t.name: v for t, v in tags.items()}
    assert by_name["h-prod"] == ["prod", "web"]
    assert by_name["ag"] == ["group-tag"]
    assert by_name["svc"] == ["svc-tag"]
    assert by_name["sg"] == ["sg-tag"]


def test_tags_for_untagged_object_is_empty() -> None:
    snap = Snapshot(addresses=[_addr("bare", [])])
    g = ReferenceGraph.build(snap)
    (target,) = g.unused("address")
    assert g.tags_for(target) == []


def test_tags_for_tag_kind_is_empty() -> None:
    # Tags don't carry tags; the column is blank for the tag kind, not an error.
    snap = Snapshot(tags=[Tag(name="orphan")])
    g = ReferenceGraph.build(snap)
    (target,) = g.unused("tag")
    assert g.tags_for(target) == []


def test_tags_for_scopes_by_location() -> None:
    # Same name in two device-groups must not cross-contaminate tags.
    snap = Snapshot(
        addresses=[
            _addr("dup", ["a-tag"], Location.dg("DG-A")),
            _addr("dup", ["b-tag"], Location.dg("DG-B")),
        ],
        device_groups=["DG-A", "DG-B"],
    )
    g = ReferenceGraph.build(snap)
    by_loc = {t.location.name: g.tags_for(t) for t in g.unused("address")}
    assert by_loc["DG-A"] == ["a-tag"]
    assert by_loc["DG-B"] == ["b-tag"]


# --- Reference.tags: the referrer's tags in the where-used listing (#184) ---


def test_reference_tags_carry_security_rule_tags() -> None:
    # The trap of #184: `tags_for` indexes objects only, so it answers [] for a
    # rule. A rule referrer is the most common row in a where-used listing.
    snap = Snapshot(
        addresses=[_addr("a", [])],
        security_rules=[SecurityRule(name="r1", destination=["a"], tags=["t-rule"])],
    )
    g = ReferenceGraph.build(snap)
    (ref,) = g.where_used("address", "a", SHARED)
    assert ref.referrer_kind == "security-rule"
    assert ref.tags == ("t-rule",)


def test_reference_tags_carry_nat_rule_tags() -> None:
    snap = Snapshot(
        addresses=[_addr("a", [])],
        nat_rules=[NatRule(name="n1", destination=["a"], tags=["t-nat"])],
    )
    g = ReferenceGraph.build(snap)
    (ref,) = g.where_used("address", "a", SHARED)
    assert ref.referrer_kind == "nat-rule"
    assert ref.tags == ("t-nat",)


def test_reference_tags_carry_policy_rule_tags() -> None:
    snap = Snapshot(
        addresses=[_addr("a", [])],
        policy_rules=[
            PolicyRule(name="q1", rule_type=RuleType.QOS, destination=["a"], tags=["t-qos"])
        ],
    )
    g = ReferenceGraph.build(snap)
    (ref,) = g.where_used("address", "a", SHARED)
    assert ref.referrer_kind == "qos-rule"
    assert ref.tags == ("t-qos",)


def test_reference_tags_separate_pre_and_post_rules_of_one_name() -> None:
    # Safety-critical: one location can hold a `pre` rule and a `post` rule with
    # one name. A tag index keyed on (kind, name, location) merges the two and
    # reports the wrong owner for a delete.
    snap = Snapshot(
        addresses=[_addr("a", [])],
        security_rules=[
            SecurityRule(name="r", rulebase=Rulebase.PRE, destination=["a"], tags=["pre-tag"]),
            SecurityRule(name="r", rulebase=Rulebase.POST, destination=["a"], tags=["post-tag"]),
        ],
    )
    g = ReferenceGraph.build(snap)
    by_rulebase = {ref.rulebase: ref.tags for ref in g.where_used("address", "a", SHARED)}
    assert by_rulebase[Rulebase.PRE] == ("pre-tag",)
    assert by_rulebase[Rulebase.POST] == ("post-tag",)


def test_reference_tags_scope_rules_by_location() -> None:
    # Same-named rules in two device groups must not cross-contaminate tags.
    snap = Snapshot(
        device_groups=["DG-A", "DG-B"],
        addresses=[_addr("a", [])],
        security_rules=[
            SecurityRule(name="r", location=Location.dg("DG-A"), destination=["a"], tags=["a-tag"]),
            SecurityRule(name="r", location=Location.dg("DG-B"), destination=["a"], tags=["b-tag"]),
        ],
    )
    g = ReferenceGraph.build(snap)
    by_loc = {ref.referrer_location.name: ref.tags for ref in g.where_used("address", "a", SHARED)}
    assert by_loc["DG-A"] == ("a-tag",)
    assert by_loc["DG-B"] == ("b-tag",)


def test_reference_tags_carry_group_referrer_tags() -> None:
    # An object referrer keeps the same meaning: the row shows the group's tags.
    snap = Snapshot(
        addresses=[_addr("a", [])],
        address_groups=[AddressGroup(name="ag", static_members=["a"], tags=["grp-tag"])],
    )
    g = ReferenceGraph.build(snap)
    (ref,) = g.where_used("address", "a", SHARED)
    assert ref.referrer_kind == "address-group"
    assert ref.field == "static"
    assert ref.tags == ("grp-tag",)


def test_reference_tags_carry_service_group_referrer_tags() -> None:
    snap = Snapshot(
        services=[Service(name="s", protocol="tcp", destination_port="443")],
        service_groups=[ServiceGroup(name="sg", members=["s"], tags=["sg-tag"])],
    )
    g = ReferenceGraph.build(snap)
    (ref,) = g.where_used("service", "s", SHARED)
    assert ref.referrer_kind == "service-group"
    assert ref.tags == ("sg-tag",)


def test_reference_tags_empty_for_untagged_referrer() -> None:
    snap = Snapshot(
        addresses=[_addr("a", [])],
        security_rules=[SecurityRule(name="r", destination=["a"])],
    )
    g = ReferenceGraph.build(snap)
    (ref,) = g.where_used("address", "a", SHARED)
    assert ref.tags == ()


def test_reference_tags_keep_the_disabled_rule_tags() -> None:
    snap = Snapshot(
        addresses=[_addr("a", [])],
        security_rules=[SecurityRule(name="r", destination=["a"], disabled=True, tags=["t-off"])],
    )
    g = ReferenceGraph.build(snap)
    (ref,) = g.where_used("address", "a", SHARED)
    assert ref.referrer_disabled is True
    assert ref.tags == ("t-off",)


def test_reference_tags_for_dag_referrer_are_the_group_tags() -> None:
    # A `dynamic` row comes from a dynamic address group that matched the object
    # by tag. The column shows the tags of that group. It does not show the
    # filter tags that caused the match.
    snap = Snapshot(
        addresses=[_addr("h", ["prod"])],
        address_groups=[AddressGroup(name="dag-prod", dynamic_filter="'prod'", tags=["dag-own"])],
    )
    g = ReferenceGraph.build(snap)
    (ref,) = [r for r in g.where_used("address", "h", SHARED) if r.field == "dynamic"]
    assert ref.tags == ("dag-own",)
    assert "prod" not in ref.tags


def test_reference_tags_on_a_traced_tag_include_that_tag() -> None:
    # `refs used` on a tag object lists the objects and the rules that carry it,
    # so the traced tag is in the referrer's own tag list. That is correct.
    snap = Snapshot(
        tags=[Tag(name="t1")],
        addresses=[_addr("a", ["t1"])],
        security_rules=[SecurityRule(name="r", tags=["t1"])],
    )
    g = ReferenceGraph.build(snap)
    by_kind = {ref.referrer_kind: ref.tags for ref in g.where_used("tag", "t1", SHARED)}
    assert by_kind["address"] == ("t1",)
    assert by_kind["security-rule"] == ("t1",)


def test_reference_tags_survive_a_device_group_shadow() -> None:
    # A child device group holds its own `a`, which shadows the shared `a`. Each
    # rule row must keep the tags of the rule that the row comes from.
    snap = Snapshot(
        device_groups=["parent", "child"],
        device_group_parents={"child": "parent"},
        addresses=[_addr("a", []), _addr("a", [], Location.dg("child"))],
        security_rules=[
            SecurityRule(
                name="r-child",
                location=Location.dg("child"),
                destination=["a"],
                tags=["child-tag"],
            ),
            SecurityRule(name="r-shared", destination=["a"], tags=["shared-tag"]),
        ],
    )
    g = ReferenceGraph.build(snap)
    (local,) = g.where_used("address", "a", Location.dg("child"))
    assert local.referrer_name == "r-child"
    assert local.tags == ("child-tag",)
    (shared,) = g.where_used("address", "a", SHARED)
    assert shared.referrer_name == "r-shared"
    assert shared.tags == ("shared-tag",)


def test_every_referrer_field_carries_the_referrer_tags() -> None:
    """Pin `Reference.tags` on every field of the walk (#184).

    `_walk` wires the referrer tags at 16 call sites. A miss on one site drops
    the tags of a whole field, and no other test sees it. One tagged referrer of
    each kind covers every site in one table.
    """
    snap = Snapshot(
        tags=[Tag(name=n) for n in ("tg-ag", "tg-addr", "tg-svc", "tg-sec", "tg-nat", "tg-pol")],
        addresses=[_addr("a1", ["tg-addr"]), _addr("nh", [])],
        address_groups=[AddressGroup(name="ag", static_members=["a1"], tags=["tg-ag"])],
        services=[Service(name="s1", protocol="tcp", destination_port="443", tags=["tg-svc"])],
        service_groups=[ServiceGroup(name="sg", members=["s1"], tags=["tg-sg"])],
        security_rules=[
            SecurityRule(
                name="sec",
                source=["a1"],
                destination=["a1"],
                service=["s1"],
                tags=["tg-sec"],
            )
        ],
        nat_rules=[
            NatRule(
                name="nat",
                source=["a1"],
                destination_translation="a1",
                source_translation=["a1"],
                service="s1",
                tags=["tg-nat"],
            )
        ],
        policy_rules=[
            PolicyRule(
                name="pbf",
                rule_type=RuleType.PBF,
                source=["a1"],
                destination=["a1"],
                service=["s1"],
                nexthop="nh",
                tags=["tg-pol"],
            )
        ],
    )
    g = ReferenceGraph.build(snap)
    by_field = {(r.referrer_name, r.field): r.tags for r in g.references}
    # A duplicate key would hide a site behind another row.
    assert len(by_field) == len(g.references)
    assert by_field == {
        ("ag", "static"): ("tg-ag",),
        ("ag", "tag"): ("tg-ag",),
        ("sg", "members"): ("tg-sg",),
        ("a1", "tag"): ("tg-addr",),
        ("s1", "tag"): ("tg-svc",),
        ("sec", "source"): ("tg-sec",),
        ("sec", "destination"): ("tg-sec",),
        ("sec", "service"): ("tg-sec",),
        ("sec", "tag"): ("tg-sec",),
        ("nat", "source"): ("tg-nat",),
        ("nat", "source-translation"): ("tg-nat",),
        ("nat", "destination-translation"): ("tg-nat",),
        ("nat", "service"): ("tg-nat",),
        ("nat", "tag"): ("tg-nat",),
        ("pbf", "source"): ("tg-pol",),
        ("pbf", "destination"): ("tg-pol",),
        ("pbf", "service"): ("tg-pol",),
        ("pbf", "tag"): ("tg-pol",),
        ("pbf", "nexthop"): ("tg-pol",),
    }


def test_reference_stays_hashable_with_tags() -> None:
    # `Reference` is a frozen dataclass, so it has a generated __hash__. A list
    # field makes every hash raise; the tags field must stay a tuple.
    snap = Snapshot(
        addresses=[_addr("a", [])],
        security_rules=[SecurityRule(name="r", destination=["a"], tags=["t"])],
    )
    g = ReferenceGraph.build(snap)
    assert len({*g.where_used("address", "a", SHARED)}) == 1


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


def _addr(
    name: str, tags: list[str], loc: Location = SHARED, *, value: str = "10.0.0.1/32"
) -> Address:
    return Address(name=name, location=loc, type=AddressType.IP_NETMASK, value=value, tags=tags)


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


# --- live DAG membership from registered IPs (#183) --------------------------


def _live(values: dict[str, set[str]]) -> LiveDagMembership:
    return build_membership(
        {"001": RegisteredIps(by_value={v: frozenset(t) for v, t in values.items()})}
    )


def test_live_registered_tag_keeps_a_dag_matched_address_off_unused() -> None:
    # SAFETY: the first acceptance criterion of #183. The address carries no
    # config tag, so only the live registration puts it in the DAG.
    snap = Snapshot(
        addresses=[_addr("h-vm", [], value="10.1.1.5")],
        address_groups=[AddressGroup(name="dag-prod", dynamic_filter="'prod'")],
        security_rules=[SecurityRule(name="r", destination=["dag-prod"])],
    )
    assert "h-vm" in {t.name for t in ReferenceGraph.build(snap).unused("address")}
    live = ReferenceGraph.build(snap, live_dag=_live({"10.1.1.5": {"prod"}}))
    assert "h-vm" not in {t.name for t in live.unused("address")}


def test_live_enrichment_never_removes_a_config_matched_member() -> None:
    # CRITICAL: a DAG filter can negate a tag. A live tag must never take an
    # address out of a DAG, because that would make a new delete candidate.
    snap = Snapshot(
        addresses=[_addr("h-vm", ["prod"], value="10.1.1.5")],
        address_groups=[
            AddressGroup(name="dag-prod", dynamic_filter="'prod' and not 'quarantine'")
        ],
        security_rules=[SecurityRule(name="r", destination=["dag-prod"])],
    )
    for graph in (
        ReferenceGraph.build(snap),
        ReferenceGraph.build(snap, live_dag=_live({"10.1.1.5": {"quarantine"}})),
    ):
        assert "h-vm" not in {t.name for t in graph.unused("address")}


def test_live_enrichment_never_removes_a_member_of_a_bare_negation_filter() -> None:
    # The same rule for `not 'x'` alone: the address matches the filter with its
    # config tags, and the live tag must not cancel the match.
    snap = Snapshot(
        addresses=[_addr("h-vm", [], value="10.1.1.5")],
        address_groups=[AddressGroup(name="dag-clean", dynamic_filter="not 'quarantine'")],
        security_rules=[SecurityRule(name="r", destination=["dag-clean"])],
    )
    live = ReferenceGraph.build(snap, live_dag=_live({"10.1.1.5": {"quarantine"}}))
    assert "h-vm" not in {t.name for t in live.unused("address")}


def test_a_live_derived_edge_carries_its_own_field_name() -> None:
    # A registered IP is registered against an IP, not against an address
    # object. A rename cannot repoint such an edge, so the row must say so.
    snap = Snapshot(
        addresses=[_addr("h-vm", [], value="10.1.1.5"), _addr("h-cfg", ["prod"])],
        address_groups=[AddressGroup(name="dag-prod", dynamic_filter="'prod'")],
        security_rules=[SecurityRule(name="r", destination=["dag-prod"])],
    )
    g = ReferenceGraph.build(snap, live_dag=_live({"10.1.1.5": {"prod"}}))
    (live_ref,) = g.where_used("address", "h-vm", SHARED)
    assert live_ref.referrer_kind == "address-group"
    assert live_ref.referrer_name == "dag-prod"
    assert live_ref.field == "dynamic-registered"
    # A config-tag match keeps the historical `dynamic` field.
    (cfg_ref,) = g.where_used("address", "h-cfg", SHARED)
    assert cfg_ref.field == "dynamic"


def test_a_live_matched_address_is_a_dag_member() -> None:
    snap = Snapshot(
        addresses=[_addr("h-vm", [], value="10.1.1.5")],
        address_groups=[AddressGroup(name="dag-prod", dynamic_filter="'prod'")],
    )
    g = ReferenceGraph.build(snap, live_dag=_live({"10.1.1.5": {"prod"}}))
    members = g.dag_members(Target("address-group", "dag-prod", SHARED))
    assert [m.name for m in members] == ["h-vm"]


def test_live_tags_do_not_widen_the_config_scope_chain() -> None:
    # SAFETY: the registered map is estate-wide, but the DAG still matches only
    # the addresses its own device-group chain can see.
    snap = Snapshot(
        addresses=[
            _addr("a-vm", [], Location.dg("DG-A"), value="10.1.1.5"),
            _addr("b-vm", [], Location.dg("DG-B"), value="10.1.1.6"),
        ],
        address_groups=[
            AddressGroup(name="dag", location=Location.dg("DG-A"), dynamic_filter="'prod'")
        ],
        security_rules=[SecurityRule(name="r", location=Location.dg("DG-A"), destination=["dag"])],
        device_groups=["DG-A", "DG-B"],
    )
    live = _live({"10.1.1.5": {"prod"}, "10.1.1.6": {"prod"}})
    g = ReferenceGraph.build(snap, live_dag=live)
    unused = {(t.location.name, t.name) for t in g.unused("address")}
    assert ("DG-A", "a-vm") not in unused
    assert ("DG-B", "b-vm") in unused  # a sibling device group stays out of scope


def test_a_second_firewall_never_cancels_the_match_of_the_first() -> None:
    # CRITICAL (#183): fwA registers 10.1.1.5 with 'prod'. fwB registers the
    # same IP with 'quarantine'. The DAG of fwA really holds the address. One
    # joined tag set would drop it, and the address would become a delete
    # candidate. psc evaluates the filter per firewall, so the match holds.
    snap = Snapshot(
        addresses=[_addr("h-vm", [], value="10.1.1.5")],
        address_groups=[
            AddressGroup(name="dag-prod", dynamic_filter="'prod' and not 'quarantine'")
        ],
        security_rules=[SecurityRule(name="r", destination=["dag-prod"])],
    )
    live = build_membership(
        {
            "fwA": RegisteredIps(by_value={"10.1.1.5": frozenset({"prod"})}),
            "fwB": RegisteredIps(by_value={"10.1.1.5": frozenset({"quarantine"})}),
        }
    )
    g = ReferenceGraph.build(snap, live_dag=live)
    assert "h-vm" not in {t.name for t in g.unused("address")}
    # The order of the firewalls does not change the answer.
    flipped = build_membership(
        {
            "fwB": RegisteredIps(by_value={"10.1.1.5": frozenset({"quarantine"})}),
            "fwA": RegisteredIps(by_value={"10.1.1.5": frozenset({"prod"})}),
        }
    )
    g_flipped = ReferenceGraph.build(snap, live_dag=flipped)
    assert "h-vm" not in {t.name for t in g_flipped.unused("address")}


def test_a_shared_dag_holds_a_live_registered_device_group_address() -> None:
    # SAFETY (#183): PAN-OS pushes a shared DAG to every device group, and a
    # registered IP has no device group. The live match therefore widens to the
    # whole snapshot for a shared DAG. Without this psc lists a live host as a
    # delete candidate, under a caveat that claims registered IPs ARE scanned.
    snap = Snapshot(
        addresses=[_addr("h-vm", [], Location.dg("DG-A"), value="10.1.1.5")],
        address_groups=[AddressGroup(name="dag-prod", dynamic_filter="'prod'")],
        security_rules=[SecurityRule(name="r", destination=["dag-prod"])],
        device_groups=["DG-A"],
    )
    assert "h-vm" in {t.name for t in ReferenceGraph.build(snap).unused("address")}
    g = ReferenceGraph.build(snap, live_dag=_live({"10.1.1.5": {"prod"}}))
    assert "h-vm" not in {t.name for t in g.unused("address")}
    (ref,) = g.where_used("address", "h-vm", Location.dg("DG-A"))
    assert ref.referrer_name == "dag-prod"
    assert ref.field == "dynamic-registered"


def test_a_shared_dag_keeps_the_config_tag_scope_rule() -> None:
    # The widening is for the live match only. A config tag of a device-group
    # address still does not join a shared DAG, so no offline result moves.
    snap = Snapshot(
        addresses=[_addr("h-vm", ["prod"], Location.dg("DG-A"), value="10.1.1.5")],
        address_groups=[AddressGroup(name="dag-prod", dynamic_filter="'prod'")],
        security_rules=[SecurityRule(name="r", destination=["dag-prod"])],
        device_groups=["DG-A"],
    )
    for g in (ReferenceGraph.build(snap), ReferenceGraph.build(snap, live_dag=_live({}))):
        assert "h-vm" in {t.name for t in g.unused("address")}
        assert g.dag_members(Target("address-group", "dag-prod", SHARED)) == []


def test_a_device_group_dag_keeps_its_chain_under_live_data() -> None:
    # The widening is for a shared DAG only. A DAG of DG-A does not reach a
    # sibling device group, live data or not.
    snap = Snapshot(
        addresses=[_addr("b-vm", [], Location.dg("DG-B"), value="10.1.1.5")],
        address_groups=[
            AddressGroup(name="dag", location=Location.dg("DG-A"), dynamic_filter="'prod'")
        ],
        security_rules=[SecurityRule(name="r", location=Location.dg("DG-A"), destination=["dag"])],
        device_groups=["DG-A", "DG-B"],
    )
    g = ReferenceGraph.build(snap, live_dag=_live({"10.1.1.5": {"prod"}}))
    assert "b-vm" in {t.name for t in g.unused("address")}


def test_a_shared_dag_takes_a_matched_address_once() -> None:
    # The live pass must not add a second edge for an address the config pass
    # already took.
    snap = Snapshot(
        addresses=[_addr("h-vm", [], value="10.1.1.5")],
        address_groups=[AddressGroup(name="dag-prod", dynamic_filter="'prod'")],
        security_rules=[SecurityRule(name="r", destination=["dag-prod"])],
    )
    g = ReferenceGraph.build(snap, live_dag=_live({"10.1.1.5": {"prod"}}))
    assert len(g.where_used("address", "h-vm", SHARED)) == 1
    assert len(g.dag_members(Target("address-group", "dag-prod", SHARED))) == 1


def test_live_tags_do_not_change_the_tags_column() -> None:
    # The output contract stays additive: `tags` reports config tags only.
    snap = Snapshot(addresses=[_addr("h-vm", [], value="10.1.1.5")])
    g = ReferenceGraph.build(snap, live_dag=_live({"10.1.1.5": {"prod"}}))
    assert g.tags_for(Target("address", "h-vm", SHARED)) == []


def test_live_enrichment_adds_no_graph_warning() -> None:
    # Coverage counts belong to the CLI. `warnings` stays the channel for a
    # coverage gap that the operator must act on.
    snap = Snapshot(addresses=[_addr("h-vm", [], value="10.1.1.5")])
    g = ReferenceGraph.build(snap, live_dag=_live({"10.9.9.9": {"prod"}}))
    assert g.warnings == []


def test_a_graph_without_live_data_is_unchanged(snapshot: Snapshot) -> None:
    # Regression guard for the offline contract.
    plain = ReferenceGraph.build(snapshot)
    explicit = ReferenceGraph.build(snapshot, live_dag=None)
    assert plain.unused("address") == explicit.unused("address")
    assert plain.references == explicit.references
    assert plain.warnings == explicit.warnings


def test_an_unparseable_dag_filter_stays_a_match_nothing_under_live_data() -> None:
    snap = Snapshot(
        addresses=[_addr("h-vm", [], value="10.1.1.5")],
        address_groups=[AddressGroup(name="dag-bad", dynamic_filter="'prod' and")],
        security_rules=[SecurityRule(name="r", destination=["dag-bad"])],
    )
    g = ReferenceGraph.build(snap, live_dag=_live({"10.1.1.5": {"prod"}}))
    assert "h-vm" in {t.name for t in g.unused("address")}
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


# --- the shared shadow gate (#187) ---------------------------------------


def _shadow_graph() -> ReferenceGraph:
    """`web` defined in shared AND in dg-edge, with a dg-local group."""
    dg = Location.dg("dg-edge")
    snap = Snapshot(
        addresses=[
            Address(name="web", location=SHARED, type=AddressType.IP_NETMASK, value="10.0.0.1/32"),
            Address(name="web", location=dg, type=AddressType.IP_NETMASK, value="10.9.9.9/32"),
        ],
        address_groups=[AddressGroup(name="g", location=dg, static_members=["web"])],
        device_groups=["dg-edge"],
    )
    return ReferenceGraph.build(snap)


def test_reference_breaks_is_false_when_the_name_falls_through() -> None:
    g = _shadow_graph()
    assert (
        reference_breaks(
            g, "address", Location.dg("dg-edge"), "web", {("address", "web", "dg-edge")}
        )
        is False
    )


def test_reference_breaks_is_true_when_every_definition_goes() -> None:
    g = _shadow_graph()
    delete_set = {("address", "web", "dg-edge"), ("address", "web", "shared")}
    assert reference_breaks(g, "address", Location.dg("dg-edge"), "web", delete_set) is True


def test_reference_breaks_is_false_for_a_name_that_already_dangles() -> None:
    g = _shadow_graph()
    assert reference_breaks(g, "address", Location.dg("dg-edge"), "ghost", set()) is False


def test_reference_breaks_keeps_a_same_named_object_of_another_kind() -> None:
    # The kind in the ignored triple is load-bearing: an address delete must not
    # hide a same-named address-group that the plan keeps.
    dg = Location.dg("dg-edge")
    snap = Snapshot(
        addresses=[
            Address(name="web", location=SHARED, type=AddressType.IP_NETMASK, value="10.0.0.1/32")
        ],
        address_groups=[AddressGroup(name="web", location=dg, static_members=[])],
        device_groups=["dg-edge"],
    )
    g = ReferenceGraph.build(snap)
    assert reference_breaks(g, "address", dg, "web", {("address", "web", "dg-edge")}) is False


def test_fall_through_note_names_the_survivor_and_its_value() -> None:
    g = _shadow_graph()
    (ref,) = [r for r in g.references if r.referrer_name == "g"]
    note = fall_through_note(g, ref, {("address", "web", "dg-edge")})
    assert note is not None
    assert "address-group 'g'@dg-edge static" in note
    assert "'web'" in note
    assert "address 'web'@shared (10.0.0.1/32)" in note


def test_fall_through_note_is_none_when_the_reference_really_breaks() -> None:
    g = _shadow_graph()
    (ref,) = [r for r in g.references if r.referrer_name == "g"]
    delete_set = {("address", "web", "dg-edge"), ("address", "web", "shared")}
    assert fall_through_note(g, ref, delete_set) is None


def test_fall_through_note_is_none_when_the_survivor_is_the_same_object() -> None:
    # Deleting the shared `web` changes nothing for a referrer inside dg-edge:
    # the name already binds to the device-group object.
    g = _shadow_graph()
    (ref,) = [r for r in g.references if r.referrer_name == "g"]
    assert fall_through_note(g, ref, {("address", "web", "shared")}) is None


def test_fall_through_note_reads_the_survivors_own_value() -> None:
    """The lookup matches on location, not on name alone.

    The snapshot lists the device-group object FIRST. A name-only lookup then
    reports the value of the object the plan deletes.
    """
    dg = Location.dg("dg-edge")
    snap = Snapshot(
        addresses=[
            Address(name="web", location=dg, type=AddressType.IP_NETMASK, value="10.9.9.9/32"),
            Address(name="web", location=SHARED, type=AddressType.IP_NETMASK, value="10.0.0.1/32"),
        ],
        address_groups=[AddressGroup(name="g", location=dg, static_members=["web"])],
        device_groups=["dg-edge"],
    )
    g = ReferenceGraph.build(snap)
    (ref,) = [r for r in g.references if r.referrer_name == "g"]
    note = fall_through_note(g, ref, {("address", "web", "dg-edge")})
    assert note is not None
    assert "address 'web'@shared (10.0.0.1/32)" in note
    assert "10.9.9.9/32" not in note


def _shadow_service_graph(port: str | None) -> ReferenceGraph:
    """`web` defined as a service in shared AND in dg-edge, with a dg-local group."""
    dg = Location.dg("dg-edge")
    snap = Snapshot(
        services=[
            Service(name="web", location=SHARED, protocol="tcp", destination_port=port),
            Service(name="web", location=dg, protocol="tcp", destination_port="8443"),
        ],
        service_groups=[ServiceGroup(name="sg", location=dg, members=["web"])],
        device_groups=["dg-edge"],
    )
    return ReferenceGraph.build(snap)


def test_fall_through_note_names_a_surviving_service_with_its_port() -> None:
    """The port change is the hazard, so the note must carry the new port."""
    g = _shadow_service_graph("80")
    (ref,) = [r for r in g.references if r.referrer_name == "sg"]
    note = fall_through_note(g, ref, {("service", "web", "dg-edge")})
    assert note is not None
    assert "service 'web'@shared (tcp/80)" in note
    assert "8443" not in note


def test_fall_through_note_names_a_portless_service_by_protocol() -> None:
    g = _shadow_service_graph(None)
    (ref,) = [r for r in g.references if r.referrer_name == "sg"]
    note = fall_through_note(g, ref, {("service", "web", "dg-edge")})
    assert note is not None
    assert "service 'web'@shared (tcp)" in note
