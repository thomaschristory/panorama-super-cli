from __future__ import annotations

import pytest
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Input, Static
from textual.widgets._footer import FooterKey

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _app(workbench_xml: str) -> WorkbenchApp:
    sess = WorkbenchSession(source=OfflineSource(workbench_xml), output_mode=OutputMode.SET)
    return WorkbenchApp(sess)


@pytest.mark.asyncio
async def test_search_and_staged_share_the_top_bar(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        topbar = app.query_one("#topbar", Horizontal)
        assert topbar.query_one("#search", Input) is not None
        assert topbar.query_one("#staging", Static) is not None


@pytest.mark.asyncio
async def test_results_and_selection_are_stacked_vertically(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        panes = app.query_one("#panes", Vertical)
        assert panes.query_one("#results", DataTable) is not None
        assert panes.query_one("#selection", DataTable) is not None


@pytest.mark.asyncio
async def test_results_table_spans_the_full_width(workbench_xml: str) -> None:
    # The regression the old side-by-side layout caused: the widest table in the
    # app (kind/name/location/value) only ever got half the terminal.
    app = _app(workbench_xml)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        results = app.query_one("#results", DataTable)
        selection = app.query_one("#selection", DataTable)
        assert results.size.width > 60  # was ~50 side-by-side on an 100-col term
        assert results.size.width == selection.size.width


@pytest.mark.asyncio
async def test_results_pane_is_taller_than_the_selection_pane(workbench_xml: str) -> None:
    # results is the scanning surface (2fr); the selection is usually a few rows (1fr).
    app = _app(workbench_xml)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        results = app.query_one("#results", DataTable)
        selection = app.query_one("#selection", DataTable)
        assert results.size.height > selection.size.height


@pytest.mark.asyncio
async def test_search_is_not_full_terminal_width(workbench_xml: str) -> None:
    # It shares the row with the staged strip now.
    app = _app(workbench_xml)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        search = app.query_one("#search", Input)
        assert search.size.width < 100


@pytest.mark.asyncio
async def test_staged_strip_still_updates(workbench_xml: str) -> None:
    # Moving the strip must not break the counter (it is refreshed by
    # _refresh_selection_view, which queries it by id).
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        strip = app.query_one("#staging", Static)
        assert "staged (0)" in str(strip.render())


@pytest.mark.asyncio
async def test_footer_shows_exactly_three_keys_with_no_duplicates(workbench_xml: str) -> None:
    # Regression for the Footer rendering ctrl+p twice: once from our explicit
    # command-table binding, once from Textual's own built-in command-palette
    # slot (Footer(show_command_palette=...), defaults True).
    #
    # Focus the results table rather than asserting straight off `run_test()`:
    # the search Input has autofocus on mount, and Input.check_consume_key()
    # swallows every plain-character binding (including `?` and `q`) out of
    # the active-bindings set while it's focused, which would hide the real
    # bug behind an unrelated "footer only shows ctrl+p" artifact.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#results", DataTable).focus()
        await pilot.pause()
        pairs = [(fk.key, fk.description) for fk in app.query(FooterKey)]
        assert len(pairs) == 3, pairs
        assert pairs.count(("ctrl+p", "commands")) == 1
        assert set(pairs) == {
            ("question_mark", "keys"),
            ("ctrl+p", "commands"),
            ("q", "quit"),
        }
