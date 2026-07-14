from __future__ import annotations

from psc.tui.app import WorkbenchApp
from psc.tui.commands import (
    CATEGORIES,
    FOOTER_KEYS,
    HUB_COMMANDS,
    bindings,
    by_category,
    hub_actions,
)


def test_every_command_has_an_action_method() -> None:
    # The table is the source of truth; a row with no handler is a dead key.
    for cmd in HUB_COMMANDS:
        assert hasattr(WorkbenchApp, f"action_{cmd.action}"), cmd.action


def test_every_hub_action_method_is_in_the_table() -> None:
    # The converse: a spoke wired up but never added to the table would be
    # invisible in the ? overlay and the palette. This is the test that keeps
    # the single source of truth honest.
    handled = {c.action for c in HUB_COMMANDS}
    # Actions Textual itself provides (quit, command_palette) are in the table;
    # these are the app's own action_* methods.
    own = {
        name.removeprefix("action_") for name in vars(WorkbenchApp) if name.startswith("action_")
    }
    assert own <= handled, own - handled


def test_no_duplicate_keys() -> None:
    keys = [k for c in HUB_COMMANDS for k in (c.key, *c.aliases)]
    assert len(keys) == len(set(keys)), "a key is bound twice"


def test_no_duplicate_actions() -> None:
    actions = [c.action for c in HUB_COMMANDS]
    assert len(actions) == len(set(actions))


def test_categories_are_all_known() -> None:
    assert {c.category for c in HUB_COMMANDS} <= set(CATEGORIES)


def test_hub_actions_excludes_quit_and_palette() -> None:
    # quit and the command palette must stay live while a spoke is open;
    # every other action is gated by the spoke-stacking guard.
    actions = hub_actions()
    assert "quit" not in actions
    assert "command_palette" not in actions
    assert "dedup" in actions
    assert "staged" in actions


def test_bindings_cover_keys_and_aliases() -> None:
    bound = {b.key for b in bindings()}
    for cmd in HUB_COMMANDS:
        assert cmd.key in bound
        for alias in cmd.aliases:
            assert alias in bound


def test_remove_selected_keeps_both_keys() -> None:
    # delete and backspace both drop the focused selection row (#91).
    by_key = {b.key: b.action for b in bindings()}
    assert by_key["delete"] == "remove_selected"
    assert by_key["backspace"] == "remove_selected"


def test_only_footer_keys_are_shown() -> None:
    # The whole point of the rework: the footer advertises three keys, not 22.
    shown = {b.key for b in bindings() if b.show}
    assert shown == set(FOOTER_KEYS)


def test_footer_keys_are_the_three_discovery_keys() -> None:
    assert frozenset({"?", "ctrl+p", "q"}) == FOOTER_KEYS


def test_by_category_returns_every_command_in_category_order() -> None:
    grouped = by_category()
    assert [name for name, _ in grouped] == list(CATEGORIES)
    flat = [cmd for _name, cmds in grouped for cmd in cmds]
    assert len(flat) == len(HUB_COMMANDS)


def test_descriptions_are_real_sentences() -> None:
    # Not 4-char footer labels — these are what the ? overlay and palette show.
    for cmd in HUB_COMMANDS:
        assert len(cmd.description) > 15, cmd.action
        assert cmd.title
