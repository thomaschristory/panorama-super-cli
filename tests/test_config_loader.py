"""Config persistence: secrets are written with restrictive perms from creation.

`save_config` writes the API key to disk, so the file must never exist in a
world-readable state — not even for the brief window between create and chmod.
The parent `~/.psc` dir must likewise be private.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from psc.config.loader import load_config, save_config
from psc.config.models import Config, Profile

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits are meaningless on Windows"
)


def _configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "psc" / "config.yaml"
    monkeypatch.setenv("PSC_CONFIG", str(path))
    return path


@_POSIX_ONLY
def test_save_config_file_is_0600(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = _configured(monkeypatch, tmp_path)
    save_config(Config(profiles=[Profile(name="p", hostname="h", api_key="SECRET")]))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@_POSIX_ONLY
def test_save_config_tightens_preexisting_loose_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # O_TRUNC reuses an existing inode and keeps its mode bits, so a config file
    # left 0644 by an older psc must be repaired to 0600 by the post-write chmod.
    path = _configured(monkeypatch, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stale", encoding="utf-8")
    os.chmod(path, 0o644)
    save_config(Config(profiles=[Profile(name="p", hostname="h", api_key="SECRET")]))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@_POSIX_ONLY
def test_save_config_parent_dir_is_0700(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = _configured(monkeypatch, tmp_path)
    save_config(Config())
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


@_POSIX_ONLY
def test_save_config_never_world_readable_even_with_open_umask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An open umask must not leak the secret: the file is created 0600 atomically,
    # so no other-readable bits can ever appear regardless of the process umask.
    path = _configured(monkeypatch, tmp_path)
    old = os.umask(0o000)
    try:
        save_config(Config(profiles=[Profile(name="p", hostname="h", api_key="SECRET")]))
    finally:
        os.umask(old)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


@_POSIX_ONLY
def test_save_config_tightens_preexisting_loose_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _configured(monkeypatch, tmp_path)
    path.parent.mkdir(parents=True)
    os.chmod(path.parent, 0o755)  # simulate a pre-existing world-readable dir
    save_config(Config())
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_save_then_load_round_trips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configured(monkeypatch, tmp_path)
    cfg = Config(
        default_profile="p",
        profiles=[Profile(name="p", hostname="pano.example", api_key="SECRET")],
    )
    save_config(cfg)
    loaded = load_config()
    assert loaded.default_profile == "p"
    assert loaded.profiles[0].api_key == "SECRET"
