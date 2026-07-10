from __future__ import annotations

import pytest
from textual.widgets import DataTable, Input

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.group import GroupScreen, plan_group_add_member
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _session(path: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(path), output_mode=OutputMode.SET)


def test_plan_group_add_member(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    cs = plan_group_add_member(sess, "web-pool", "db-gw")
    (edit,) = cs.reference_edits
    assert edit.referrer_kind == "address-group"
    assert edit.after == ["web-srv-01", "db-gw"]


@pytest.mark.asyncio
async def test_g_adds_selection_to_group(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")  # select db-gw
        await pilot.pause()
        await pilot.press("G")  # open group spoke
        await pilot.pause()
        assert isinstance(app.screen, GroupScreen)
        app.screen.query_one("#group-name", Input).value = "web-pool"
        await pilot.press("enter")
        await pilot.pause()
        assert [s.label for s in sess.staging] == ["add db-gw to web-pool"]
        grp = next(g for g in sess.working_snapshot.address_groups if g.name == "web-pool")
        assert grp.static_members == ["web-srv-01", "db-gw"]


@pytest.mark.asyncio
async def test_group_spoke_empty_selection_shows_prompt(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("G")
        await pilot.pause()
        assert isinstance(app.screen, GroupScreen)
        assert app.screen.query_one("#group-empty")


@pytest.mark.asyncio
async def test_group_spoke_unknown_group_does_not_stage(workbench_xml_refs: str) -> None:
    sess = _session(workbench_xml_refs)
    app = WorkbenchApp(sess)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("G")
        await pilot.pause()
        app.screen.query_one("#group-name", Input).value = "no-such-group"
        await pilot.press("enter")
        await pilot.pause()
        assert sess.staging == []
        assert isinstance(app.screen, GroupScreen)  # stays open after the bell
