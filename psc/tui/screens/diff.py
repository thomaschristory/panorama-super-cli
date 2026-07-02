"""Diff spoke: drift between two device-groups (read-only).

Mirrors `psc diff --device-group A --against B`. Only the device-group-vs-
device-group mode fits a single-session TUI — file-vs-file needs a second config
loaded, which the workbench has no place for. Each side is resolved to its
*effective visible* object set (its own objects plus everything inherited from
shared / ancestor device-groups) and compared by bare name, exactly like the CLI.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Select, Static

from psc.core.diff import diff_snapshots
from psc.core.models import Location
from psc.tui.session import WorkbenchSession

# (display kind, SnapshotDiff attribute) in report order.
_KIND_ATTRS = (
    ("address", "addresses"),
    ("address-group", "address_groups"),
    ("service", "services"),
    ("service-group", "service_groups"),
    ("tag", "tags"),
    ("security-rule", "security_rules"),
    ("nat-rule", "nat_rules"),
)

# A DG-vs-DG diff needs at least two scopes to choose between.
_MIN_SCOPES = 2


def location_names(session: WorkbenchSession) -> list[str]:
    """Selectable diff scopes: `shared` plus every device-group, sorted."""
    return ["shared", *sorted(session.working_snapshot.device_groups)]


def _location(name: str) -> Location:
    return Location.shared() if name == "shared" else Location.dg(name)


def diff_rows(session: WorkbenchSession, base: str, other: str) -> list[tuple[str, str, str, str]]:
    """Flatten a DG-vs-DG diff into (kind, change, name, detail) rows.

    `change` is added / removed / changed relative to `base` -> `other`: an
    object visible in `other` but not `base` is *added*, the reverse is
    *removed*, and one visible in both with a differing definition is *changed*
    (detail lists the differing fields).
    """
    snap = session.working_snapshot
    d = diff_snapshots(snap, snap, scope_base=_location(base), scope_other=_location(other))
    rows: list[tuple[str, str, str, str]] = []
    for kind, attr in _KIND_ATTRS:
        kd = getattr(d, attr)
        rows.extend((kind, "added", o.name, "") for o in kd.added)
        rows.extend((kind, "removed", o.name, "") for o in kd.removed)
        rows.extend((kind, "changed", c.name, ", ".join(c.changed_fields)) for c in kd.changed)
    return rows


class DiffScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._locations = location_names(session)

    def compose(self) -> ComposeResult:
        if len(self._locations) < _MIN_SCOPES:
            yield Static(
                "Diff needs at least two scopes (shared + a device-group).",
                id="diff-empty",
            )
            yield Footer()
            return
        base, other = self._locations[0], self._locations[1]
        yield Static("Compare a base scope against another (added/removed/changed):")
        yield Select(
            [(n, n) for n in self._locations], value=base, allow_blank=False, id="diff-base"
        )
        yield Select(
            [(n, n) for n in self._locations], value=other, allow_blank=False, id="diff-other"
        )
        table: DataTable[str] = DataTable(id="diff-table")
        yield table
        yield Static("", id="diff-note")
        yield Footer()

    def on_mount(self) -> None:
        if len(self._locations) < _MIN_SCOPES:
            return
        table = self.query_one("#diff-table", DataTable)
        table.add_columns("kind", "change", "name", "detail")
        self._render_diff()

    def _selected(self, widget_id: str) -> str:
        value = self.query_one(widget_id, Select).value
        return value if isinstance(value, str) else self._locations[0]

    def _render_diff(self) -> None:
        base, other = self._selected("#diff-base"), self._selected("#diff-other")
        table = self.query_one("#diff-table", DataTable)
        table.clear()
        rows = diff_rows(self.session, base, other)
        for kind, change, name, detail in rows:
            table.add_row(kind, change, name, detail)
        note = "" if rows else f"No differences between {base} and {other}."
        self.query_one("#diff-note", Static).update(note)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in ("diff-base", "diff-other"):
            self._render_diff()
