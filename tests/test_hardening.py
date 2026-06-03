"""Regression tests for the v0.1 adversarial-review findings (issue #17)."""

from __future__ import annotations

from psc.core.apply_xml import apply_changeset
from psc.core.dedup import ObjectRef, plan_merge
from psc.core.models import Location
from psc.core.parse import parse_config
from psc.core.refs import ReferenceGraph
from psc.core.resolve import find_ip

# --- Finding 1: special characters in names must not break apply -----------

QUOTED_NAME_CONFIG = """<config><shared>
  <address>
    <entry name="a"><ip-netmask>10.0.0.10/32</ip-netmask></entry>
    <entry name="b"><ip-netmask>10.0.0.10/32</ip-netmask></entry>
  </address>
  <address-group>
    <entry name="grp's [edge]"><static><member>a</member><member>b</member></static></entry>
  </address-group>
</shared></config>"""


def test_apply_handles_quotes_and_brackets_in_names() -> None:
    snap = parse_config(QUOTED_NAME_CONFIG)
    graph = ReferenceGraph.build(snap)
    cs = plan_merge(
        snap,
        graph,
        keep=ObjectRef(name="a", location="shared"),
        drop=ObjectRef(name="b", location="shared"),
    )
    assert not cs.is_blocked
    new_snap = parse_config(apply_changeset(QUOTED_NAME_CONFIG, cs))
    grp = next(g for g in new_snap.address_groups if g.name == "grp's [edge]")
    # The reference was actually rewritten (not silently skipped) and b deleted.
    assert grp.static_members == ["a"]
    assert all(x.name != "b" for x in new_snap.addresses)


# --- Finding 2: NAT translation reference blocks the merge -----------------

NAT_XLATE_CONFIG = """<config><shared>
  <address>
    <entry name="pool-a"><ip-netmask>198.51.100.10/32</ip-netmask></entry>
    <entry name="pool-b"><ip-netmask>198.51.100.10/32</ip-netmask></entry>
  </address>
  <pre-rulebase><nat><rules>
    <entry name="n1">
      <source><member>any</member></source>
      <source-translation><dynamic-ip-and-port>
        <translated-address><member>pool-b</member></translated-address>
      </dynamic-ip-and-port></source-translation>
    </entry>
  </rules></nat></pre-rulebase>
</shared></config>"""


def test_merge_blocks_on_nat_translation_reference() -> None:
    snap = parse_config(NAT_XLATE_CONFIG)
    graph = ReferenceGraph.build(snap)
    cs = plan_merge(
        snap,
        graph,
        keep=ObjectRef(name="pool-a", location="shared"),
        drop=ObjectRef(name="pool-b", location="shared"),
    )
    assert cs.is_blocked
    assert any("translation" in b for b in cs.blockers)
    # Finding 4: a blocked plan carries zero ops.
    assert cs.reference_edits == []
    assert cs.deletes == []


# --- Finding 3: find ip group membership respects DG shadowing -------------

SHADOW_CONFIG = """<config>
  <shared>
    <address><entry name="H-web"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
  </shared>
  <devices><entry name="localhost.localdomain"><device-group>
    <entry name="prod">
      <address><entry name="H-web"><ip-netmask>10.99.99.1/32</ip-netmask></entry></address>
      <address-group>
        <entry name="G"><static><member>H-web</member></static></entry>
      </address-group>
    </entry>
  </device-group></entry></devices>
</config>"""


def test_find_ip_group_respects_dg_shadow() -> None:
    snap = parse_config(SHADOW_CONFIG)
    # Querying the shared value while scoped to prod: group G lists the *prod*
    # H-web (10.99.99.1), which does NOT match 10.0.0.1 — G must not appear.
    res = find_ip(snap, "10.0.0.1", scope=Location.dg("prod"))
    assert "G" not in {g.name for g in res.groups}
    # Sanity: querying the prod-local value DOES surface G.
    res2 = find_ip(snap, "10.99.99.1", scope=Location.dg("prod"))
    assert "G" in {g.name for g in res2.groups}


# --- Finding 5: unused tag uses token, not substring, matching -------------

TAG_FILTER_CONFIG = """<config><shared>
  <tag>
    <entry name="web"><color>color1</color></entry>
    <entry name="webserver"><color>color2</color></entry>
  </tag>
  <address-group>
    <entry name="dag"><dynamic><filter>'webserver'</filter></dynamic></entry>
  </address-group>
</shared></config>"""


def test_unused_tag_token_match_not_substring() -> None:
    graph = ReferenceGraph.build(parse_config(TAG_FILTER_CONFIG))
    unused = {t.name for t in graph.unused("tag")}
    assert "webserver" not in unused  # actually referenced by the filter
    assert "web" in unused  # NOT referenced — substring match would have hidden it
