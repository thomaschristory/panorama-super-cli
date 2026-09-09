"""Move spoke: promote selected objects toward shared (stages a ChangeSet each)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Checkbox, Footer, Select, Static

from psc.core.changeset import ChangeSet, ObjectKind
from psc.core.refs import ReferenceGraph
from psc.core.relocate import plan_move
from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem
from psc.tui.widgets.review import ReviewPanel, can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

_MOVABLE_KINDS = {"address", "address-group", "service", "service-group", "tag"}


def movable_items(session: WorkbenchSession) -> list[SelectionItem]:
    """Selected objects of a movable kind that are not already in shared.

    A move only ever promotes *toward* shared, so an object already at the top
    (shared) can never be moved regardless of the chosen destination.
    """
    return [i for i in session.selected_of_kinds(_MOVABLE_KINDS) if i.location != "shared"]


def move_destinations(session: WorkbenchSession) -> list[str]:
    """Destinations the user may promote toward: 'shared' plus every device-group.

    Sorted deterministically (shared first, then device-groups alphabetically) so
    the drop-down order is stable across renders. The engine — not this list —
    gates which of these are legal for a given source (only shared or a strict
    ancestor of the source is a valid promote target)."""
    return ["shared", *sorted(session.working_snapshot.device_groups)]


def plan_move_item(
    session: WorkbenchSession, item: SelectionItem, dest_name: str, *, cascade: bool = False
) -> ChangeSet:
    """Plan promoting one selected object toward `dest_name` (e.g. 'shared').

    `cascade` mirrors `psc move --cascade`: instead of blocking when the object's
    dependencies aren't yet visible at the destination, pull that downward closure
    (a group's members, any object's tags) up to `dest_name` too.
    """
    graph = ReferenceGraph.build(session.working_snapshot)
    return plan_move(
        session.working_snapshot,
        graph,
        kind=ObjectKind(item.kind),
        name=item.name,
        source_name=item.location,
        dest_name=dest_name,
        cascade=cascade,
    )


class MoveScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+y", "stage", "move"),
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._items = movable_items(session)
        self._dests = move_destinations(session)

    def compose(self) -> ComposeResult:
        if not self._items:
            yield Static("No selected objects outside shared to move.", id="move-empty")
        else:
            names = ", ".join(f"{i.name}@{i.location}" for i in self._items)
            # Default to shared: the safe, common promote target. The Select lists
            # shared + every device-group; the engine gates illegal (downward)
            # destinations at confirm time.
            yield Select(
                [(d, d) for d in self._dests],
                value="shared",
                allow_blank=False,
                id="move-dest",
            )
            yield Static(f"Move: {names}", id="move-plan")
            if self._can_cascade():
                # Only a non-tag object has a downward dependency (a group's
                # members, any object's tags) to pull along; a plain tag has
                # nothing to cascade, so the checkbox would be a no-op there.
                yield Checkbox("cascade dependencies", id="move-cascade")
            # Preview the FIRST movable item's plan; the chosen dest + cascade apply
            # uniformly to every item, so it is representative (and, unlike before,
            # gives the move spoke a live plan preview at all).
            yield ReviewPanel(id="review")
        yield Footer()

    def on_mount(self) -> None:
        if self._items:
            self._render_plan()

    def _can_cascade(self) -> bool:
        return any(i.kind != "tag" for i in self._items)

    def _selected_dest(self) -> str:
        select = self.query_one("#move-dest", Select)
        value = select.value
        # allow_blank=False keeps a concrete value selected; guard the typing only.
        return str(value) if value is not Select.BLANK else "shared"

    def _chosen_cascade(self) -> bool:
        # The checkbox is only composed when something can be cascaded (see
        # `compose`), so absent it there is nothing to pull along.
        if not self._can_cascade():
            return False
        return self.query_one("#move-cascade", Checkbox).value

    def _render_plan(self) -> None:
        # Re-derive from the current selection; the first movable item is the
        # preview subject (the stage loop applies the same dest + cascade to all).
        items = movable_items(self.session)
        if not items:
            return
        try:
            cs = plan_move_item(
                self.session, items[0], self._selected_dest(), cascade=self._chosen_cascade()
            )
        except Exception:
            # A transient bad state mid-interaction must not crash the app; the
            # confirm path re-plans and gates for real.
            return
        self.query_one("#review", ReviewPanel).show(cs)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "move-dest" and self._items:
            self._render_plan()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "move-cascade" and self._items:
            self._render_plan()

    def action_stage(self) -> None:
        hub = cast("WorkbenchApp", self.app)
        # Re-derive movable items from the current selection at confirm time.
        items = movable_items(self.session)
        if not items:
            self.app.bell()
            return
        dest = self._selected_dest()
        cascade = self._chosen_cascade()
        try:
            for item in items:
                if item.location == dest:
                    continue  # already at the chosen destination — nothing to move
                cs = plan_move_item(self.session, item, dest, cascade=cascade)
                if not can_apply(cs):
                    # A blocked move stops here rather than silently popping with
                    # a partial result: preceding items are already staged
                    # (visible in the hub's staging strip); stay on-screen so the
                    # user sees the bell and can decide (esc to leave).
                    self.app.bell()
                    hub._refresh_selection_view()
                    return
                self.session.stage(f"move {item.name} -> {dest}", cs)
        except Exception:
            self.app.bell()
            hub._refresh_selection_view()
            return
        hub._refresh_selection_view()
        self.app.pop_screen()
