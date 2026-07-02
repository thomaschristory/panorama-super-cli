"""Export spoke: write objects of one kind to an NDJSON file (read-only).

Mirrors `psc export <kind>`: a portable, one-object-per-line dump ordered by
(location, name). It is an *export*, not a config write — it never stages or
commits anything, and (like every workbench file write) it refuses to overwrite
an offline source export.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Input, Select, Static

from psc.core.changeset import ObjectKind
from psc.output.errors import PscError
from psc.tui.session import WorkbenchSession

# Display label -> engine kind, matching the CLI `export` kind names.
_KINDS: dict[str, ObjectKind] = {
    "addresses": ObjectKind.ADDRESS,
    "address-groups": ObjectKind.ADDRESS_GROUP,
    "services": ObjectKind.SERVICE,
    "service-groups": ObjectKind.SERVICE_GROUP,
    "tags": ObjectKind.TAG,
}


class ExportScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+s", "export", "write file"),
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        yield Static("Export objects to NDJSON (ctrl+s writes the file):")
        yield Select(
            [(label, label) for label in _KINDS],
            value="addresses",
            allow_blank=False,
            id="export-kind",
        )
        yield Input(placeholder="destination path (e.g. addresses.ndjson)", id="export-path")
        yield Static("", id="export-status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#export-path", Input).focus()

    def _status(self, message: str) -> None:
        self.query_one("#export-status", Static).update(message)

    def action_export(self) -> None:
        label = self.query_one("#export-kind", Select).value
        kind = _KINDS[label] if isinstance(label, str) else ObjectKind.ADDRESS
        path = self.query_one("#export-path", Input).value.strip()
        if not path:
            self._status("[red]a destination path is required[/red]")
            self.app.bell()
            return
        try:
            count = self.session.export_kind(kind, path)
        except PscError as exc:
            self._status(f"[red]{exc}[/red]")
            self.app.bell()
            return
        self._status(f"wrote {count} object(s) to {path}")
