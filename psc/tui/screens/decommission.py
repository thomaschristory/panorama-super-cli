"""Decommission spoke: reference-safe teardown of selected address objects."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer

from psc.core.changeset import ChangeSet
from psc.core.decommission import plan_decommission
from psc.core.models import Address
from psc.core.refs import ReferenceGraph
from psc.tui.session import WorkbenchSession
from psc.tui.widgets.review import ReviewPanel, can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp


def plan_selection_decommission(session: WorkbenchSession) -> ChangeSet | None:
    """Plan teardown of the selected address objects, or None if none selected."""
    selected = session.selected_of_kinds({"address"})
    if not selected:
        return None
    index = {(a.location.name, a.name): a for a in session.working_snapshot.addresses}
    targets: list[Address] = []
    for item in selected:
        obj = index.get((item.location, item.name))
        if obj is not None:
            targets.append(obj)
    if not targets:
        return None
    graph = ReferenceGraph.build(session.working_snapshot)
    return plan_decommission(session.working_snapshot, graph, targets)


class DecommissionScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+y", "stage", "confirm teardown"),
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._plan = plan_selection_decommission(session)

    def compose(self) -> ComposeResult:
        yield ReviewPanel(id="review")
        yield Footer()

    def on_mount(self) -> None:
        panel = self.query_one("#review", ReviewPanel)
        if self._plan is None:
            panel.update("Select one or more address objects to decommission.")
        else:
            panel.show(self._plan)

    def action_stage(self) -> None:
        # Re-plan against the current snapshot at confirm time; never crash on an
        # engine/apply error.
        try:
            plan = plan_selection_decommission(self.session)
            if plan is None or not can_apply(plan):
                self.app.bell()
                return
            self.session.stage("decommission address objects", plan)
        except Exception:
            self.app.bell()
            return
        cast("WorkbenchApp", self.app)._refresh_selection_view()
        self.app.pop_screen()
