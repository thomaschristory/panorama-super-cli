"""Regression tests for issues found in the adversarial review of the workbench.

Each test pins a specific bug that was fixed: cross-spoke plan staleness, engine
raises crashing the TUI, double-apply on repeat apply, and weak coverage of the
mutating spokes' actual effect (repoint/scrub/destination).
"""

from __future__ import annotations

import pytest
from textual.widgets import DataTable, Input

from psc.core.dedup import ObjectRef, plan_merge
from psc.core.refs import ReferenceGraph
from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.decommission import plan_selection_decommission
from psc.tui.screens.move import plan_move_item
from psc.tui.screens.rename import plan_rename_item
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(path: str, mode: OutputMode = OutputMode.SET) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(path), output_mode=mode)


def _merge_dupes(sess: WorkbenchSession) -> object:
    graph = ReferenceGraph.build(sess.working_snapshot)
    return plan_merge(
        sess.working_snapshot,
        graph,
        keep=ObjectRef(name="web-srv-01", location="shared"),
        drop=ObjectRef(name="web-srv-02", location="shared"),
    )


# --- cross-spoke stacking guard (was: stale plan corruption) ----------------


@pytest.mark.asyncio
async def test_spoke_key_cannot_stack_a_second_spoke(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "web-srv-01"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("x")  # open decommission
        await pilot.pause()
        depth_after_first = len(app.screen_stack)
        await pilot.press("r")  # try to stack rename on top — must be inert
        await pilot.pause()
        assert len(app.screen_stack) == depth_after_first
        assert type(app.screen).__name__ == "DecommissionScreen"


# --- rename repoints references (was: only checked the rename op) -----------


def test_rename_repoints_referencing_group(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    item = SelectionItem(kind="address", name="web-srv-01", location="shared")
    cs = plan_rename_item(sess, item, "web-server-01")
    sess.stage("rename web-srv-01", cs)
    # object renamed
    names = {a.name for a in sess.working_snapshot.addresses}
    assert "web-server-01" in names
    assert "web-srv-01" not in names
    # the referencing group's member was repointed, not left dangling
    pool = next(g for g in sess.working_snapshot.address_groups if g.name == "web-pool")
    assert pool.static_members is not None
    assert "web-server-01" in pool.static_members
    assert "web-srv-01" not in pool.static_members


# --- move actually lands in the destination (was: only is_blocked check) ----


def test_move_lands_object_in_shared(workbench_xml_dg: str) -> None:
    sess = _session(workbench_xml_dg)
    item = SelectionItem(kind="address", name="dg-only", location="dg1")
    cs = plan_move_item(sess, item, "shared")
    sess.stage("move dg-only -> shared", cs)
    shared = {a.name for a in sess.working_snapshot.addresses if a.location.name == "shared"}
    dg1 = {a.name for a in sess.working_snapshot.addresses if a.location.name == "dg1"}
    assert "dg-only" in shared
    assert "dg-only" not in dg1


# --- decommission scrubs references before deleting (was: unreferenced obj) --


def test_decommission_scrubs_group_reference(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    sess.toggle(SelectionItem(kind="address", name="web-srv-01", location="shared"))
    cs = plan_selection_decommission(sess)
    assert cs is not None
    sess.stage("decommission web-srv-01", cs)
    # object gone
    assert not any(a.name == "web-srv-01" for a in sess.working_snapshot.addresses)
    # web-pool must not still reference the deleted object (it is either scrubbed
    # of the member or removed entirely once emptied) — never left dangling.
    pool = next((g for g in sess.working_snapshot.address_groups if g.name == "web-pool"), None)
    if pool is not None:
        assert "web-srv-01" not in (pool.static_members or [])


# --- heterogeneous compounding across two different spokes -------------------


def test_compound_dedup_then_rename(workbench_xml: str) -> None:
    sess = _session(workbench_xml)
    sess.stage("merge dupes", _merge_dupes(sess))
    # second, different spoke — planned against the compounded snapshot
    rename = plan_rename_item(
        sess, SelectionItem(kind="address", name="db-gw", location="shared"), "db-gateway"
    )
    assert not rename.is_blocked
    sess.stage("rename db-gw", rename)
    names = {a.name for a in sess.working_snapshot.addresses}
    assert len(sess.staging) == 2
    assert "web-srv-02" not in names  # merged away
    assert "db-gw" not in names and "db-gateway" in names  # renamed


# --- apply clears staging so a repeat apply can't double-push ----------------


def test_offline_apply_clears_staging(workbench_xml: str, tmp_path) -> None:
    sess = _session(workbench_xml, OutputMode.OFFLINE_APPLY)
    sess.stage("merge", _merge_dupes(sess))
    dest = tmp_path / "out.xml"
    first = sess.apply_batch(out_path=str(dest))
    assert first.ops == 1
    assert sess.staging == []  # cleared after a real apply
    # a second apply has nothing to replay
    second = sess.apply_batch(out_path=str(dest))
    assert second.ops == 0


def test_atomic_write_leaves_no_tmp_file(workbench_xml: str, tmp_path) -> None:
    sess = _session(workbench_xml, OutputMode.OFFLINE_APPLY)
    sess.stage("merge", _merge_dupes(sess))
    dest = tmp_path / "out.xml"
    sess.apply_batch(out_path=str(dest))
    assert dest.exists()
    assert not (tmp_path / "out.xml.tmp").exists()  # temp sibling cleaned up


# --- rule spoke stages end-to-end through its two-input form -----------------


@pytest.mark.asyncio
async def test_rule_spoke_stages_member_add(workbench_xml_rule: str) -> None:
    sess = _session(workbench_xml_rule)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        assert [i.name for i in sess.selection] == ["db-gw"]
        await pilot.press("e")
        await pilot.pause()
        app.screen.query_one("#rule-name", Input).value = "allow-web"
        field = app.screen.query_one("#rule-field", Input)
        field.value = "source"
        field.focus()  # staging only fires on submit of the field box
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert len(sess.staging) == 1
    assert "db-gw" in sess.working_xml
