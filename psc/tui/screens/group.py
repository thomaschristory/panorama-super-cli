"""Group spoke: add the selected objects as members of an existing group.

The membership analogue of the rule spoke: pick objects in the hub, press `G`,
name a target address-/service-group, and each selected object is added to its
member list (idempotent). Removal lives in the CLI (`psc group edit-member
--remove`); the spoke is add-only, mirroring the rule spoke.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Input, Static

from psc.core.changeset import ChangeSet
from psc.core.group_edit import plan_group_member_edit
from psc.output.errors import PscError
from psc.tui.session import WorkbenchSession
from psc.tui.widgets.review import can_apply, escape_markup

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp


def plan_group_add_member(
    session: WorkbenchSession, group_name: str, member_name: str
) -> ChangeSet:
    """Plan adding `member_name` to `group_name`'s member list (idempotent)."""
    return plan_group_member_edit(
        session.working_snapshot, group_name, add=member_name, remove=None
    )


class GroupScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._members = [i.name for i in session.selection]

    def compose(self) -> ComposeResult:
        if not self._members:
            yield Static("Select objects to add to a group first.", id="group-empty")
        else:
            # Unescaped, a bracketed name list is parsed as markup and dropped (#129).
            names = escape_markup(", ".join(self._members))
            yield Static(f"Add {names} to an address- or service-group.")
            yield Input(placeholder="group name", id="group-name")
            yield Static("Enter on the group box to stage.", id="group-hint")
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "group-name" or not self._members:
            return
        group_name = event.value.strip()
        if not group_name:
            self.app.bell()
            return
        hub = cast("WorkbenchApp", self.app)
        # Re-derive from the CURRENT selection at confirm time so a stale name
        # (renamed/decommissioned since the screen opened) is never added.
        members = [i.name for i in self.session.selection]
        try:
            for member in members:
                cs = plan_group_add_member(self.session, group_name, member)
                if not can_apply(cs):
                    # Blocked member: stop, stay on-screen (earlier ones staged).
                    self.app.bell()
                    hub._refresh_selection_view()
                    return
                if not cs.is_empty:
                    self.session.stage(f"add {member} to {group_name}", cs)
        except PscError:
            # Unknown/dynamic/ambiguous group: surface via bell, don't crash.
            self.app.bell()
            hub._refresh_selection_view()
            return
        hub._refresh_selection_view()
        self.app.pop_screen()
