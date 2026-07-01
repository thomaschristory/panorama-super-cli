from __future__ import annotations

import pytest
from textual.widgets import DataTable, Input

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _app(workbench_xml: str) -> WorkbenchApp:
    sess = WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)
    return WorkbenchApp(sess)


@pytest.mark.asyncio
async def test_search_populates_results(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "srv"
        await pilot.press("enter")
        await pilot.pause()
        table = app.query_one("#results", DataTable)
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_space_toggles_selection(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        assert [i.name for i in app.session.selection] == ["db-gw"]


@pytest.mark.asyncio
async def test_space_twice_deselects(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "db-gw"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert app.session.selection == []


@pytest.mark.asyncio
async def test_dedup_spoke_stages_merge_and_reconciles(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search", Input).value = "10.0.5.10"
        await pilot.press("enter")
        await pilot.pause()
        results = app.query_one("#results", DataTable)
        results.focus()
        await pilot.press("space")  # row 0
        results.move_cursor(row=1)
        await pilot.press("space")  # row 1
        await pilot.pause()
        assert len(app.session.selection) == 2
        await pilot.press("d")  # open dedup screen
        await pilot.pause()
        await pilot.press("ctrl+y")  # stage the proposed merge
        await pilot.pause()
        assert len(app.session.staging) == 1
        assert len(app.session.selection) == 1  # reconciled: merged-away dupe gone
