"""Audit spoke: address overlaps/containment involving the selection (read-only)."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from psc.core.audit import OverlapPair, find_overlapping_addresses
from psc.tui.session import WorkbenchSession


def selection_overlaps(session: WorkbenchSession) -> list[OverlapPair]:
    """Overlap/containment pairs where at least one side is a selected address."""
    selected = {(i.location, i.name) for i in session.selected_of_kinds({"address"})}
    if not selected:
        return []
    # Unscoped scan over the whole snapshot (find_overlapping_addresses accepts
    # a scope=Location; a future optimisation could narrow to the selection's DG
    # for large configs).
    pairs = find_overlapping_addresses(session.working_snapshot)
    return [
        p
        for p in pairs
        if (p.left_location, p.left_name) in selected
        or (p.right_location, p.right_name) in selected
    ]


class AuditScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self._pairs = selection_overlaps(session)

    def compose(self) -> ComposeResult:
        if not self._pairs:
            yield Static("No overlaps involving the selected addresses.", id="audit-empty")
        else:
            table: DataTable[str] = DataTable(id="audit-table")
            yield table
        yield Footer()

    def on_mount(self) -> None:
        if self._pairs:
            table = self.query_one("#audit-table", DataTable)
            table.add_columns("relationship", "left", "left value", "right", "right value")
            for p in self._pairs:
                table.add_row(
                    p.relationship.value, p.left_name, p.left_value, p.right_name, p.right_value
                )
