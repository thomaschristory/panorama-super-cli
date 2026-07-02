"""Apply spoke: choose the output format + destination at apply time (#122).

Historically the workbench's output disposition was fixed at launch by
`--output-mode` / `--apply-out`. That forced the operator to decide *how* to emit
a batch before seeing what it contained. This screen moves the choice to apply
time: `ctrl+a` opens it, the launch flags become the pre-selected default, and
the operator picks a disposition (and destination) here instead.

The engine already supports every disposition — this screen only sets
`session.output_mode` / `offline_partial` / the out-path and calls the existing
`apply_batch`. The safety model is unchanged: blockers still hard-gate, offline
never overwrites the source export, live never commits. A live push and an
overwrite of an existing file each need an explicit second confirmation.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.content import Content
from textual.screen import Screen
from textual.widgets import Footer, Input, Select, Static

from psc.core.source import LiveSource
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

# disposition value -> (output_mode, offline_partial, needs a destination path)
_MODES: dict[str, tuple[OutputMode, bool, bool]] = {
    "set-preview": (OutputMode.SET, False, False),
    "set-file": (OutputMode.SET, False, True),
    "offline-full": (OutputMode.OFFLINE_APPLY, False, True),
    "offline-partial": (OutputMode.OFFLINE_APPLY, True, True),
    "live-push": (OutputMode.LIVE_APPLY, False, False),
}

_LABELS: list[tuple[str, str]] = [
    ("Print the set script here", "set-preview"),
    ("Save a set-command file (.set)", "set-file"),
    ("Save a full XML config", "offline-full"),
    ("Save a minimal partial XML config", "offline-partial"),
    ("Push to the live candidate (never commits)", "live-push"),
]


def initial_disposition(session: WorkbenchSession) -> str:
    """The disposition pre-selected from the launch flags (`--output-mode` etc.)."""
    if session.output_mode is OutputMode.LIVE_APPLY:
        return "live-push"
    if session.output_mode is OutputMode.OFFLINE_APPLY:
        return "offline-partial" if session.offline_partial else "offline-full"
    return "set-file" if session.apply_out_path else "set-preview"


class ApplyScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+a", "apply", "apply"),
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._live = isinstance(session.source, LiveSource)
        # Two-step confirmation latch for a live push / overwriting an existing
        # file; reset whenever the disposition or path changes.
        self._armed = False

    def compose(self) -> ComposeResult:
        # Offer the live option only when the session is backed by a live profile.
        options = [(label, value) for label, value in _LABELS if value != "live-push" or self._live]
        default = initial_disposition(self.session)
        if default == "live-push" and not self._live:
            default = "set-preview"
        n = len(self.session.staging)
        labels = "; ".join(s.label for s in self.session.staging)
        yield Static(f"Apply {n} staged change(s): {labels}" if n else "Nothing staged.")
        yield Static("Output format + destination (ctrl+a to apply):")
        yield Select(options, value=default, allow_blank=False, id="apply-mode")
        yield Input(
            value=self.session.apply_out_path or "",
            placeholder="destination path (for the file / config options)",
            id="apply-path",
        )
        yield Static("", id="apply-status")
        yield Footer()

    def _status(self, message: str) -> None:
        self.query_one("#apply-status", Static).update(message)

    def _disposition(self) -> str:
        value = self.query_one("#apply-mode", Select).value
        return value if isinstance(value, str) else "set-preview"

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "apply-mode":
            self._armed = False  # a changed choice must be re-confirmed

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "apply-path":
            self._armed = False

    def action_apply(self) -> None:
        if not self.session.staging:
            self._status("[red]nothing staged[/red]")
            self.app.bell()
            return
        disposition = self._disposition()
        output_mode, offline_partial, needs_path = _MODES[disposition]
        raw_path = self.query_one("#apply-path", Input).value.strip()
        path = raw_path or None
        if needs_path and path is None:
            self._status("[red]this option needs a destination path[/red]")
            self.app.bell()
            return

        # Second-confirmation gate for the irreversible / clobbering choices.
        confirm = None
        if disposition == "live-push":
            confirm = "push to the LIVE candidate"
        elif needs_path and path is not None and Path(path).exists():
            confirm = f"overwrite {path}"
        if confirm is not None and not self._armed:
            self._armed = True
            self._status(f"[yellow]{confirm} — press ctrl+a again to confirm[/yellow]")
            return

        self.session.output_mode = output_mode
        self.session.offline_partial = offline_partial
        try:
            outcome = self.session.apply_batch(out_path=path if needs_path else None)
        except Exception as exc:
            self._armed = False
            self._status(f"[red]apply failed: {exc}[/red]")
            self.app.bell()
            return
        self._armed = False
        # OFFLINE/LIVE apply clears staging; keep the hub's counter in sync.
        cast("WorkbenchApp", self.app)._refresh_selection_view()
        # For a SET preview `detail` is the whole script; for the rest it's a
        # one-line summary. Render as plain Content so the set-script `[ ... ]`
        # member lists aren't eaten by Textual's markup engine (#129).
        self.query_one("#apply-status", Static).update(
            Content(outcome.detail or f"applied {outcome.ops} change(s)")
        )
