"""Duplicates spoke: config-wide duplicate scan (read-only).

Mirrors the CLI `dedup addresses` / `dedup services` / `dedup groups` discovery
subcommands, which the selection-scoped `d` dedup spoke never covered — that
one only groups the *current selection*. A kind toggle switches between the
three buckets. This spoke is pure discovery (like its CLI counterparts); the
actual merge stays in the `d` spoke / `dedup merge` CLI, so nothing here mutates
the config.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Select, Static

from psc.core.dedup import (
    DuplicateGroup,
    find_duplicate_addresses,
    find_duplicate_groups,
    find_duplicate_services,
)
from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession

_KINDS = ("address", "service", "address-group")


def duplicate_buckets(session: WorkbenchSession, kind: str) -> list[DuplicateGroup]:
    """Every config-wide duplicate bucket (2+ members) for `kind`.

    Reuses the same engines as the CLI: `find_duplicate_addresses` (strict, the
    CLI default), `find_duplicate_services`, and `find_duplicate_groups` (bucketed
    by effective leaf-address set; dynamic/unresolvable groups are skipped by the
    engine). An unknown kind yields no buckets.
    """
    snap = session.working_snapshot
    if kind == "address":
        return find_duplicate_addresses(snap)
    if kind == "service":
        return find_duplicate_services(snap)
    if kind == "address-group":
        return find_duplicate_groups(snap, ReferenceGraph.build(snap)).buckets
    return []


class DuplicatesScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        yield Static("Config-wide duplicate scan (read-only). Kind:")
        yield Select([(k, k) for k in _KINDS], value="address", allow_blank=False, id="dup-kind")
        table: DataTable[str] = DataTable(id="dup-table")
        yield table
        yield Static("", id="dup-note")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#dup-table", DataTable).add_columns("value", "count", "members")
        self._render_kind("address")

    def _render_kind(self, kind: str) -> None:
        table = self.query_one("#dup-table", DataTable)
        table.clear()
        buckets = duplicate_buckets(self.session, kind)
        for b in buckets:
            members = ", ".join(f"{m.name}@{m.location}" for m in b.members)
            table.add_row(b.value, str(b.count), members)
        note = "" if buckets else f"No duplicate {kind} objects in the config."
        self.query_one("#dup-note", Static).update(note)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "dup-kind" and isinstance(event.value, str):
            self._render_kind(event.value)
