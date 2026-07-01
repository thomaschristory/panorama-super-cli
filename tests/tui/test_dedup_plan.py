"""Unit tests for the pure dedup-planning helper (no running Textual app)."""

from __future__ import annotations

from psc.core.source import OfflineSource
from psc.tui.screens.dedup import plan_selection_merge
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(workbench_xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)


def test_plan_selection_merge_none_without_duplicate_pair(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    # db-gw is unique; a single address can't form a pair.
    sess.toggle(SelectionItem(kind="address", name="db-gw", location="shared"))
    assert plan_selection_merge(sess) is None


def test_plan_selection_merge_ignores_non_address_kinds(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    sess.toggle(SelectionItem(kind="service", name="tcp-8443", location="shared"))
    assert plan_selection_merge(sess) is None


def test_plan_selection_merge_keeps_first_selected(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    # web-srv-01 and web-srv-02 share 10.0.5.10/32; select 02 first, 01 second.
    sess.toggle(SelectionItem(kind="address", name="web-srv-02", location="shared"))
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    plan = plan_selection_merge(sess)
    assert plan is not None
    label, cs = plan
    # first-selected (web-srv-02) is kept, second (web-srv-01) is dropped
    assert label == "merge web-srv-01 -> web-srv-02"
    assert not cs.is_blocked
