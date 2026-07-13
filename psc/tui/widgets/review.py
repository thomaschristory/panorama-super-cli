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


def escape_markup(text: str) -> str:
    """Escape Textual markup in dynamic text.

    Textual's console-markup engine treats ``[ ... ]`` as a tag and drops it, so
    a rendered `set` member list like ``[ addr-a addr-b ]`` would display as an
    empty ``... destination`` with the members swallowed (#129, a display-only
    bug — the emitted script/file is correct). Backslash-escaping every opening
    bracket makes the members render literally. Applied only to *content* woven
    into markup strings, never to the intentional ``[b]``/``[red]`` tags.
    """
    return text.replace("[", r"\[")


def review_lines(cs: ChangeSet) -> list[str]:
    lines = [f"[b]{escape_markup(cs.title)}[/b]"]
    for w in cs.warnings:
        lines.append(f"  [yellow]! {escape_markup(w)}[/yellow]")
    if cs.is_blocked:
        lines.append("[red]BLOCKED — will not apply:[/red]")
        lines.extend(f"  [red]- {escape_markup(b)}[/red]" for b in cs.blockers)
        return lines
    for line in render_changeset(cs):
        lines.append(f"  {escape_markup(line)}")
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
