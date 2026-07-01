"""Dedup spoke: propose a safe merge for duplicate selected addresses."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static

from psc.core.changeset import ChangeSet
from psc.core.dedup import ObjectRef, plan_merge
from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem
from psc.tui.widgets.review import ReviewPanel, can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

_MIN_DUP_COUNT = 2


def plan_selection_merge(session: WorkbenchSession) -> tuple[str, ChangeSet] | None:
    """First duplicate address pair in the selection -> (label, merge plan).

    Returns None when fewer than two selected addresses share a value.
    """
    addrs = session.selected_of_kinds({"address"})
    snap = session.working_snapshot
    index = {(a.location.name, a.name): a for a in snap.addresses}
    by_value: dict[str, list[SelectionItem]] = {}
    for item in addrs:
        obj = index.get((item.location, item.name))
        if obj is None:
            continue
        by_value.setdefault(obj.value, []).append(item)
    for _value, group in by_value.items():
        if len(group) >= _MIN_DUP_COUNT:
            keep, drop = group[0], group[1]
            graph = ReferenceGraph.build(snap)
            cs = plan_merge(
                snap,
                graph,
                keep=ObjectRef(name=keep.name, location=keep.location),
                drop=ObjectRef(name=drop.name, location=drop.location),
            )
            return (f"merge {drop.name} -> {keep.name}", cs)
    return None


class DedupScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+y", "stage", "stage merge"),
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._plan = plan_selection_merge(session)

    def compose(self) -> ComposeResult:
        panel = ReviewPanel(id="review")
        yield panel
        if self._plan is None:
            yield Static("No duplicate addresses in the selection.", id="dedup-empty")
        yield Footer()

    def on_mount(self) -> None:
        if self._plan is not None:
            self.query_one("#review", ReviewPanel).show(self._plan[1])

    def action_stage(self) -> None:
        if self._plan is None:
            self.app.bell()
            return
        label, cs = self._plan
        if not can_apply(cs):
            self.app.bell()
            return
        self.session.stage(label, cs)
        self.app.pop_screen()
        # Refresh the hub's selection/staging view after returning.
        hub_app = cast("WorkbenchApp", self.app)
        hub_app._refresh_selection_view()
