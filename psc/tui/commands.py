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
    # False only for quit — it must stay reachable no matter what's on the
    # stack. (The palette used to be False here too, but that let ctrl+p open
    # over a spoke and list commands check_action would then refuse to run —
    # picking one silently did nothing. It's gated like every other spoke key
    # now.
    #
    # `check_action` is doing real work here for `?` too — modality does NOT
    # protect it. `App._check_bindings(priority=True)` walks the *screen's*
    # binding chain, not the modal-aware one, so a priority binding (which `?`
    # is, see `priority` below) dispatches straight through an open
    # ModalScreen: Textual's own priority `ctrl+q` quits the app even with a
    # modal on top. Without this guard, pressing `?` while `KeymapScreen` is
    # already open would push a second `KeymapScreen` on top of itself. Don't
    # "simplify" this to `hub_only=False` on the theory that `?` is read-only
    # and modality already covers it — it doesn't, and that combination is
    # exactly how the overlay would learn to stack on itself.)
    hub_only: bool = True
    # Whether Textual should check this binding *before* the focused widget
    # gets the key. A focused Input swallows plain printable keys as typed
    # characters, and the app launches with focus in #search — so `?`, the
    # only place the other ~22 hidden hotkeys are discoverable, must be
    # priority or it's unreachable from the app's default state. Every other
    # key (including `q`) stays non-priority on purpose: a search query must
    # be able to contain any letter.
    priority: bool = False


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
        "Collapse the selected duplicates onto one survivor, or promote them to shared",
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
        priority=True,
    ),
    Command(
        "ctrl+p",
        "command_palette",
        "Commands",
        "Search every command by name",
        "Session",
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
        out.append(
            Binding(
                cmd.key,
                cmd.action,
                label,
                show=cmd.key in FOOTER_KEYS,
                priority=cmd.priority,
            )
        )
        out.extend(
            Binding(alias, cmd.action, label, show=False, priority=cmd.priority)
            for alias in cmd.aliases
        )
    return out


def priority_keys() -> frozenset[str]:
    """The literal characters of every priority command's key AND aliases.

    Textual's `Input` pre-filters any *App*-level binding for a key it could
    plausibly insert as a character — before the priority check ever runs —
    so a priority `Binding` alone doesn't make it past a focused `Input`. The
    search box needs this set to know which characters to stop claiming via
    `check_consume_key`, letting the priority binding actually fire. See
    `psc/tui/app.py`'s `SearchInput`.

    `bindings()` stamps `priority` onto alias bindings too (an alias is a
    real, working key for the same action), so this must cover aliases or a
    priority command with a printable alias would get `priority=True` in
    `bindings()` but still be silently swallowed by a focused Input on the
    alias key specifically.

    This only works because every `priority=True` command's `key` *and every
    alias* is a single character — `check_consume_key` compares against the
    typed *character* (`" "`, `None`, …), not Textual's key *name* (`"space"`,
    `"ctrl+p"`, …). A multi-character priority key or alias would silently
    never match and the override would just never fire. A table-integrity
    test in `tests/tui/test_commands.py` enforces that invariant so a
    violation fails loudly in CI instead of silently at runtime.
    """
    return frozenset(key for cmd in HUB_COMMANDS if cmd.priority for key in (cmd.key, *cmd.aliases))


def hub_actions() -> frozenset[str]:
    """Actions the spoke-stacking guard disables while a spoke is open."""
    return frozenset(cmd.action for cmd in HUB_COMMANDS if cmd.hub_only)


def by_category() -> list[tuple[str, list[Command]]]:
    """The table grouped for display, in `CATEGORIES` order."""
    return [(cat, [c for c in HUB_COMMANDS if c.category == cat]) for cat in CATEGORIES]
