"""Rename spoke: reference-aware rename of a chosen selected object."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Input, Select, Static

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


def renameable_items(session: WorkbenchSession) -> list[SelectionItem]:
    """Every selected object (any kind) — a rename target the user can pick."""
    return list(session.selection)


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
        self._items = renameable_items(session)

    def compose(self) -> ComposeResult:
        if not self._items:
            yield Static("Select an object to rename.", id="rename-empty")
        else:
            # With several objects selected, the user must say WHICH one to rename
            # rather than the tool silently picking the first (#89). A single
            # selection still shows the picker (one entry) so the flow is uniform.
            yield Static("Choose which selected object to rename:")
            yield Select(
                [(f"{i.kind} '{i.name}'@{i.location}", idx) for idx, i in enumerate(self._items)],
                value=0,
                allow_blank=False,
                id="rename-target",
            )
            yield Input(placeholder="new name (Enter to stage)", id="rename-input")
        yield Footer()

    def on_mount(self) -> None:
        # Land focus on the name Input (added a target Select above it, which
        # would otherwise capture focus) so Enter stages immediately; the user can
        # Tab up to the target dropdown to pick a different object first.
        if self._items:
            self.query_one("#rename-input", Input).focus()

    def _chosen_item(self) -> SelectionItem | None:
        if not self._items:
            return None
        value = self.query_one("#rename-target", Select).value
        idx = value if isinstance(value, int) else 0
        return self._items[idx] if 0 <= idx < len(self._items) else None

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "rename-input":
            return
        item = self._chosen_item()
        if item is None:
            return
        new_name = event.value.strip()
        if not new_name:
            self.app.bell()
            return
        try:
            cs = plan_rename_item(self.session, item, new_name)
            if not can_apply(cs):
                self.app.bell()
                return
            self.session.stage(f"rename {item.name} -> {new_name}", cs)
        except Exception:
            self.app.bell()
            return
        cast("WorkbenchApp", self.app)._refresh_selection_view()
        self.app.pop_screen()
