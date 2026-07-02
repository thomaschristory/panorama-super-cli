"""Dangling spoke: references that resolve to no object (read-only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession


@dataclass(frozen=True)
class DanglingRow:
    referrer_kind: str
    referrer_name: str
    referrer_location: str
    field: str
    target_name: str


def dangling_rows(session: WorkbenchSession) -> list[DanglingRow]:
    """Every reference whose named target resolves to no object."""
    graph = ReferenceGraph.build(session.working_snapshot)
    rows = [
        DanglingRow(
            referrer_kind=r.referrer_kind,
            referrer_name=r.referrer_name,
            referrer_location=r.referrer_location.name,
            field=r.field,
            target_name=r.target_name,
        )
        for r in graph.dangling()
    ]
    return sorted(rows, key=lambda r: (r.referrer_location, r.referrer_name, r.target_name))


class DanglingScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self._rows = dangling_rows(session)

    def compose(self) -> ComposeResult:
        if not self._rows:
            yield Static("No dangling references.", id="dangling-empty")
        else:
            table: DataTable[str] = DataTable(id="dangling-table")
            yield table
        yield Footer()

    def on_mount(self) -> None:
        if self._rows:
            table = self.query_one("#dangling-table", DataTable)
            table.add_columns("referrer kind", "referrer", "location", "field", "missing target")
            for r in self._rows:
                table.add_row(
                    r.referrer_kind,
                    r.referrer_name,
                    r.referrer_location,
                    r.field,
                    r.target_name,
                )
