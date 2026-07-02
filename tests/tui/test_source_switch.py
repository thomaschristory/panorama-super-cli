"""Tests for switching the active workbench source mid-session (#121)."""

from __future__ import annotations

from pathlib import Path

import panos.panorama
import pytest
from textual.widgets import DataTable, Input

from psc.config.loader import save_config
from psc.config.models import Config, Profile
from psc.core.source import LiveSource, OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.profiles import ProfilesScreen
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode, SelectionItem

from .conftest import WORKBENCH_XML_DG


def _session(xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(xml), output_mode=OutputMode.SET)


def _write_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *names: str) -> None:
    """Point PSC_CONFIG at a temp file holding the given profiles."""
    monkeypatch.setenv("PSC_CONFIG", str(tmp_path / "psc" / "config.yaml"))
    profiles = [Profile(name=n, hostname=f"{n}.example", api_key="LUFRPT1KEY") for n in names]
    save_config(Config(profiles=profiles))


class _DgPano:
    """Fake Panorama that serves WORKBENCH_XML_DG on read (no real device)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        class _Xapi:
            ssl_context = None

            def show(self, xpath: str, **kwargs: object) -> None: ...
            def xml_result(self) -> str:
                return WORKBENCH_XML_DG

        self.xapi = _Xapi()


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


# --- TUI: reload onto a focused live profile ---------------------------------


@pytest.mark.asyncio
async def test_profiles_spoke_reloads_live_profile(
    workbench_xml: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_config(monkeypatch, tmp_path, "prod")
    monkeypatch.setattr(panos.panorama, "Panorama", lambda *a, **k: _DgPano())
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("p")
        await pilot.pause()
        app.screen.query_one("#profile-table", DataTable).focus()  # focus the profile row
        # No reload-path -> targets the focused profile as a live source.
        await pilot.press("ctrl+r")
        await pilot.pause()
    assert isinstance(app.session.source, LiveSource)
    assert {a.name for a in app.session.working_snapshot.addresses} == {"anchor", "dg-only"}


# --- TUI: no profile focused + empty path is rejected ------------------------


@pytest.mark.asyncio
async def test_profiles_reload_no_target_is_rejected(
    workbench_xml: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_config(monkeypatch, tmp_path)  # no profiles at all
    original = OfflineSource(workbench_xml).path
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("p")
        await pilot.pause()
        # empty reload-path, no profile to focus
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert isinstance(app.screen, ProfilesScreen)  # stayed put, no crash
    assert isinstance(app.session.source, OfflineSource)
    assert app.session.source.path == original  # source unchanged


# --- TUI: moving the profile cursor re-requires confirmation -----------------


@pytest.mark.asyncio
async def test_profiles_reload_confirmation_resets_on_cursor_move(
    workbench_xml: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_config(monkeypatch, tmp_path, "p1", "p2")
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        # Stage a batch so a reload would discard it.
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

        await pilot.press("p")
        await pilot.pause()
        table = app.screen.query_one("#profile-table", DataTable)
        table.focus()
        await pilot.press("ctrl+r")  # arms, targeting p1
        await pilot.pause()
        table.move_cursor(row=1)  # cursor -> p2 resets the arm
        await pilot.pause()
        await pilot.press("ctrl+r")  # must re-arm, NOT reload
        await pilot.pause()
        # Never reloaded: the staged batch and the offline source are intact.
        assert len(app.session.staging) == 1
        assert isinstance(app.session.source, OfflineSource)
