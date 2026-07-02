"""Pilot test: the in-TUI profile manager (issue #83).

Opens the profile screen from the hub (`p`), adds a profile through the form,
and asserts it is both listed on-screen and persisted to the on-disk config.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import DataTable, Input

from psc.config.loader import load_config
from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.profiles import ProfilesScreen
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _app(workbench_xml: str) -> WorkbenchApp:
    sess = WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)
    return WorkbenchApp(sess)


@pytest.mark.asyncio
async def test_profile_screen_add_persists(
    workbench_xml: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg_path = tmp_path / "psc" / "config.yaml"
    monkeypatch.setenv("PSC_CONFIG", str(cfg_path))
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()  # move focus off the search Input
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, ProfilesScreen)
        app.screen.query_one("#profile-name", Input).value = "prod"
        app.screen.query_one("#profile-host", Input).value = "pano.example"
        app.screen.query_one("#profile-api-key", Input).value = "SECRET"
        await pilot.pause()
        await pilot.press("ctrl+y")  # add/update
        await pilot.pause()
        # Listed on-screen.
        table = app.screen.query_one("#profile-table", DataTable)
        cells = {str(table.get_cell(rk, ck)) for rk in table.rows for ck in table.columns}
        assert any("prod" in c for c in cells)
        assert any("pano.example" in c for c in cells)
    # Persisted to disk.
    loaded = load_config()
    assert [p.name for p in loaded.profiles] == ["prod"]
    assert loaded.profiles[0].api_key == "SECRET"


@pytest.mark.asyncio
async def test_profile_screen_opens_from_hub(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, ProfilesScreen)
        await pilot.press("escape")
        await pilot.pause()
