from __future__ import annotations

import pytest

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
        app.query_one("#search").value = "srv"  # type: ignore[union-attr]
        await pilot.press("enter")
        await pilot.pause()
        table = app.query_one("#results")
        assert table.row_count == 2  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_space_toggles_selection(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#search").value = "db-gw"  # type: ignore[union-attr]
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#results").focus()
        await pilot.press("space")
        await pilot.pause()
        assert [i.name for i in app.session.selection] == ["db-gw"]
