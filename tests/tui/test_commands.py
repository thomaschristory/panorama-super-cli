from __future__ import annotations

import pytest
from textual.binding import Binding

import psc.tui.commands as commands_module
from psc.tui.app import WorkbenchApp
from psc.tui.commands import (
    CATEGORIES,
    FOOTER_KEYS,
    HUB_COMMANDS,
    Command,
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


def test_keymap_is_hub_only() -> None:
    # `?` must stay gated by the spoke-stacking guard (hub_only=True). Modality
    # does NOT protect it — Textual's priority-binding dispatch walks the
    # *screen's* binding chain, not a modal-aware one, so a priority binding
    # (which `?` is) fires straight through an open ModalScreen. Without this
    # guard, pressing `?` while KeymapScreen is already open would push a
    # second KeymapScreen on top of itself instead of doing nothing.
    keymap = next(c for c in HUB_COMMANDS if c.action == "keymap")
    assert keymap.hub_only is True


def test_hub_actions_excludes_only_quit() -> None:
    # quit must stay live while a spoke is open; every other action, including
    # the command palette (#1 — every one of its commands assumes the hub, so
    # opening it over a spoke let you pick a command that silently did
    # nothing), is gated by the spoke-stacking guard.
    actions = hub_actions()
    assert "quit" not in actions
    assert "command_palette" in actions
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


# The expected (key, action) pairs, spelled out independently of HUB_COMMANDS
# and bindings() — both derive WorkbenchApp.BINDINGS from the same function
# over the same table, so comparing them to *each other* (as this test used
# to) is a tautology: swap the actions of two keys in the table and both
# sides swap identically, so the comparison still passes. Swap `d`/`D` in
# HUB_COMMANDS and this list is what actually catches it.
_EXPECTED_KEY_ACTION_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("space", "toggle_row"),
        ("v", "inspect"),
        ("delete", "remove_selected"),
        ("backspace", "remove_selected"),
        ("c", "create"),
        ("r", "rename"),
        ("m", "move"),
        ("G", "group_add"),
        ("N", "group_new"),
        ("e", "rule_edit"),
        ("x", "decommission"),
        ("d", "dedup"),
        ("D", "duplicates"),
        ("u", "usage"),
        ("a", "audit"),
        ("i", "unused"),
        ("g", "dangling"),
        ("f", "diff"),
        ("l", "name_lint"),
        ("n", "name_apply"),
        ("s", "staged"),
        ("o", "export"),
        ("p", "profiles"),
        ("?", "keymap"),
        ("ctrl+p", "command_palette"),
        ("q", "quit"),
    }
)


def test_app_bindings_are_derived_from_the_table() -> None:
    # Compare (key, action) pairs, not just the set of keys — a hypothetical
    # swapped pairing (same keys, wrong action wired to one of them) would
    # pass a keys-only comparison but must fail here.
    app_pairs = {(b.key, b.action) for b in WorkbenchApp.BINDINGS if isinstance(b, Binding)}
    assert app_pairs == {(b.key, b.action) for b in bindings()}
    # Both sides above are derived from HUB_COMMANDS by the same function, so
    # that comparison alone can't catch a wrong pairing baked into the table
    # itself. Pin the actual mapping against a spec that isn't table-derived.
    assert app_pairs == _EXPECTED_KEY_ACTION_PAIRS


def test_app_hub_actions_are_derived_from_the_table() -> None:
    assert hub_actions() == WorkbenchApp._HUB_ACTIONS


def test_question_mark_is_the_only_priority_binding() -> None:
    # '?' is the sole discovery surface for the ~22 hidden hotkeys, so it must
    # survive a focused Input (priority=True). Every other key — including
    # 'q' — must stay swallowable by a focused Input, so a search query can
    # contain any letter. This guards against that asymmetry eroding.
    priority_keys = {b.key for b in bindings() if b.priority}
    assert priority_keys == {"?"}


def test_priority_commands_have_single_character_keys() -> None:
    # priority_keys() (used by SearchInput.check_consume_key) compares against
    # a typed *character* (" ", None, ...), not a Textual key *name* ("space",
    # "ctrl+p", ...). A priority command whose key OR alias isn't a single
    # character would silently never make it past a focused Input — the
    # override would just never fire. Fail loudly here instead of letting
    # that happen at runtime. Covering aliases matters because bindings()
    # stamps priority onto alias bindings too (see
    # test_aliased_priority_command_yields_priority_on_key_and_aliases below).
    for cmd in HUB_COMMANDS:
        if cmd.priority:
            assert len(cmd.key) == 1, cmd.action
            assert all(len(a) == 1 for a in cmd.aliases), cmd.action


def test_aliased_priority_command_yields_priority_on_key_and_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression (#2): bindings() used to drop `priority` when expanding an
    # alias, so an aliased priority command would quietly lose priority on
    # the alias only. No real table row combines priority + aliases today,
    # so exercise it against a monkeypatched table rather than waiting for a
    # future row to collide with the single-character-key invariant.
    synthetic = (
        Command(
            "?",
            "keymap",
            "Keys",
            "Show every key binding, grouped by what it does",
            "Session",
            aliases=("h",),
            priority=True,
        ),
    )
    monkeypatch.setattr(commands_module, "HUB_COMMANDS", synthetic)
    by_key = {b.key: b.priority for b in commands_module.bindings()}
    assert by_key["?"] is True
    assert by_key["h"] is True


def test_priority_keys_covers_aliases_too(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: priority_keys() used to return only cmd.key, never
    # cmd.aliases, so a priority command's printable alias would get
    # priority=True in bindings() but SearchInput.check_consume_key would
    # still swallow it — the alias would silently never fire from the search
    # box, the app's default focus. Both key and alias must come back here.
    synthetic = (
        Command(
            "?",
            "keymap",
            "Keys",
            "Show every key binding, grouped by what it does",
            "Session",
            aliases=("h",),
            priority=True,
        ),
    )
    monkeypatch.setattr(commands_module, "HUB_COMMANDS", synthetic)
    assert commands_module.priority_keys() == {"?", "h"}
