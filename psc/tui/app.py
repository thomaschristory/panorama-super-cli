"""WorkbenchApp + HubScreen — the Textual frontend over WorkbenchSession."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Input, Static

from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem

_TCSS = str(Path(__file__).with_name("workbench.tcss"))


class HubScreen(Widget):
    """The home layout container (a plain Widget, not a leaf Static)."""

    def compose(self) -> ComposeResult:
        yield Input(placeholder="search: IP / value / name", id="search")
        with Horizontal():
            yield DataTable(id="results")
            with Vertical():
                yield DataTable(id="selection")
                yield Static("staged (0)", id="staging")


class WorkbenchApp(App[None]):
    CSS_PATH = _TCSS
    TITLE = "psc workbench"
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("space", "toggle_row", "select"),
        ("d", "dedup", "dedup"),
        ("u", "usage", "usage"),
        ("ctrl+a", "apply_batch", "apply"),
        ("q", "quit", "quit"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        # The rows currently shown in #results, parallel to the table rows.
        self._results: list[SelectionItem] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield HubScreen()
        yield Footer()

    def on_mount(self) -> None:
        results = self.query_one("#results", DataTable)
        results.add_columns("kind", "name", "location")
        results.cursor_type = "row"
        sel = self.query_one("#selection", DataTable)
        sel.add_columns("kind", "name", "location")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search":
            return
        self._results = self.session.search(event.value)
        table = self.query_one("#results", DataTable)
        table.clear()
        for item in self._results:
            table.add_row(item.kind, item.name, item.location)

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

    def action_dedup(self) -> None:
        from psc.tui.screens.dedup import DedupScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(DedupScreen(self.session))

    def action_usage(self) -> None:
        from psc.tui.screens.usage import UsageScreen  # noqa: PLC0415 — avoid import cycle

        self.push_screen(UsageScreen(self.session))

    def action_apply_batch(self) -> None:
        out_path = self.session.apply_out_path
        try:
            outcome = self.session.apply_batch(out_path=out_path)
        except Exception as exc:
            # Surface any apply failure in the staging strip rather than letting
            # it crash the app — PscError (missing out_path / blocked) for
            # offline, plus arbitrary transport errors from a live push.
            self.query_one("#staging", Static).update(f"[red]apply failed: {exc}[/red]")
            self.bell()
            return
        first_line = outcome.detail.splitlines()[0] if outcome.detail else ""
        self.query_one("#staging", Static).update(f"applied {outcome.ops} change(s) — {first_line}")
