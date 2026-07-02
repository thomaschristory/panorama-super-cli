"""Lint spoke: naming-template drift for the default scheme (read-only)."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from psc.core.naming import NameFinding, NamingScheme, lint
from psc.tui.session import WorkbenchSession


def lint_rows(session: WorkbenchSession) -> list[NameFinding]:
    """Non-compliant naming findings under the default scheme (the drift)."""
    findings = lint(session.working_snapshot, NamingScheme())
    return [f for f in findings if not f.compliant]


class LintScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self._rows = lint_rows(session)

    def compose(self) -> ComposeResult:
        if not self._rows:
            yield Static("All objects comply with the naming scheme.", id="lint-empty")
        else:
            table: DataTable[str] = DataTable(id="lint-table")
            yield table
        yield Footer()

    def on_mount(self) -> None:
        if self._rows:
            table = self.query_one("#lint-table", DataTable)
            table.add_columns("kind", "location", "current", "suggested")
            for f in self._rows:
                table.add_row(f.kind, f.location, f.current, f.suggested)
