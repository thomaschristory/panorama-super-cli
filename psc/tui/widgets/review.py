"""Review panel: renders a ChangeSet's plan (set script + warnings + blockers).

`review_lines`/`can_apply` are pure so they unit-test without a running app;
`ReviewPanel` is the thin Textual wrapper the screens mount.
"""

from __future__ import annotations

from textual.widgets import Static

from psc.core.changeset import ChangeSet
from psc.core.setcmd import render_changeset


def can_apply(cs: ChangeSet) -> bool:
    """Blocked changesets can never be staged/applied — the hard gate."""
    return not cs.is_blocked


def review_lines(cs: ChangeSet) -> list[str]:
    lines = [f"[b]{cs.title}[/b]"]
    for w in cs.warnings:
        lines.append(f"  [yellow]! {w}[/yellow]")
    if cs.is_blocked:
        lines.append("[red]BLOCKED — will not apply:[/red]")
        lines.extend(f"  [red]- {b}[/red]" for b in cs.blockers)
        return lines
    for line in render_changeset(cs):
        lines.append(f"  {line}")
    return lines


class ReviewPanel(Static):
    """Displays the plan for a ChangeSet; `can_apply` gates the apply key."""

    _cs: ChangeSet

    def show(self, cs: ChangeSet) -> None:
        self._cs = cs
        self.update("\n".join(review_lines(cs)))

    @property
    def can_apply(self) -> bool:
        return can_apply(getattr(self, "_cs", ChangeSet(title="")))
