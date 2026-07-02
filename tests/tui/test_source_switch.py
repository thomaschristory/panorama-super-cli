"""Tests for switching the active workbench source mid-session (#121)."""

from __future__ import annotations

import pytest
from textual.widgets import DataTable, Input

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.profiles import ProfilesScreen
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem


def _session(xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(xml), output_mode=OutputMode.SET)


# --- engine: reload rebuilds the snapshot and clears session state -----------


def test_reload_rebuilds_snapshot_and_clears_state(
    workbench_xml: str, workbench_xml_dg: str
) -> None:
    session = _session(workbench_xml)
    session.selection.append(SelectionItem(kind="address", name="db-gw", location="shared"))
    names_before = {a.name for a in session.working_snapshot.addresses}
    assert "web-srv-01" in names_before

    session.reload(OfflineSource(workbench_xml_dg))

    names_after = {a.name for a in session.working_snapshot.addresses}
    assert names_after == {"anchor", "dg-only"}  # the new config
    assert "web-srv-01" not in names_after
    assert session.selection == []  # selection referenced the old config
    assert session.staging == []
    assert isinstance(session.source, OfflineSource)
    assert session.source.path == OfflineSource(workbench_xml_dg).path


# --- TUI: load an offline export from the profiles spoke ----------------------


@pytest.mark.asyncio
async def test_profiles_spoke_reloads_offline_export(
    workbench_xml: str, workbench_xml_dg: str
) -> None:
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()  # off the search Input
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, ProfilesScreen)
        app.screen.query_one("#reload-path", Input).value = workbench_xml_dg
        await pilot.press("ctrl+r")  # nothing staged -> reloads immediately
        await pilot.pause()
    names = {a.name for a in app.session.working_snapshot.addresses}
    assert names == {"anchor", "dg-only"}
    assert isinstance(app.session.source, OfflineSource)
    assert app.session.source.path == OfflineSource(workbench_xml_dg).path


# --- TUI: reloading with a staged batch needs a second confirmation ----------


@pytest.mark.asyncio
async def test_profiles_reload_discard_requires_confirmation(
    workbench_xml: str, workbench_xml_dg: str
) -> None:
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        # Stage a dedup merge so the batch is non-empty.
        app.query_one("#search", Input).value = "10.0.5.10"
        await pilot.press("enter")
        await pilot.pause()
        results = app.query_one("#results", DataTable)
        results.focus()
        await pilot.press("space")
        results.move_cursor(row=1)
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert len(app.session.staging) == 1

        await pilot.press("p")
        await pilot.pause()
        app.screen.query_one("#reload-path", Input).value = workbench_xml_dg
        await pilot.press("ctrl+r")  # first press: arms, does NOT reload
        await pilot.pause()
        assert len(app.session.staging) == 1  # batch preserved
        assert "web-srv-01" in {a.name for a in app.session.working_snapshot.addresses}
        await pilot.press("ctrl+r")  # second press: confirms, reloads, discards
        await pilot.pause()
    assert app.session.staging == []
    assert {a.name for a in app.session.working_snapshot.addresses} == {"anchor", "dg-only"}
