from __future__ import annotations

from psc.core.source import OfflineSource
from psc.tui.screens.usage import UsageRow, selection_where_used
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(workbench_xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


def test_where_used_finds_group_referrer(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    rows = selection_where_used(sess)
    assert any(
        isinstance(r, UsageRow)
        and r.object_name == "web-srv-01"
        and r.referrer_kind == "address-group"
        and r.referrer_name == "web-pool"
        for r in rows
    )


def test_where_used_empty_for_unreferenced(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    sess.toggle(SelectionItem(kind="address", name="db-gw", location="shared"))
    assert selection_where_used(sess) == []


def test_where_used_only_considers_selection(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    assert selection_where_used(sess) == []
