"""Audit spoke: address overlaps or services-vs-well-known ports (read-only).

Two modes, switched by a picker:

- **overlaps** — address overlap/containment involving the *selection* (mirrors
  `audit overlaps`, scoped to the selected addresses).
- **well-known** — custom services whose single destination port duplicates a
  predefined PAN-OS service or an IANA well-known port (mirrors
  `audit services-vs-wellknown`, a config-wide scan that needs no selection).
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Select, Static

from psc.core.audit import (
    OverlapPair,
    find_overlapping_addresses,
    find_wellknown_duplicate_services,
)
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
        self.session = session

    def compose(self) -> ComposeResult:
        yield Static("Audit mode:")
        yield Select(
            [
                ("address overlaps (selection)", "overlaps"),
                ("services vs well-known ports", "wellknown"),
            ],
            value="overlaps",
            allow_blank=False,
            id="audit-mode",
        )
        table: DataTable[str] = DataTable(id="audit-table")
        yield table
        yield Static("", id="audit-note")
        yield Footer()

    def on_mount(self) -> None:
        self._render_mode("overlaps")

    def _render_mode(self, mode: str) -> None:
        table = self.query_one("#audit-table", DataTable)
        # Clear columns too: the two modes have different column sets.
        table.clear(columns=True)
        note = self.query_one("#audit-note", Static)
        if mode == "wellknown":
            matches = find_wellknown_duplicate_services(self.session.working_snapshot)
            table.add_columns("service", "location", "port", "duplicates", "kind")
            for m in matches:
                table.add_row(
                    m.service_name,
                    m.service_location,
                    f"{m.protocol}/{m.port}",
                    m.canonical_name,
                    m.kind.value,
                )
            note.update(
                "" if matches else "No custom services duplicate a well-known/predefined port."
            )
            return
        pairs = selection_overlaps(self.session)
        table.add_columns("relationship", "left", "left value", "right", "right value")
        for p in pairs:
            table.add_row(
                p.relationship.value, p.left_name, p.left_value, p.right_name, p.right_value
            )
        note.update("" if pairs else "No overlaps involving the selected addresses.")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "audit-mode" and isinstance(event.value, str):
            self._render_mode(event.value)
