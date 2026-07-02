"""Tests for the interactive apply-time output picker (#122)."""

from __future__ import annotations

import pytest
from textual.widgets import DataTable, Input, Select

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.apply import ApplyScreen, initial_disposition
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _session(xml: str, mode: OutputMode = OutputMode.SET) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(xml), output_mode=mode)


async def _stage_one_merge(app: WorkbenchApp, pilot) -> None:
    """Select the two duplicate web-srv addresses and stage a dedup merge."""
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


# --- default disposition from launch flags -----------------------------------


def test_initial_disposition_tracks_launch_mode(workbench_xml: str) -> None:
    assert initial_disposition(_session(workbench_xml, OutputMode.SET)) == "set-preview"
    off = _session(workbench_xml, OutputMode.OFFLINE_APPLY)
    assert initial_disposition(off) == "offline-full"
    off.offline_partial = True
    assert initial_disposition(off) == "offline-partial"
    set_file = _session(workbench_xml, OutputMode.SET)
    set_file.apply_out_path = "/tmp/x.set"
    assert initial_disposition(set_file) == "set-file"


# --- ctrl+a opens the picker instead of applying directly --------------------


@pytest.mark.asyncio
async def test_ctrl_a_opens_apply_picker(workbench_xml: str) -> None:
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        await _stage_one_merge(app, pilot)
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert isinstance(app.screen, ApplyScreen)


# --- choosing offline-full in-app, overriding the launched SET default -------


@pytest.mark.asyncio
async def test_picker_writes_offline_config_chosen_in_app(workbench_xml: str, tmp_path) -> None:
    dest = tmp_path / "candidate.xml"
    app = WorkbenchApp(_session(workbench_xml))  # launched in SET mode
    async with app.run_test() as pilot:
        await _stage_one_merge(app, pilot)
        await pilot.press("ctrl+a")
        await pilot.pause()
        app.screen.query_one("#apply-mode", Select).value = "offline-full"
        app.screen.query_one("#apply-path", Input).value = str(dest)
        await pilot.press("ctrl+a")  # dest does not exist -> applies immediately
        await pilot.pause()
    assert dest.exists()
    assert "web-srv-02" not in dest.read_text(encoding="utf-8")
    # OFFLINE_APPLY commits, so staging is cleared.
    assert app.session.staging == []


# --- set-file export keeps staging -------------------------------------------


@pytest.mark.asyncio
async def test_picker_set_file_keeps_staging(workbench_xml: str, tmp_path) -> None:
    dest = tmp_path / "batch.set"
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        await _stage_one_merge(app, pilot)
        await pilot.press("ctrl+a")
        await pilot.pause()
        app.screen.query_one("#apply-mode", Select).value = "set-file"
        app.screen.query_one("#apply-path", Input).value = str(dest)
        await pilot.press("ctrl+a")
        await pilot.pause()
    assert dest.exists()
    # A set-script export is not a commit -> staging is preserved.
    assert len(app.session.staging) == 1


# --- overwrite of an existing file needs a second confirmation ---------------


@pytest.mark.asyncio
async def test_picker_overwrite_requires_confirmation(workbench_xml: str, tmp_path) -> None:
    dest = tmp_path / "existing.xml"
    dest.write_text("OLD", encoding="utf-8")
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        await _stage_one_merge(app, pilot)
        await pilot.press("ctrl+a")
        await pilot.pause()
        app.screen.query_one("#apply-mode", Select).value = "offline-full"
        app.screen.query_one("#apply-path", Input).value = str(dest)
        await pilot.press("ctrl+a")  # first press: arms, does NOT write
        await pilot.pause()
        assert dest.read_text(encoding="utf-8") == "OLD"  # untouched
        await pilot.press("ctrl+a")  # second press: confirms + writes
        await pilot.pause()
    assert dest.read_text(encoding="utf-8") != "OLD"


# --- an offline session never offers the live-push option --------------------


@pytest.mark.asyncio
async def test_picker_offline_session_hides_live_option(workbench_xml: str) -> None:
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        await _stage_one_merge(app, pilot)
        await pilot.press("ctrl+a")
        await pilot.pause()
        select = app.screen.query_one("#apply-mode", Select)
        values = {value for _label, value in select._options}
        assert "live-push" not in values
