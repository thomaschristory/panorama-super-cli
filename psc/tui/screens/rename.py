"""Rename spoke: reference-aware rename of the first selected object."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Input, Static

from psc.core.changeset import ChangeSet, ObjectKind
from psc.core.naming import plan_rename
from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem
from psc.tui.widgets.review import can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp


def first_renameable(session: WorkbenchSession) -> SelectionItem | None:
    """The first selected object (any kind), or None if the selection is empty."""
    return session.selection[0] if session.selection else None


def plan_rename_item(session: WorkbenchSession, item: SelectionItem, new_name: str) -> ChangeSet:
    """Plan a reference-aware rename of `item` to `new_name`."""
    graph = ReferenceGraph.build(session.working_snapshot)
    return plan_rename(
        session.working_snapshot,
        graph,
        kind=ObjectKind(item.kind),
        location_name=item.location,
        old_name=item.name,
        new_name=new_name,
    )


class RenameScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._item = first_renameable(session)

    def compose(self) -> ComposeResult:
        if self._item is None:
            yield Static("Select an object to rename.", id="rename-empty")
        else:
            yield Static(f"Rename {self._item.kind} '{self._item.name}'@{self._item.location} to:")
            yield Input(placeholder="new name (Enter to stage)", id="rename-input")
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "rename-input" or self._item is None:
            return
        new_name = event.value.strip()
        if not new_name:
            self.app.bell()
            return
        cs = plan_rename_item(self.session, self._item, new_name)
        if not can_apply(cs):
            self.app.bell()
            return
        self.session.stage(f"rename {self._item.name} -> {new_name}", cs)
        self.app.pop_screen()
        cast("WorkbenchApp", self.app)._refresh_selection_view()
