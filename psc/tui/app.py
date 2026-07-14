"""WorkbenchApp + HubScreen — the Textual frontend over WorkbenchSession."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Input, Static

from psc.tui.commands import bindings, hub_actions
from psc.tui.session import WorkbenchSession, render_value
from psc.tui.state import SelectionItem

_TCSS = str(Path(__file__).with_name("workbench.tcss"))


class HubScreen(Widget):
    """The home layout container (a plain Widget, not a leaf Static).

    Search and the staged strip share the top row; the two tables stack
    vertically below so the results table — the widest content in the app — gets
    the full terminal width instead of half of it.
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Input(placeholder="search: IP / value / name", id="search")
            yield Static("staged (0)", id="staging")
        with Vertical(id="panes"):
            yield DataTable(id="results")
            yield DataTable(id="selection")


class WorkbenchApp(App[None]):
    CSS_PATH = _TCSS
    TITLE = "psc workbench"

    # Keys, labels and descriptions all live in psc/tui/commands.py — see there
    # to add a spoke. Only FOOTER_KEYS are shown; `?` lists the rest.
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = list(bindings())

    # Hub-only bindings — disabled while any spoke screen is on top of the
    # stack, so a spoke key can't stack a second spoke over the first (which
    # would let the first spoke's plan go stale and corrupt the config on
    # confirm). Spokes have their own ctrl+y/escape bindings.
    _HUB_ACTIONS: ClassVar[frozenset[str]] = hub_actions()

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        # The rows currently shown in #results, parallel to the table rows.
        self._results: list[SelectionItem] = []

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # A spoke screen is pushed on top of the hub (stack depth > 1). While one
        # is active, the hub bindings are inert — you must finish/cancel the
        # spoke first. This is the guard that prevents cross-spoke plan staleness.
        return not (action in self._HUB_ACTIONS and len(self.screen_stack) > 1)

    def compose(self) -> ComposeResult:
        yield Header()
        yield HubScreen()
        yield Footer()

    def on_mount(self) -> None:
        results = self.query_one("#results", DataTable)
        results.add_columns("kind", "name", "location", "value")
        results.cursor_type = "row"
        sel = self.query_one("#selection", DataTable)
        sel.add_columns("kind", "name", "location")
        # Focusable + row cursor so a single selected item can be dropped
        # directly from the selection panel (delete/backspace), #91.
        sel.cursor_type = "row"

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search":
            return
        self._results = self.session.search(event.value)
        table = self.query_one("#results", DataTable)
        table.clear()
        snapshot = self.session.working_snapshot
        for item in self._results:
            table.add_row(item.kind, item.name, item.location, render_value(snapshot, item))

    def _reload_view(self) -> None:
        """Reset the hub after the session is pointed at a new source (#121):
        the old search results reference the previous config, so clear them and
        resync the (now empty) selection + staging strip."""
        self._results = []
        self.query_one("#results", DataTable).clear()
        # The old query no longer matches the new config; clear it so the empty
        # results table isn't misread as "no matches for this search".
        self.query_one("#search", Input).value = ""
        self._refresh_selection_view()

    def _refresh_selection_view(self) -> None:
        sel = self.query_one("#selection", DataTable)
        sel.clear()
        for i in self.session.selection:
            sel.add_row(i.kind, i.name, i.location)
        self.query_one("#staging", Static).update(f"staged ({len(self.session.staging)})")

    def action_toggle_row(self) -> None:
        table = self.query_one("#results", DataTable)
        if not self._results:
            return
        row = table.cursor_row
        if row >= len(self._results):
            return
        self.session.toggle(self._results[row])
        self._refresh_selection_view()

    def action_inspect(self) -> None:
        # 'Open' the focused results row read-only: show its member tree and
        # effective leaf set. Acts on the cursor row (like toggle_row), not the
        # selection, so you can inspect without first selecting.
        from psc.tui.screens.inspect import InspectScreen  # noqa: PLC0415 — avoid cycle

        table = self.query_one("#results", DataTable)
        if not self._results:
            return
        row = table.cursor_row
        if row >= len(self._results):
            return
        self.push_screen(InspectScreen(self.session, self._results[row]))

    def action_remove_selected(self) -> None:
        # Drop the focused row directly from the selection panel (#91). Only acts
        # when the selection table is focused, so delete/backspace elsewhere is a
        # no-op rather than removing a surprise item. The selection rows are built
        # in `session.selection` order, so cursor_row indexes it directly.
        sel = self.query_one("#selection", DataTable)
        if self.focused is not sel or not self.session.selection:
            return
        if self.session.remove_at(sel.cursor_row):
            self._refresh_selection_view()

    def action_create(self) -> None:
        from psc.tui.screens.create import CreateScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(CreateScreen(self.session))

    def action_dedup(self) -> None:
        from psc.tui.screens.dedup import DedupScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(DedupScreen(self.session))

    def action_duplicates(self) -> None:
        from psc.tui.screens.duplicates import DuplicatesScreen  # noqa: PLC0415 — avoid cycle

        self.push_screen(DuplicatesScreen(self.session))

    def action_usage(self) -> None:
        from psc.tui.screens.usage import UsageScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(UsageScreen(self.session))

    def action_audit(self) -> None:
        from psc.tui.screens.audit import AuditScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(AuditScreen(self.session))

    def action_diff(self) -> None:
        from psc.tui.screens.diff import DiffScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(DiffScreen(self.session))

    def action_export(self) -> None:
        from psc.tui.screens.export import ExportScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(ExportScreen(self.session))

    def action_move(self) -> None:
        from psc.tui.screens.move import MoveScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(MoveScreen(self.session))

    def action_decommission(self) -> None:
        from psc.tui.screens.decommission import DecommissionScreen  # noqa: PLC0415 — avoid cycle

        self.push_screen(DecommissionScreen(self.session))

    def action_rename(self) -> None:
        from psc.tui.screens.rename import RenameScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(RenameScreen(self.session))

    def action_rule_edit(self) -> None:
        from psc.tui.screens.rule import RuleScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(RuleScreen(self.session))

    def action_group_add(self) -> None:
        from psc.tui.screens.group import GroupScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(GroupScreen(self.session))

    def action_group_new(self) -> None:
        from psc.tui.screens.group_new import NewGroupScreen  # noqa: PLC0415 — avoid cycle

        self.push_screen(NewGroupScreen(self.session))

    def action_unused(self) -> None:
        from psc.tui.screens.unused import UnusedScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(UnusedScreen(self.session))

    def action_dangling(self) -> None:
        from psc.tui.screens.dangling import DanglingScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(DanglingScreen(self.session))

    def action_name_lint(self) -> None:
        from psc.tui.screens.lint import LintScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(LintScreen(self.session))

    def action_name_apply(self) -> None:
        from psc.tui.screens.name_apply import NameApplyScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(NameApplyScreen(self.session))

    def action_profiles(self) -> None:
        from psc.tui.screens.profiles import ProfilesScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(ProfilesScreen(self.session))

    def action_staged(self) -> None:
        from psc.tui.screens.staged import StagedScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(StagedScreen(self.session))
