from __future__ import annotations

from psc.core.models import SHARED, Location, Snapshot
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


def test_predefined_any_not_dangling() -> None:
    xml = """<config><shared>
      <pre-rulebase><security><rules>
        <entry name="r"><source><member>any</member></source>
          <destination><member>any</member></destination></entry>
      </rules></security></pre-rulebase>
    </shared></config>"""
    g = ReferenceGraph.build(parse_config(xml))
    assert g.dangling() == []
