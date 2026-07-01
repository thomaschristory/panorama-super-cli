from __future__ import annotations

from psc.core.source import OfflineSource
from psc.tui.screens.move import movable_items, plan_move_item
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(path: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(path), output_mode=OutputMode.SET)


def test_movable_items_excludes_shared(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    assert movable_items(sess) == []


def test_movable_items_includes_device_group_object(workbench_xml_dg: str) -> None:
    sess = _session(workbench_xml_dg)
    item = SelectionItem(kind="address", name="dg-only", location="dg1")
    sess.toggle(item)
    assert movable_items(sess) == [item]


def test_plan_move_item_to_shared_is_not_blocked(workbench_xml_dg: str) -> None:
    sess = _session(workbench_xml_dg)
    item = SelectionItem(kind="address", name="dg-only", location="dg1")
    cs = plan_move_item(sess, item, "shared")
    assert not cs.is_blocked
    assert not cs.is_empty
