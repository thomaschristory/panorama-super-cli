"""Usage spoke: where-used for the selected objects (read-only, never stages)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from psc.core.models import Location
from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession


def _loc(name: str) -> Location:
    return Location.shared() if name == "shared" else Location.dg(name)


@dataclass(frozen=True)
class UsageRow:
    object_kind: str
    object_name: str
    object_location: str
    referrer_kind: str
    referrer_name: str
    referrer_location: str
    field: str


def selection_where_used(session: WorkbenchSession) -> list[UsageRow]:
    """Every reference to each selected object, across the working snapshot."""
    if not session.selection:
        return []
    graph = ReferenceGraph.build(session.working_snapshot)
    rows: list[UsageRow] = []
    for item in session.selection:
        for ref in graph.where_used(item.kind, item.name, _loc(item.location)):
            rows.append(
                UsageRow(
                    object_kind=item.kind,
                    object_name=item.name,
                    object_location=item.location,
                    referrer_kind=ref.referrer_kind,
                    referrer_name=ref.referrer_name,
                    referrer_location=ref.referrer_location.name,
                    field=ref.field,
                )
            )
    return rows


class UsageScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self._rows = selection_where_used(session)

    def compose(self) -> ComposeResult:
        if not self._rows:
            yield Static("No references to the selected objects.", id="usage-empty")
        else:
            table: DataTable[str] = DataTable(id="usage-table")
            yield table
        yield Footer()

    def on_mount(self) -> None:
        if self._rows:
            table = self.query_one("#usage-table", DataTable)
            table.add_columns("kind", "object", "referrer kind", "referrer", "location", "field")
            for r in self._rows:
                table.add_row(
                    r.object_kind,
                    r.object_name,
                    r.referrer_kind,
                    r.referrer_name,
                    r.referrer_location,
                    r.field,
                )
