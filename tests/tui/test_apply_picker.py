"""Tests for the interactive apply-time output picker (#122)."""

from __future__ import annotations

import panos.panorama
import pytest
from textual.widgets import DataTable, Input, Select

from psc.core.source import LiveSource, OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.apply import ApplyScreen, initial_disposition
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode

from .conftest import WORKBENCH_XML


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


async def _open_apply(app: WorkbenchApp, pilot) -> None:
    """Open the apply picker the supported way (#127): via the staged changelist."""
    app.query_one("#results", DataTable).focus()  # off the search Input
    await pilot.press("s")  # staged changelist
    await pilot.pause()
    await pilot.press("ctrl+a")  # -> apply picker
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


# --- apply is reachable only from the staged changelist (#127) ---------------


@pytest.mark.asyncio
async def test_apply_picker_opens_from_staged(workbench_xml: str) -> None:
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        await _stage_one_merge(app, pilot)
        app.query_one("#results", DataTable).focus()
        await pilot.press("s")  # staged changelist first
        await pilot.pause()
        await pilot.press("ctrl+a")  # then apply
        await pilot.pause()
        assert isinstance(app.screen, ApplyScreen)


@pytest.mark.asyncio
async def test_ctrl_a_on_hub_does_not_open_apply(workbench_xml: str) -> None:
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        await _stage_one_merge(app, pilot)
        app.query_one("#results", DataTable).focus()
        await pilot.press("ctrl+a")  # hub has no apply binding anymore
        await pilot.pause()
        assert not isinstance(app.screen, ApplyScreen)
        assert len(app.session.staging) == 1  # nothing applied


# --- choosing offline-full in-app, overriding the launched SET default -------


@pytest.mark.asyncio
async def test_picker_writes_offline_config_chosen_in_app(workbench_xml: str, tmp_path) -> None:
    dest = tmp_path / "candidate.xml"
    app = WorkbenchApp(_session(workbench_xml))  # launched in SET mode
    async with app.run_test() as pilot:
        await _stage_one_merge(app, pilot)
        await _open_apply(app, pilot)
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
        await _open_apply(app, pilot)
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
        await _open_apply(app, pilot)
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
        await _open_apply(app, pilot)
        select = app.screen.query_one("#apply-mode", Select)
        values = {value for _label, value in select._options}
        assert "live-push" not in values


# --- set-preview: an inline export that keeps the batch staged ---------------


@pytest.mark.asyncio
async def test_picker_set_preview_keeps_staging(workbench_xml: str) -> None:
    app = WorkbenchApp(_session(workbench_xml))  # default disposition = set-preview
    async with app.run_test() as pilot:
        await _stage_one_merge(app, pilot)
        await _open_apply(app, pilot)
        assert app.screen.query_one("#apply-mode", Select).value == "set-preview"
        await pilot.press("ctrl+a")  # preview: renders inline, writes nothing
        await pilot.pause()
        assert len(app.session.staging) == 1  # a preview is not a commit


# --- a file/config option with no destination path is rejected ---------------


@pytest.mark.asyncio
async def test_picker_missing_path_is_rejected(workbench_xml: str) -> None:
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        await _stage_one_merge(app, pilot)
        await _open_apply(app, pilot)
        app.screen.query_one("#apply-mode", Select).value = "offline-full"
        # leave #apply-path empty
        await pilot.press("ctrl+a")
        await pilot.pause()
        # Rejected: nothing applied, staging intact, still on the apply screen.
        assert len(app.session.staging) == 1
        assert isinstance(app.screen, ApplyScreen)


# --- pressing ctrl+a with nothing staged is rejected -------------------------


@pytest.mark.asyncio
async def test_picker_nothing_staged_is_rejected(workbench_xml: str) -> None:
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        await _open_apply(app, pilot)  # via the (empty) staged changelist
        assert isinstance(app.screen, ApplyScreen)
        await pilot.press("ctrl+a")  # nothing staged -> rejected, no crash
        await pilot.pause()
        assert isinstance(app.screen, ApplyScreen)
        assert app.session.staging == []


# --- changing the path after arming re-requires confirmation -----------------


@pytest.mark.asyncio
async def test_picker_confirmation_resets_on_path_change(workbench_xml: str, tmp_path) -> None:
    a = tmp_path / "a.xml"
    a.write_text("OLDA", encoding="utf-8")
    b = tmp_path / "b.xml"
    b.write_text("OLDB", encoding="utf-8")
    app = WorkbenchApp(_session(workbench_xml))
    async with app.run_test() as pilot:
        await _stage_one_merge(app, pilot)
        await _open_apply(app, pilot)
        app.screen.query_one("#apply-mode", Select).value = "offline-full"
        app.screen.query_one("#apply-path", Input).value = str(a)
        await pilot.press("ctrl+a")  # arms overwrite of a.xml
        await pilot.pause()
        app.screen.query_one("#apply-path", Input).value = str(b)  # resets the arm
        await pilot.press("ctrl+a")  # b exists -> must re-arm, NOT write yet
        await pilot.pause()
        assert b.read_text(encoding="utf-8") == "OLDB"
        await pilot.press("ctrl+a")  # now confirms + writes b
        await pilot.pause()
    assert a.read_text(encoding="utf-8") == "OLDA"  # the abandoned target is untouched
    assert b.read_text(encoding="utf-8") != "OLDB"


# --- live session: live-push is offered and needs a second confirmation ------


class _LiveWorkbenchPano:
    """Fake Panorama for the workbench live path: serves the config on read and
    records candidate writes without ever committing."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.committed = False
        self.calls: list[str] = []
        pano = self

        class _Xapi:
            ssl_context = None

            def show(self, xpath: str, **kwargs: object) -> None: ...
            def xml_result(self) -> str:
                return WORKBENCH_XML

            def set(self, xpath: str, element: str, **kwargs: object) -> None:
                pano.calls.append("set")

            def edit(self, xpath: str, element: str, **kwargs: object) -> None:
                pano.calls.append("edit")

            def delete(self, xpath: str, **kwargs: object) -> None:
                pano.calls.append("delete")

            def rename(self, xpath: str, newname: str, **kwargs: object) -> None:
                pano.calls.append("rename")

        self.xapi = _Xapi()

    def commit(self, *args: object, **kwargs: object) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_picker_live_push_requires_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    pano = _LiveWorkbenchPano()
    monkeypatch.setattr(panos.panorama, "Panorama", lambda *a, **k: pano)
    session = WorkbenchSession(
        source=LiveSource("pano.example", "LUFRPT1KEYABC123", verify=False),
        output_mode=OutputMode.LIVE_APPLY,
    )
    app = WorkbenchApp(session)
    async with app.run_test() as pilot:
        await _stage_one_merge(app, pilot)
        await _open_apply(app, pilot)
        select = app.screen.query_one("#apply-mode", Select)
        assert select.value == "live-push"  # default pre-seeded + offered on live
        await pilot.press("ctrl+a")  # first press: arms, pushes NOTHING
        await pilot.pause()
        assert pano.calls == []
        assert len(app.session.staging) == 1
        await pilot.press("ctrl+a")  # second press: confirms + pushes to candidate
        await pilot.pause()
    assert pano.calls  # the batch was pushed
    assert pano.committed is False  # never commits
    assert app.session.staging == []  # live apply clears staging
