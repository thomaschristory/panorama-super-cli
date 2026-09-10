"""Unused spoke: objects of a chosen kind that no rule reaches.

The spoke lists candidates and pushes them into the workbench selection buffer.
It never deletes anything itself. The delete spoke (`X`) plans the removal, so
the operator always sees a plan between the candidate list and the change.

`space` sends the row under the cursor to the selection. `a` sends every listed
row. The `tags` column carries the issue #180 signal: a tag-bearing candidate
can join a dynamic address-group at runtime, so it is the row to check first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Select, Static

from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

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

_HINT = "space — send row to selection   a — send all   X (hub) — plan the delete"


@dataclass(frozen=True)
class UnusedRow:
    kind: str
    name: str
    location: str
    tags: list[str] = field(default_factory=list)

    @property
    def item(self) -> SelectionItem:
        return SelectionItem(kind=self.kind, name=self.name, location=self.location)


def unused_rows(session: WorkbenchSession, kind: str) -> list[UnusedRow]:
    """Objects of `kind` no rule reaches (recursively), as sortable rows.

    Each row carries the object's tags. A tag can put an address into a dynamic
    address-group at runtime, which `unused` cannot see, so the operator needs
    the tags at the moment they choose what to delete (issue #180).
    """
    graph = ReferenceGraph.build(session.working_snapshot)
    rows = [
        UnusedRow(kind=t.kind, name=t.name, location=t.location.name, tags=graph.tags_for(t))
        for t in graph.unused(kind)
    ]
    return sorted(rows, key=lambda r: (r.location, r.name))


def add_rows_to_selection(session: WorkbenchSession, rows: list[UnusedRow]) -> int:
    """Add `rows` to the session selection. Returns how many were new."""
    return sum(1 for r in rows if session.add(r.item))


class UnusedScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("space", "select_row", "→ selection"),
        ("a", "select_all", "all → selection"),
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._rows: list[UnusedRow] = []

    def compose(self) -> ComposeResult:
        yield Static("Show unused objects of kind:")
        yield Select([(k, k) for k in _KINDS], value="address", allow_blank=False, id="unused-kind")
        yield Static(_CAVEAT, id="unused-caveat")
        table: DataTable[str] = DataTable(id="unused-table")
        yield table
        yield Static(_HINT, id="unused-note")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#unused-table", DataTable)
        table.add_columns("kind", "name", "location", "tags")
        table.cursor_type = "row"  # highlight whole candidates, not single cells
        self._render_kind("address")
        # Focus the table so arrows walk the rows and `space` reaches the screen
        # binding instead of opening the kind Select.
        table.focus()

    def _render_kind(self, kind: str) -> None:
        table = self.query_one("#unused-table", DataTable)
        table.clear()
        self._rows = unused_rows(self.session, kind)
        for r in self._rows:
            table.add_row(r.kind, r.name, r.location, ", ".join(r.tags))

    def _note(self, text: str) -> None:
        self.query_one("#unused-note", Static).update(text)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "unused-kind" and isinstance(event.value, str):
            self._render_kind(event.value)
            self._note(_HINT)

    def action_select_row(self) -> None:
        if not self._rows:
            return
        row = self.query_one("#unused-table", DataTable).cursor_row
        if not 0 <= row < len(self._rows):
            return
        candidate = self._rows[row]
        added = add_rows_to_selection(self.session, [candidate])
        state = "sent to selection" if added else "already selected"
        self._note(f"{candidate.name} — {state}")
        cast("WorkbenchApp", self.app)._refresh_selection_view()

    def action_select_all(self) -> None:
        if not self._rows:
            return
        added = add_rows_to_selection(self.session, self._rows)
        total = len(self._rows)
        note = f"sent {added} of {total} to selection"
        if added < total:
            note += f" ({total - added} already selected)"
        self._note(note)
        cast("WorkbenchApp", self.app)._refresh_selection_view()
