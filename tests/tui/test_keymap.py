from __future__ import annotations

import pytest
from rich.console import Console
from textual.widgets import DataTable, Input, Static

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.commands import CATEGORIES, HUB_COMMANDS
from psc.tui.screens.keymap import DISMISS_HINT, KeymapScreen
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _app(workbench_xml: str) -> WorkbenchApp:
    sess = WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)
    return WorkbenchApp(sess)


def _rendered_text(static: Static, width: int) -> str:
    """Render a Static's content the way a terminal of `width` columns would.

    `static.content` is the exact object passed to the constructor — a flat
    markup string under the old renderer, a `rich.table.Table` under the fixed
    one. `Console.print` handles both (including the old string's inline
    markup), so this works unchanged against either implementation.
    """
    console = Console(width=width, record=True)
    console.print(static.content)
    return console.export_text()


@pytest.mark.asyncio
async def test_question_mark_opens_the_overlay(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()  # off the search Input
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, KeymapScreen)


@pytest.mark.asyncio
async def test_question_mark_opens_the_overlay_from_a_focused_search_input(
    workbench_xml: str,
) -> None:
    # Regression: the app launches with focus in #search, and a focused Input
    # swallows plain printable keys — including '?', the only discovery
    # surface for the other ~22 hidden hotkeys. '?' must be a priority
    # binding so it reaches the app before the Input consumes it. Don't focus
    # #results first; the point is that this works from the launch state.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        search = app.query_one("#search", Input)
        assert search.has_focus  # guard: prove we're testing the real launch state
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, KeymapScreen)


@pytest.mark.asyncio
async def test_question_mark_does_not_leak_into_the_search_input(workbench_xml: str) -> None:
    # Proves the priority binding *intercepts* the key rather than both the
    # binding and the Input's default character-insertion handler firing.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        search = app.query_one("#search", Input)
        assert search.has_focus
        await pilot.press("question_mark")
        await pilot.pause()
        assert search.value == ""


@pytest.mark.asyncio
async def test_q_is_still_swallowed_by_a_focused_search_input(workbench_xml: str) -> None:
    # Deliberate asymmetry: unlike '?', 'q' must stay a normal (non-priority)
    # binding so a search for an object name containing "q" still works from
    # the launch state. Guards against someone later "fixing" q to match ?.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        search = app.query_one("#search", Input)
        assert search.has_focus
        await pilot.press("q")
        await pilot.pause()
        assert search.value == "q"
        assert app.is_running  # did not quit


@pytest.mark.asyncio
async def test_overlay_lists_every_command_with_its_description(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("question_mark")
        await pilot.pause()
        static = app.screen.query_one("#keymap-body", Static)
        # Wide enough that nothing wraps, so every description is one substring.
        body = _rendered_text(static, width=200)
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
        static = app.screen.query_one("#keymap-body", Static)
        body = _rendered_text(static, width=200)
        for category in ("Navigate", "Objects", "Analyze", "Names", "Session"):
            assert category in body


@pytest.mark.asyncio
async def test_overlay_shows_a_dismiss_hint(workbench_xml: str) -> None:
    # The Footer's `? keys` entry is disabled while the overlay is up (spoke-
    # stacking guard), so the card itself must say what closes it.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("question_mark")
        await pilot.pause()
        static = app.screen.query_one("#keymap-body", Static)
        body = _rendered_text(static, width=200)
        assert DISMISS_HINT in body


@pytest.mark.asyncio
async def test_no_description_line_wraps_to_the_left_margin(workbench_xml: str) -> None:
    """Regression: the old flat-string renderer let a Static soft-wrap one long
    `\\n`-joined blob, so a wrapped description's continuation line landed at
    column 0 — under the key column — instead of hanging under the description
    text. Every non-blank rendered line must be indented, except the category
    header and the dismiss-hint lines, which are legitimately flush-left.
    """
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("question_mark")
        await pilot.pause()
        static = app.screen.query_one("#keymap-body", Static)
        # ~66 cols: the card's interior width on a realistic 100-col terminal.
        body = _rendered_text(static, width=66)
        allowed_flush_left = set(CATEGORIES) | {DISMISS_HINT}
        for line in body.splitlines():
            if not line.strip():
                continue
            if line == line.lstrip() and line.strip() not in allowed_flush_left:
                pytest.fail(f"line wrapped to the left margin: {line!r}")


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
