from __future__ import annotations

from psc.core.source import OfflineSource
from psc.tui.screens.rename import first_renameable, plan_rename_item
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(workbench_xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


def test_first_renameable_returns_first_selected(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    a = SelectionItem(kind="address", name="db-gw", location="shared")
    sess.toggle(a)
    assert first_renameable(sess) == a


def test_first_renameable_none_when_empty(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    assert first_renameable(sess) is None


def test_plan_rename_item_builds_reference_aware_rename(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    # web-srv-01 is referenced by web-pool; renaming must repoint that group.
    item = SelectionItem(kind="address", name="web-srv-01", location="shared")
    cs = plan_rename_item(sess, item, "web-server-01")
    assert not cs.is_blocked
    assert not cs.is_empty
    assert any(r.new_name == "web-server-01" for r in cs.renames)
