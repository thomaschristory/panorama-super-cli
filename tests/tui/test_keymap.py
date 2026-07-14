from __future__ import annotations

import pytest
from textual.widgets import DataTable, Static

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.commands import HUB_COMMANDS
from psc.tui.screens.keymap import KeymapScreen
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _app(workbench_xml: str) -> WorkbenchApp:
    sess = WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)
    return WorkbenchApp(sess)


@pytest.mark.asyncio
async def test_question_mark_opens_the_overlay(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()  # off the search Input
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, KeymapScreen)


@pytest.mark.asyncio
async def test_overlay_lists_every_command_with_its_description(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("question_mark")
        await pilot.pause()
        body = str(app.screen.query_one("#keymap-body", Static).render())
        for cmd in HUB_COMMANDS:
            assert cmd.title in body, cmd.title
            assert cmd.description in body, cmd.description


@pytest.mark.asyncio
async def test_overlay_groups_by_category(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("question_mark")
        await pilot.pause()
        body = str(app.screen.query_one("#keymap-body", Static).render())
        for category in ("Navigate", "Objects", "Analyze", "Names", "Session"):
            assert category in body


@pytest.mark.asyncio
async def test_escape_dismisses_the_overlay(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, KeymapScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, KeymapScreen)


@pytest.mark.asyncio
async def test_question_mark_again_dismisses_the_overlay(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("question_mark")
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        assert not isinstance(app.screen, KeymapScreen)


@pytest.mark.asyncio
async def test_hub_keys_are_inert_while_the_overlay_is_up(workbench_xml: str) -> None:
    # The overlay is a screen on the stack, so check_action gates the hub keys —
    # pressing 'd' must not stack the dedup spoke behind the cheatsheet.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("question_mark")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, KeymapScreen)
        assert len(app.screen_stack) == 2  # hub + overlay, nothing else
