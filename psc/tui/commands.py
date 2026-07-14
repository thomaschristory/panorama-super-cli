"""The hub command table — the single source of truth for the workbench's keys.

`BINDINGS`, the spoke-stacking guard (`_HUB_ACTIONS`), the `?` keymap overlay and
the ctrl+p command palette are all *derived* from `HUB_COMMANDS`, so adding a
spoke is one row here rather than four edits in three files that drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.binding import Binding


@dataclass(frozen=True)
class Command:
    """One hub action: its key, its handler, and how it describes itself."""

    key: str
    action: str
    title: str
    description: str
    category: str
    # A second key for the same action (delete/backspace both drop a row).
    aliases: tuple[str, ...] = ()
    # Whether the spoke-stacking guard disables this while a spoke is open.
    # False for quit and the palette, which must stay reachable from anywhere.
    hub_only: bool = True


# Display order in the ? overlay and in the palette's empty-query list.
CATEGORIES: tuple[str, ...] = ("Navigate", "Objects", "Analyze", "Names", "Session")

# The only keys the Footer advertises. Everything else still works — it just
# stops shouting from the bottom of the screen; `?` is how you find it.
FOOTER_KEYS: frozenset[str] = frozenset({"?", "ctrl+p", "q"})

HUB_COMMANDS: tuple[Command, ...] = (
    Command(
        "space",
        "toggle_row",
        "Select",
        "Add or remove the focused results row from the selection",
        "Navigate",
    ),
    Command(
        "v",
        "inspect",
        "Inspect",
        "Open the focused row read-only: member tree and effective leaf set",
        "Navigate",
    ),
    Command(
        "delete",
        "remove_selected",
        "Remove",
        "Drop the focused row from the selection panel",
        "Navigate",
        aliases=("backspace",),
    ),
    Command(
        "c",
        "create",
        "Create",
        "Create an address, group, service, service-group or tag",
        "Objects",
    ),
    Command(
        "r",
        "rename",
        "Rename",
        "Rename an object and repoint every reference to it",
        "Objects",
    ),
    Command(
        "m",
        "move",
        "Move",
        "Promote selected objects toward shared",
        "Objects",
    ),
    Command(
        "G",
        "group_add",
        "Add to group",
        "Add the selection as members of an existing group",
        "Objects",
    ),
    Command(
        "N",
        "group_new",
        "New group",
        "Build a new group out of the selection",
        "Objects",
    ),
    Command(
        "e",
        "rule_edit",
        "Edit rule",
        "Add the selection as members of an existing rule field",
        "Objects",
    ),
    Command(
        "x",
        "decommission",
        "Decommission",
        "Reference-safe cascading teardown of the selected addresses",
        "Objects",
    ),
    Command(
        "d",
        "dedup",
        "Dedup",
        "Collapse the selected duplicates toward one chosen survivor",
        "Analyze",
    ),
    Command(
        "D",
        "duplicates",
        "Duplicate scan",
        "Find every duplicate bucket in the whole config",
        "Analyze",
    ),
    Command(
        "u",
        "usage",
        "Usage",
        "Where-used report for the whole selection",
        "Analyze",
    ),
    Command(
        "a",
        "audit",
        "Audit",
        "Overlapping or contained IP ranges, and services duplicating a known port",
        "Analyze",
    ),
    Command(
        "i",
        "unused",
        "Unused",
        "List objects that no rule reaches",
        "Analyze",
    ),
    Command(
        "g",
        "dangling",
        "Dangling",
        "List references to names that resolve to nothing",
        "Analyze",
    ),
    Command(
        "f",
        "diff",
        "Diff",
        "Drift between two device-groups: added, removed and changed objects",
        "Analyze",
    ),
    Command(
        "l",
        "name_lint",
        "Name lint",
        "Report objects whose name drifts from the naming scheme",
        "Names",
    ),
    Command(
        "n",
        "name_apply",
        "Apply naming scheme",
        "Rename drifting objects to their scheme name",
        "Names",
    ),
    Command(
        "s",
        "staged",
        "Staged changes",
        "Inspect the staged changelist, drop changes, and apply the batch",
        "Session",
    ),
    Command(
        "o",
        "export",
        "Export",
        "Write objects of one kind to an NDJSON file",
        "Session",
    ),
    Command(
        "p",
        "profiles",
        "Profiles",
        "Manage live connection profiles and switch the active source",
        "Session",
    ),
    Command(
        "?",
        "keymap",
        "Keys",
        "Show every key binding, grouped by what it does",
        "Session",
    ),
    Command(
        "ctrl+p",
        "command_palette",
        "Commands",
        "Search every command by name",
        "Session",
        hub_only=False,
    ),
    Command(
        "q",
        "quit",
        "Quit",
        "Quit the workbench",
        "Session",
        hub_only=False,
    ),
)


def bindings() -> list[Binding]:
    """The app's BINDINGS, derived from the table.

    Only `FOOTER_KEYS` get `show=True` — the rest still work, they're just not
    advertised in the Footer.
    """
    out: list[Binding] = []
    for cmd in HUB_COMMANDS:
        label = cmd.title.lower()
        out.append(Binding(cmd.key, cmd.action, label, show=cmd.key in FOOTER_KEYS))
        out.extend(Binding(alias, cmd.action, label, show=False) for alias in cmd.aliases)
    return out


def hub_actions() -> frozenset[str]:
    """Actions the spoke-stacking guard disables while a spoke is open."""
    return frozenset(cmd.action for cmd in HUB_COMMANDS if cmd.hub_only)


def by_category() -> list[tuple[str, list[Command]]]:
    """The table grouped for display, in `CATEGORIES` order."""
    return [(cat, [c for c in HUB_COMMANDS if c.category == cat]) for cat in CATEGORIES]
