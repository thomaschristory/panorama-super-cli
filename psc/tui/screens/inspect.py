"""Inspect spoke: 'open' the focused object to see what it contains.

Read-only, like the usage spoke — never stages, never mutates. Renders the
member tree (nested groups recurse) and the effective leaf set over the shared
`inspect_object` engine, so the TUI and CLI show the same expansion.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static, Tree
from textual.widgets.tree import TreeNode

from psc.core.inspect import InspectNode, NodeStatus, ObjectView
from psc.core.models import Location
from psc.tui.session import WorkbenchSession
from psc.tui.state import SelectionItem

_STATUS_SUFFIX = {
    NodeStatus.DYNAMIC: " (dynamic)",
    NodeStatus.DANGLING: " (dangling)",
    NodeStatus.CYCLE: " (cycle ↩)",
}


def _loc(name: str) -> Location:
    return Location.shared() if name == "shared" else Location.dg(name)


def _node_label(node: InspectNode) -> str:
    if node.kind == "field":  # a rule field grouping, e.g. "source:"
        return f"{node.name}:"
    loc = f" @{node.location}" if node.location else ""
    detail = f" = {node.detail}" if node.detail else ""
    return f"{node.kind} {node.name}{loc}{detail}{_STATUS_SUFFIX.get(node.status, '')}"


def _effective_lines(views: list[ObjectView]) -> str:
    lines: list[str] = []
    for view in views:
        if view.effective_leaves is None:
            lines.append(f"{view.name}: membership view (no flat leaf set)")
        else:
            note = "" if view.effective_complete else " (incomplete)"
            lines.append(f"{view.name}: {len(view.effective_leaves)} leaf value(s){note}")
            lines.extend(f"  • {leaf}" for leaf in view.effective_leaves)
        for warning in view.warnings:
            lines.append(f"  ! {warning}")
    return "\n".join(lines) if lines else "(nothing to expand)"


class InspectScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "app.pop_screen", "back"),
    ]

    def __init__(self, session: WorkbenchSession, item: SelectionItem) -> None:
        super().__init__()
        self._item = item
        self._views = inspect_object_for(session, item)

    def compose(self) -> ComposeResult:
        if not self._views:
            yield Static(f"No object named '{self._item.name}'.", id="inspect-empty")
            yield Footer()
            return
        yield Tree(self._item.name, id="inspect-tree")
        yield Static(_effective_lines(self._views), id="inspect-effective")
        yield Footer()

    def on_mount(self) -> None:
        if not self._views:
            return
        tree: Tree[None] = self.query_one("#inspect-tree", Tree)
        tree.root.expand()
        for view in self._views:
            branch = tree.root.add(_node_label(view.tree), expand=True)
            _attach(branch, view.tree)


def _attach(branch: TreeNode[None], node: InspectNode) -> None:
    for child in node.children:
        _attach(branch.add(_node_label(child), expand=True), child)


def inspect_object_for(
    session: WorkbenchSession, item: SelectionItem
) -> list[ObjectView]:
    """Expand the object named by `item` against the session's working snapshot,
    scoped to the item's location (its device-group ancestry + shared)."""
    from psc.core.inspect import inspect_object  # noqa: PLC0415 — avoid import cycle

    return inspect_object(
        session.working_snapshot, item.name, scope=_loc(item.location)
    )
