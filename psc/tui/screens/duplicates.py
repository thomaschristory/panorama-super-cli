"""Duplicates spoke: config-wide duplicate scan (read-only).

Mirrors the CLI `dedup addresses` / `dedup services` / `dedup groups` discovery
subcommands, which the selection-scoped `d` dedup spoke never covered — that
one only groups the *current selection*. A kind toggle switches between the
three buckets. This spoke is pure discovery (like its CLI counterparts); the
actual merge stays in the `d` spoke / `dedup merge` CLI, so nothing here mutates
the config.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

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
from psc.tui.state import SelectionItem

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

_KINDS = ("address", "service", "address-group")


def add_bucket_to_selection(session: WorkbenchSession, bucket: DuplicateGroup) -> int:
    """Add every member of `bucket` to the session selection (idempotent).

    Each member becomes a `SelectionItem` of the *bucket's* kind
    (`address`/`service`/`address-group`), so a scan result lands as the right
    selection kind for downstream spokes. Members already selected are left in
    place — re-sending a bucket never toggles one back off. Returns the count of
    members newly added.
    """
    return sum(
        session.add(SelectionItem(kind=bucket.kind, name=m.name, location=m.location))
        for m in bucket.members
    )


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
        ("space", "select_bucket", "→ selection"),
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        # Buckets currently shown, parallel to the table rows so cursor_row
        # indexes straight into it.
        self._buckets: list[DuplicateGroup] = []

    def compose(self) -> ComposeResult:
        yield Static("Config-wide duplicate scan. space: send bucket to selection. Kind:")
        yield Select([(k, k) for k in _KINDS], value="address", allow_blank=False, id="dup-kind")
        table: DataTable[str] = DataTable(id="dup-table")
        yield table
        yield Static("", id="dup-note")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#dup-table", DataTable)
        table.add_columns("value", "count", "members")
        table.cursor_type = "row"  # highlight whole buckets, not single cells
        self._render_kind("address")
        # Focus the table (not the kind Select) so arrows navigate buckets and
        # `space` reaches the screen binding instead of opening the Select.
        table.focus()

    def _render_kind(self, kind: str) -> None:
        table = self.query_one("#dup-table", DataTable)
        table.clear()
        buckets = duplicate_buckets(self.session, kind)
        self._buckets = buckets
        for b in buckets:
            members = ", ".join(f"{m.name}@{m.location}" for m in b.members)
            table.add_row(b.value, str(b.count), members)
        note = "" if buckets else f"No duplicate {kind} objects in the config."
        self.query_one("#dup-note", Static).update(note)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "dup-kind" and isinstance(event.value, str):
            self._render_kind(event.value)

    def action_select_bucket(self) -> None:
        # Push the highlighted bucket's members onto the hub selection so a
        # downstream spoke (d merge, a audit, x decommission, …) can act on them.
        if not self._buckets:
            return
        row = self.query_one("#dup-table", DataTable).cursor_row
        if row is None or not 0 <= row < len(self._buckets):
            return
        bucket = self._buckets[row]
        added = add_bucket_to_selection(self.session, bucket)
        total = len(bucket.members)
        already = total - added
        note = f"sent {added} of {total} to selection"
        if already:
            note += f" ({already} already selected)"
        self.query_one("#dup-note", Static).update(note)
        cast("WorkbenchApp", self.app)._refresh_selection_view()
