"""Profiles spoke: view + full CRUD over ~/.psc/config.yaml (issue #83).

The workbench loads its config once at startup; before this, changing a profile
or the default output meant dropping to the shell (`psc profile ...`) or hand-
editing the YAML. This screen lets the operator manage profiles and defaults
without leaving the TUI.

It always operates on the CURRENT ON-DISK config: every action reloads via
`load_config()`, applies one framework-free `psc.tui.profiles` transform, and
persists with `save_config()` (atomic 0600 write, reused verbatim). The API key
Input is masked and never rendered into the table or any status line — the file
stays 0600 and secrets never reach a log/screen cell.

It can also switch the ACTIVE workbench source mid-session (#121): `ctrl+r`
reloads the session onto the focused profile (a live connection) or onto an
offline export path typed in the reload field. That rebuilds the working
snapshot and DISCARDS the selection + staged batch, so it asks for a second
`ctrl+r` to confirm when a batch is staged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Select, Static

from psc.config.loader import load_config, save_config
from psc.config.models import Config, Profile
from psc.core.source import LiveSource, OfflineSource
from psc.output.errors import PscError
from psc.tui.profiles import (
    VALID_OUTPUTS,
    add_or_update_profile,
    remove_profile,
    set_default_output,
    set_default_profile,
)
from psc.tui.session import WorkbenchSession

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp


class ProfilesScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+y", "save_profile", "add/update"),
        ("delete", "remove_profile", "remove"),
        ("backspace", "remove_profile", "remove"),
        ("ctrl+d", "set_default", "set default"),
        ("ctrl+o", "set_output", "set output"),
        ("ctrl+r", "reload_source", "load source"),
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession | None = None) -> None:
        super().__init__()
        # The current on-disk config; every mutation reloads before applying so
        # the screen never persists a stale in-memory view.
        self._config: Config = load_config()
        # The live workbench session, when opened from the hub — needed to switch
        # the active source (#121). None only in isolated/standalone use.
        self.session = session
        # Two-step latch: reloading discards a staged batch, so confirm first.
        self._reload_armed = False

    def compose(self) -> ComposeResult:
        table: DataTable[str] = DataTable(id="profile-table")
        yield table
        yield Static("", id="profile-summary")
        # Add/update form. An existing name upserts (mirrors `psc profile add`).
        yield Input(placeholder="name", id="profile-name")
        yield Input(placeholder="hostname", id="profile-host")
        yield Input(placeholder="api key (stored 0600)", password=True, id="profile-api-key")
        yield Input(placeholder="device-group (optional)", id="profile-dg")
        yield Select(
            [("verify TLS: on", "on"), ("verify TLS: off (insecure)", "off")],
            value="on",
            allow_blank=False,
            id="profile-verify",
        )
        yield Select(
            [(fmt, fmt) for fmt in VALID_OUTPUTS],
            value=self._config.defaults.output
            if self._config.defaults.output in VALID_OUTPUTS
            else "table",
            allow_blank=False,
            id="profile-output",
        )
        yield Input(
            placeholder="offline export path to load (or focus a profile), then ctrl+r",
            id="reload-path",
        )
        yield Static(
            "[ctrl+y] add/update  [del] remove focused  [ctrl+d] set default  "
            "[ctrl+o] set output  [ctrl+r] load source  [esc] back",
            id="profile-status",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#profile-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("name", "hostname", "verify_ssl", "device_group", "default")
        self._refresh()
        self.query_one("#profile-name", Input).focus()

    def _refresh(self) -> None:
        """Rebuild the table + summary from the current (reloaded) config."""
        table = self.query_one("#profile-table", DataTable)
        table.clear()
        for p in self._config.profiles:
            table.add_row(
                p.name,
                p.hostname,
                str(p.verify_ssl),
                p.device_group or "",
                "yes" if p.name == self._config.default_profile else "",
            )
        default = self._config.default_profile or "(none)"
        self.query_one("#profile-summary", Static).update(
            f"default profile: {default}    default output: {self._config.defaults.output}"
        )

    def _focused_profile_name(self) -> str | None:
        """The name of the profile under the table cursor, if any."""
        if not self._config.profiles:
            return None
        table = self.query_one("#profile-table", DataTable)
        row = table.cursor_row
        if row is None or not 0 <= row < len(self._config.profiles):
            return None
        return self._config.profiles[row].name

    def _focused_profile(self) -> Profile | None:
        """The Profile object under the table cursor, if any."""
        name = self._focused_profile_name()
        if name is None:
            return None
        return next((p for p in self._config.profiles if p.name == name), None)

    def _status(self, message: str) -> None:
        self.query_one("#profile-status", Static).update(message)

    def on_input_changed(self, event: Input.Changed) -> None:
        # Editing the reload target invalidates a pending discard confirmation.
        if event.input.id == "reload-path":
            self._reload_armed = False

    def action_reload_source(self) -> None:
        if self.session is None:
            self.app.bell()
            return
        path = self.query_one("#reload-path", Input).value.strip()
        profile: Profile | None = None
        if path:
            desc = f"export {path}"
        else:
            profile = self._focused_profile()
            if profile is None:
                self._status("[red]focus a profile or type an export path to load[/red]")
                self.app.bell()
                return
            desc = f"profile '{profile.name}'"
        # Reloading discards the staged batch + selection: confirm once first.
        if self.session.staging and not self._reload_armed:
            self._reload_armed = True
            n = len(self.session.staging)
            self._status(
                f"[yellow]loading {desc} discards {n} staged change(s) + the selection "
                "— press ctrl+r again to confirm[/yellow]"
            )
            return
        try:
            if path:
                source: OfflineSource | LiveSource = OfflineSource(path)
            else:
                assert profile is not None  # no path -> resolved above
                source = profile.to_live_source()
            self.session.reload(source)
        except PscError as exc:
            self._reload_armed = False
            self._status(f"[red]{exc}[/red]")
            self.app.bell()
            return
        self._reload_armed = False
        cast("WorkbenchApp", self.app)._reload_view()
        self._status(f"loaded {desc} — selection + staged batch cleared")

    def _persist(self, config: Config) -> bool:
        """Persist `config`, refresh the view, and report. Returns success."""
        try:
            save_config(config)
        except PscError as exc:
            self._config = load_config()
            self._refresh()
            self._status(f"[red]{exc}[/red]")
            self.app.bell()
            return False
        self._config = config
        self._refresh()
        return True

    def action_save_profile(self) -> None:
        name = self.query_one("#profile-name", Input).value.strip()
        host = self.query_one("#profile-host", Input).value.strip()
        if not name or not host:
            self._status("[red]name and hostname are required[/red]")
            self.app.bell()
            return
        api_key = self.query_one("#profile-api-key", Input).value  # never trimmed/logged
        dg = self.query_one("#profile-dg", Input).value.strip() or None
        verify = str(self.query_one("#profile-verify", Select).value) != "off"
        profile = Profile(
            name=name,
            hostname=host,
            api_key=api_key,
            verify_ssl=verify,
            device_group=dg,
        )
        # Reload before mutating so we upsert onto the current on-disk state.
        config = add_or_update_profile(load_config(), profile)
        if self._persist(config):
            # Deliberately do NOT echo the api key.
            self._status(f"saved profile '{name}'")

    def action_remove_profile(self) -> None:
        name = self._focused_profile_name()
        if name is None:
            self.app.bell()
            return
        try:
            config = remove_profile(load_config(), name)
        except PscError as exc:
            self._status(f"[red]{exc}[/red]")
            self.app.bell()
            return
        if self._persist(config):
            self._status(f"removed profile '{name}'")

    def action_set_default(self) -> None:
        name = self._focused_profile_name()
        if name is None:
            self.app.bell()
            return
        try:
            config = set_default_profile(load_config(), name)
        except PscError as exc:
            self._status(f"[red]{exc}[/red]")
            self.app.bell()
            return
        if self._persist(config):
            self._status(f"default profile is now '{name}'")

    def action_set_output(self) -> None:
        output = str(self.query_one("#profile-output", Select).value)
        try:
            config = set_default_output(load_config(), output)
        except PscError as exc:
            self._status(f"[red]{exc}[/red]")
            self.app.bell()
            return
        if self._persist(config):
            self._status(f"default output is now '{output}'")
