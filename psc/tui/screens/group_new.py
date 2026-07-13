"""New-group spoke: turn the current selection into a group (#146).

The find session's payoff. `G` adds the selection to a group that already exists;
`N` makes one out of it. The kind follows the selection (addresses -> an
address-group, services -> a service-group), and the location picker defaults to
the narrowest location that can actually see every member.

`plan_group_create` in `psc/core/group_edit.py` does the thinking: it refuses a
member the group's location cannot reach, or one whose name is shadowed there. A
blocked plan is never staged (the hard gate).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Input, Select, Static

from psc.core.changeset import ChangeSet
from psc.core.group_edit import plan_group_create, suggest_group_location
from psc.core.models import Location
from psc.core.refs import Target
from psc.output.errors import PscError
from psc.tui.screens.create import location_options
from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem
from psc.tui.widgets.review import can_apply, escape_markup

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

# Kinds that can be a member of some group. A tag can't, so it is never sent to
# the planner — the screen just reports how many rows it left behind.
GROUPABLE_KINDS: frozenset[str] = frozenset(
    {"address", "address-group", "service", "service-group"}
)


def _target(item: SelectionItem) -> Target:
    loc = Location.shared() if item.location == "shared" else Location.dg(item.location)
    return Target(kind=item.kind, name=item.name, location=loc)


def groupable(selection: list[SelectionItem]) -> list[SelectionItem]:
    return [i for i in selection if i.kind in GROUPABLE_KINDS]


def plan_new_group(
    session: WorkbenchSession,
    name: str,
    location: str,
    *,
    description: str | None = None,
    tags: list[str] | None = None,
) -> ChangeSet:
    """Plan a new group at `location` from the session's groupable selection."""
    members = [_target(i) for i in groupable(session.selection)]
    loc = Location.shared() if location == "shared" else Location.dg(location)
    return plan_group_create(
        session.working_snapshot,
        name.strip(),
        loc,
        members,
        description=description,
        tags=tags,
    )


class NewGroupScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+y", "stage", "create group"),
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._members = groupable(session.selection)
        self._skipped = len(session.selection) - len(self._members)
        self._locations = location_options(session)
        # The narrowest location that sees every member. None when they span
        # sibling device-groups: no location can, so fall back to shared and let
        # the planner spell out why it won't work.
        self._suggested = (
            suggest_group_location(session.working_snapshot, [i.location for i in self._members])
            or "shared"
        )

    def _derived_kind(self) -> str:
        kinds = {i.kind for i in self._members}
        if kinds & {"service", "service-group"} and not kinds & {"address", "address-group"}:
            return "service-group"
        return "address-group"

    def compose(self) -> ComposeResult:
        if not self._members:
            yield Static(
                "Select address or service objects to build a group from first.",
                id="group-new-empty",
            )
            yield Footer()
            return

        # Object names are escaped: Textual would eat a bracketed member list as
        # markup and render the line with no members at all (#129).
        names = escape_markup(", ".join(i.name for i in self._members))
        note = f"  (skipping {self._skipped} non-groupable)" if self._skipped else ""
        yield Static(f"New {self._derived_kind()} from {names}{note}", id="group-new-members")
        yield Input(placeholder="group name", id="group-new-name")
        yield Select(
            [(loc, loc) for loc in self._locations],
            value=self._suggested,
            allow_blank=False,
            id="group-new-location",
        )
        yield Input(placeholder="description (optional)", id="group-new-description")
        yield Input(placeholder="tags (comma-separated, optional)", id="group-new-tags")
        yield Static("[ctrl+y] create  [esc] cancel", id="group-new-plan")
        yield Footer()

    def on_mount(self) -> None:
        if self._members:
            # A service-group has no description field; hiding the box keeps the
            # form from offering something the planner would reject.
            self.query_one("#group-new-description").display = (
                self._derived_kind() == "address-group"
            )
            self.query_one("#group-new-name", Input).focus()

    def _location_name(self) -> str:
        value = self.query_one("#group-new-location", Select).value
        return str(value) if value is not Select.BLANK else "shared"

    def _fail(self, message: str) -> None:
        self.query_one("#group-new-plan", Static).update(f"[red]{escape_markup(message)}[/red]")
        self.app.bell()

    def action_stage(self) -> None:
        if not self._members:
            self.app.bell()
            return
        hub = cast("WorkbenchApp", self.app)
        name = self.query_one("#group-new-name", Input).value.strip()
        if not name:
            self._fail("a group needs a name")
            return
        is_address_group = self._derived_kind() == "address-group"
        description = (
            self.query_one("#group-new-description", Input).value.strip() or None
            if is_address_group
            else None
        )
        raw_tags = self.query_one("#group-new-tags", Input).value
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        try:
            cs = plan_new_group(
                self.session,
                name,
                self._location_name(),
                description=description,
                tags=tags,
            )
        except PscError as exc:
            # A selection no group can express (mixed namespaces, self-reference,
            # a bad name): say so, stay put, stage nothing.
            self._fail(str(exc))
            return
        if not can_apply(cs):
            self._fail("BLOCKED: " + "; ".join(cs.blockers))
            return
        try:
            self.session.stage(cs.title, cs)
        except PscError as exc:
            self._fail(str(exc))
            return
        # The members have been consumed into the group; a stale selection of them
        # is not what you want to act on next.
        self.session.clear_selection()
        hub._refresh_selection_view()
        self.app.pop_screen()
