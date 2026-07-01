"""Move spoke: promote selected objects toward shared (stages a ChangeSet each)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static

from psc.core.changeset import ChangeSet, ObjectKind
from psc.core.refs import ReferenceGraph
from psc.core.relocate import plan_move
from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem
from psc.tui.widgets.review import can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

_MOVABLE_KINDS = {"address", "address-group", "service", "service-group", "tag"}


def movable_items(session: WorkbenchSession) -> list[SelectionItem]:
    """Selected objects of a movable kind that are not already in shared."""
    return [i for i in session.selected_of_kinds(_MOVABLE_KINDS) if i.location != "shared"]


def plan_move_item(session: WorkbenchSession, item: SelectionItem, dest_name: str) -> ChangeSet:
    """Plan promoting one selected object toward `dest_name` (e.g. 'shared')."""
    graph = ReferenceGraph.build(session.working_snapshot)
    return plan_move(
        session.working_snapshot,
        graph,
        kind=ObjectKind(item.kind),
        name=item.name,
        source_name=item.location,
        dest_name=dest_name,
    )


class MoveScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+y", "stage", "move to shared"),
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._items = movable_items(session)

    def compose(self) -> ComposeResult:
        if not self._items:
            yield Static("No selected objects outside shared to move.", id="move-empty")
        else:
            names = ", ".join(f"{i.name}@{i.location}" for i in self._items)
            yield Static(f"Move to shared: {names}\n[ctrl+y] confirm  [esc] cancel", id="move-plan")
        yield Footer()

    def action_stage(self) -> None:
        if not self._items:
            self.app.bell()
            return
        for item in list(self._items):
            cs = plan_move_item(self.session, item, "shared")
            if not can_apply(cs):
                self.app.bell()
                break
            self.session.stage(f"move {item.name} -> shared", cs)
        self.app.pop_screen()
        cast("WorkbenchApp", self.app)._refresh_selection_view()
