"""The `?` overlay — every key binding, grouped, on demand.

The Footer only advertises three keys; this is where the other twenty live. It
reads the same `psc.tui.commands` table the bindings are generated from, so it
can never drift out of sync with what the keys actually do.
"""

from __future__ import annotations

from typing import ClassVar

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from psc.tui.commands import by_category

# The BINDINGS below are the only way out of this modal; say so on the card
# itself, since the Footer's `? keys` entry is disabled while it's open (the
# spoke-stacking guard in check_action) and has nothing to point at.
DISMISS_HINT = "esc / ? / q — close"


def _render() -> Table:
    """The cheatsheet body: 'Category' header rows, then key/title/description rows.

    A flat `\\n`-joined string left to a Static's soft-wrap put a long
    description's continuation line back at column 0, under the key column —
    it read as broken text rather than a cheatsheet. A Table keeps the wrap
    inside the description column, with continuation lines hanging under it.
    """
    table = Table(
        box=None,
        show_header=False,
        pad_edge=True,
        padding=(0, 1),
        caption=DISMISS_HINT,
        caption_style="dim",
        caption_justify="left",
    )
    # No fixed width: a pinned width (previously 20, sized for "delete /
    # backspace" on one line) reserved that space unconditionally, starving
    # the description column to nothing on a narrow terminal. Letting the key
    # column auto-size to its actual content — and putting aliases on their
    # own line within the cell instead of joining them with " / " — keeps its
    # natural width small (the longest single key/alias, "backspace", is 9
    # chars) so Rich has room left to give the description column.
    #
    # `title` must NOT be `no_wrap`: Rich only shrinks a `no_wrap` column below
    # its natural content width as a last-resort "reduce everything evenly"
    # pass, which starved `description` down to 0 (an empty column, no dash,
    # nothing) at 40 cols under the old no_wrap title — `title`'s ~20-char
    # natural width ("Apply naming scheme") plus `key`'s left almost nothing
    # for `description` to be reduced into. A wrappable `title` collapses
    # (onto a second line) before `description` is starved, same as
    # `description` itself already did — it stays one line at any realistic
    # width since titles are short, but degrades gracefully instead of
    # forcing everything else to zero when the terminal is genuinely tiny.
    table.add_column("key", style="b", no_wrap=True)
    table.add_column("title")
    table.add_column("description")  # the only column allowed to wrap

    first = True
    for category, cmds in by_category():
        if not cmds:
            continue
        if not first:
            table.add_row()
        first = False
        table.add_row(Text(category, style="b #fa582d"))
        for cmd in cmds:
            description = Text("— ")
            description.append(cmd.description, style="dim")
            # An alias (e.g. backspace for delete) is a real, working binding —
            # list it too, or the overlay defeats its own purpose of being
            # where every hidden hotkey is discoverable. One per line rather
            # than " / "-joined, so a multi-char alias doesn't widen the
            # column for every row.
            key_label = Text(cmd.key)
            for alias in cmd.aliases:
                key_label.append(f"\n{alias}")
            table.add_row(key_label, cmd.title, description)
    return table


class KeymapScreen(ModalScreen[None]):
    """A centred card over the dimmed hub listing every binding."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "close", "close"),
        ("question_mark", "close", "close"),
        ("q", "close", "close"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="keymap-card"), VerticalScroll():
            yield Static(_render(), id="keymap-body")

    def action_close(self) -> None:
        self.dismiss(None)
