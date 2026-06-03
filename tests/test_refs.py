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


def test_predefined_any_not_dangling() -> None:
    xml = """<config><shared>
      <pre-rulebase><security><rules>
        <entry name="r"><source><member>any</member></source>
          <destination><member>any</member></destination></entry>
      </rules></security></pre-rulebase>
    </shared></config>"""
    g = ReferenceGraph.build(parse_config(xml))
    assert g.dangling() == []
