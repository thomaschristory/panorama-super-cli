from __future__ import annotations

from pathlib import Path

from psc.core.source import OfflineSource
from psc.tui.screens.decommission import plan_selection_decommission
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(workbench_xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


def test_decommission_plans_delete_for_selected_address(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    sess.toggle(SelectionItem(kind="address", name="db-gw", location="shared"))
    cs = plan_selection_decommission(sess)
    assert cs is not None
    assert not cs.is_empty
    assert any(d.name == "db-gw" for d in cs.deletes)


def test_decommission_none_without_address_selection(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    assert plan_selection_decommission(sess) is None
    sess.toggle(SelectionItem(kind="service", name="tcp-8443", location="shared"))
    assert plan_selection_decommission(sess) is None


def test_decommission_reconciles_after_stage(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    item = SelectionItem(kind="address", name="db-gw", location="shared")
    sess.toggle(item)
    cs = plan_selection_decommission(sess)
    assert cs is not None
    sess.stage("decommission db-gw", cs)
    assert item not in sess.selection


# -- PAN-OS shadowing reaches the workbench too (#187) --------------------

SHADOW_XML = """<?xml version="1.0"?>
<config>
  <shared>
    <address>
      <entry name="web"><ip-netmask>10.0.0.1/32</ip-netmask></entry>
    </address>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group>
        <entry name="dg-a">
          <address>
            <entry name="web"><ip-netmask>10.9.9.9/32</ip-netmask></entry>
          </address>
          <address-group>
            <entry name="g"><static><member>web</member></static></entry>
          </address-group>
          <pre-rulebase>
            <security>
              <rules>
                <entry name="r1">
                  <source><member>g</member></source>
                  <destination><member>any</member></destination>
                  <service><member>any</member></service>
                </entry>
              </rules>
            </security>
          </pre-rulebase>
        </entry>
      </device-group>
    </entry>
  </devices>
</config>
"""


def _shadow_session(tmp_path: Path) -> WorkbenchSession:
    path = tmp_path / "shadow.xml"
    path.write_text(SHADOW_XML, encoding="utf-8")
    return _session(str(path))


def test_decommission_spoke_keeps_a_shadowed_group_and_rule(tmp_path: Path) -> None:
    sess = _shadow_session(tmp_path)
    sess.toggle(SelectionItem(kind="address", name="web", location="dg-a"))
    cs = plan_selection_decommission(sess)
    assert cs is not None
    assert [(d.kind.value, d.name, d.location) for d in cs.deletes] == [("address", "web", "dg-a")]
    assert cs.rule_deletes == []
    assert cs.reference_edits == []


def test_decommission_spoke_shows_the_fall_through_warning(tmp_path: Path) -> None:
    sess = _shadow_session(tmp_path)
    sess.toggle(SelectionItem(kind="address", name="web", location="dg-a"))
    cs = plan_selection_decommission(sess)
    assert cs is not None
    assert any("points to address 'web'@shared (10.0.0.1/32)" in w for w in cs.warnings)
