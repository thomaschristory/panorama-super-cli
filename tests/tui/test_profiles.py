"""Framework-free config/profile CRUD helpers for the TUI (issue #83).

These functions are pure `Config -> Config` transforms (no Textual). The TUI
screen collects fields and calls them, then persists via `save_config`. Tests
here cover the mutation semantics AND a round-trip through save/load so the
persisted YAML matches what the helper produced.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from psc.config.loader import load_config, save_config
from psc.config.models import Config, Profile
from psc.output.errors import ErrorType, PscError
from psc.tui.profiles import (
    add_or_update_profile,
    remove_profile,
    set_default_output,
    set_default_profile,
)


def _configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "psc" / "config.yaml"
    monkeypatch.setenv("PSC_CONFIG", str(path))
    return path


# --- add_or_update_profile -------------------------------------------------


def test_add_profile_appends_new() -> None:
    cfg = Config()
    out = add_or_update_profile(cfg, Profile(name="p", hostname="pano"))
    assert [p.name for p in out.profiles] == ["p"]
    assert out.profiles[0].hostname == "pano"


def test_add_profile_by_same_name_updates_in_place() -> None:
    # An existing name is an UPDATE (upsert), matching `psc profile add`.
    cfg = Config(profiles=[Profile(name="p", hostname="old", api_key="OLD")])
    out = add_or_update_profile(
        cfg, Profile(name="p", hostname="new", api_key="NEW", device_group="dg1")
    )
    assert len(out.profiles) == 1
    assert out.profiles[0].hostname == "new"
    assert out.profiles[0].api_key == "NEW"
    assert out.profiles[0].device_group == "dg1"


def test_add_profile_preserves_order_on_update() -> None:
    cfg = Config(profiles=[Profile(name="a", hostname="ha"), Profile(name="b", hostname="hb")])
    out = add_or_update_profile(cfg, Profile(name="a", hostname="ha2"))
    assert [p.name for p in out.profiles] == ["a", "b"]
    assert out.profiles[0].hostname == "ha2"


def test_add_profile_round_trips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configured(monkeypatch, tmp_path)
    cfg = add_or_update_profile(
        Config(), Profile(name="p", hostname="pano.example", api_key="SECRET")
    )
    save_config(cfg)
    loaded = load_config()
    assert [p.name for p in loaded.profiles] == ["p"]
    assert loaded.profiles[0].api_key == "SECRET"


# --- remove_profile --------------------------------------------------------


def test_remove_profile_drops_it() -> None:
    cfg = Config(profiles=[Profile(name="a", hostname="ha"), Profile(name="b", hostname="hb")])
    out = remove_profile(cfg, "a")
    assert [p.name for p in out.profiles] == ["b"]


def test_remove_profile_clears_default_when_matched() -> None:
    cfg = Config(
        default_profile="a",
        profiles=[Profile(name="a", hostname="ha"), Profile(name="b", hostname="hb")],
    )
    out = remove_profile(cfg, "a")
    assert out.default_profile is None


def test_remove_profile_keeps_default_when_unmatched() -> None:
    cfg = Config(
        default_profile="b",
        profiles=[Profile(name="a", hostname="ha"), Profile(name="b", hostname="hb")],
    )
    out = remove_profile(cfg, "a")
    assert out.default_profile == "b"


def test_remove_missing_profile_is_rejected() -> None:
    cfg = Config(profiles=[Profile(name="a", hostname="ha")])
    with pytest.raises(PscError) as exc:
        remove_profile(cfg, "nope")
    assert exc.value.error_type is ErrorType.NOT_FOUND


def test_remove_profile_round_trips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configured(monkeypatch, tmp_path)
    cfg = Config(
        default_profile="a",
        profiles=[Profile(name="a", hostname="ha"), Profile(name="b", hostname="hb")],
    )
    save_config(remove_profile(cfg, "a"))
    loaded = load_config()
    assert [p.name for p in loaded.profiles] == ["b"]
    assert loaded.default_profile is None


# --- set_default_profile ---------------------------------------------------


def test_set_default_profile_to_existing() -> None:
    cfg = Config(profiles=[Profile(name="a", hostname="ha"), Profile(name="b", hostname="hb")])
    out = set_default_profile(cfg, "b")
    assert out.default_profile == "b"


def test_set_default_profile_to_missing_is_rejected() -> None:
    cfg = Config(profiles=[Profile(name="a", hostname="ha")])
    with pytest.raises(PscError) as exc:
        set_default_profile(cfg, "nope")
    assert exc.value.error_type is ErrorType.NOT_FOUND


def test_set_default_profile_round_trips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configured(monkeypatch, tmp_path)
    cfg = Config(profiles=[Profile(name="a", hostname="ha"), Profile(name="b", hostname="hb")])
    save_config(set_default_profile(cfg, "b"))
    assert load_config().default_profile == "b"


# --- set_default_output ----------------------------------------------------


def test_set_default_output_valid() -> None:
    out = set_default_output(Config(), "json")
    assert out.defaults.output == "json"


def test_set_default_output_invalid_is_rejected() -> None:
    with pytest.raises(PscError) as exc:
        set_default_output(Config(), "toml")
    assert exc.value.error_type is ErrorType.VALIDATION
