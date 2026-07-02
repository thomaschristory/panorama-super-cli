"""Unused spoke: objects of a chosen kind that no rule reaches (read-only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Select, Static

from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession

_KINDS = ("address", "address-group", "service", "service-group", "tag")

# Mirrors the CLI `refs unused` stderr caveat: `unused` only scans device-group
# objects + policy rulebases, so it has blind spots (templates, network/device
# config, externally-registered DAG members). Shown as a Static so the operator
# treats results as candidates, not a delete list.
_CAVEAT = (
    "candidates only — unreferenced by the scanned objects/policy rulebases. "
    "NOT scanned: templates & network/device config, and DAG membership from "
    "externally registered IPs. Verify before deleting (esp. shared)."
)


@dataclass(frozen=True)
class UnusedRow:
    kind: str
    name: str
    location: str


def unused_rows(session: WorkbenchSession, kind: str) -> list[UnusedRow]:
    """Objects of `kind` no rule reaches (recursively), as sortable rows."""
    graph = ReferenceGraph.build(session.working_snapshot)
    rows = [
        UnusedRow(kind=t.kind, name=t.name, location=t.location.name) for t in graph.unused(kind)
    ]
    return sorted(rows, key=lambda r: (r.location, r.name))


class UnusedScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        yield Static("Show unused objects of kind:")
        yield Select([(k, k) for k in _KINDS], value="address", allow_blank=False, id="unused-kind")
        yield Static(_CAVEAT, id="unused-caveat")
        table: DataTable[str] = DataTable(id="unused-table")
        yield table
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#unused-table", DataTable).add_columns("kind", "name", "location")
        self._render_kind("address")

    def _render_kind(self, kind: str) -> None:
        table = self.query_one("#unused-table", DataTable)
        table.clear()
        for r in unused_rows(self.session, kind):
            table.add_row(r.kind, r.name, r.location)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "unused-kind" and isinstance(event.value, str):
            self._render_kind(event.value)
