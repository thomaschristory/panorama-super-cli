"""Render `ObjectView`s (from `psc.core.inspect`) for the CLI.

Shared by `find object --expand` and `show`. Table output draws a rich member
tree plus the effective-leaf set; machine formats serialize the `ObjectView`
model directly (never rich-wrapped), per the output contract.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.tree import Tree

from psc.core.inspect import InspectNode, NodeStatus, ObjectView
from psc.output.format import OutputFormat, render

_STATUS_SUFFIX = {
    NodeStatus.DYNAMIC: " [yellow](dynamic)[/yellow]",
    NodeStatus.DANGLING: " [red](dangling)[/red]",
    NodeStatus.CYCLE: " [magenta](cycle ↩)[/magenta]",
}


def _label(node: InspectNode) -> str:
    if node.kind == "field":  # a rule field grouping, e.g. "source:"
        return f"[bold]{node.name}[/bold]:"
    loc = f" @{node.location}" if node.location else ""
    detail = f" = {node.detail}" if node.detail else ""
    suffix = _STATUS_SUFFIX.get(node.status, "")
    return f"[cyan]{node.kind}[/cyan] {node.name}{loc}{detail}{suffix}"


def _attach(branch: Tree, node: InspectNode) -> None:
    for child in node.children:
        _attach(branch.add(_label(child)), child)


def _rows(views: list[ObjectView]) -> list[dict[str, Any]]:
    """Flat rows for csv: one per effective leaf; kinds without an effective set
    (tag/rule) contribute a single summary row."""
    rows: list[dict[str, Any]] = []
    for v in views:
        if v.effective_leaves is None:
            rows.append({"object": v.name, "kind": v.kind, "location": v.location, "leaf": ""})
            continue
        if not v.effective_leaves:
            rows.append(
                {"object": v.name, "kind": v.kind, "location": v.location, "leaf": "(empty)"}
            )
        for leaf in v.effective_leaves:
            rows.append({"object": v.name, "kind": v.kind, "location": v.location, "leaf": leaf})
    return rows


def render_object_views(
    stdout: Console,
    stderr: Console,
    fmt: OutputFormat,
    views: list[ObjectView],
) -> None:
    """Print `views` in `fmt`. Table mode draws a tree per view; machine formats
    serialize the model. Warnings go to `stderr` so machine output stays clean."""
    if fmt is not OutputFormat.TABLE:
        model: Any = views if len(views) != 1 else views[0]
        render(stdout, fmt, model=model, rows=_rows(views))
        return

    if not views:
        stdout.print("(no matching object)")
        return

    for view in views:
        tree = Tree(_label(view.tree))
        _attach(tree, view.tree)
        stdout.print(tree)
        if view.effective_leaves is not None:
            note = "" if view.effective_complete else "  [yellow](incomplete)[/yellow]"
            stdout.print(f"effective: {len(view.effective_leaves)} leaf value(s){note}")
            for leaf in view.effective_leaves:
                stdout.print(f"  • {leaf}")
        for warning in view.warnings:
            stderr.print(f"warning: {warning}", style="yellow")
