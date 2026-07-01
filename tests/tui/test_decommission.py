from __future__ import annotations

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
