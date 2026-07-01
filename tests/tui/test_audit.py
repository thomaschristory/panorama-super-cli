from __future__ import annotations

from psc.core.source import OfflineSource
from psc.tui.screens.audit import selection_overlaps
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(workbench_xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


def test_overlaps_finds_containment_involving_selection(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    pairs = selection_overlaps(sess)
    names = {(p.left_name, p.right_name) for p in pairs}
    assert ("net-10-0-5", "web-srv-01") in names


def test_overlaps_empty_when_selection_not_involved(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    sess.toggle(SelectionItem(kind="address", name="db-gw", location="shared"))
    assert selection_overlaps(sess) == []


def test_overlaps_empty_without_selection(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    assert selection_overlaps(sess) == []
