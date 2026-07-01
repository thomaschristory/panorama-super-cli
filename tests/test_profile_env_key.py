"""`PSC_API_KEY` env override: keep the key off disk.

Precedence is env > profile file: when `PSC_API_KEY` is set and non-empty it
overrides the on-disk `api_key`, so a user can leave the config secret-free.
An unset or empty var falls back to the stored key.
"""

from __future__ import annotations

import pytest

from psc.config.models import Profile


def test_env_overrides_profile_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSC_API_KEY", "ENVKEY")
    prof = Profile(name="p", hostname="h", api_key="DISKKEY")
    assert prof.resolved_api_key() == "ENVKEY"


def test_empty_env_falls_back_to_profile_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSC_API_KEY", "")
    prof = Profile(name="p", hostname="h", api_key="DISKKEY")
    assert prof.resolved_api_key() == "DISKKEY"


def test_unset_env_uses_profile_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PSC_API_KEY", raising=False)
    prof = Profile(name="p", hostname="h", api_key="DISKKEY")
    assert prof.resolved_api_key() == "DISKKEY"


def test_live_source_uses_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSC_API_KEY", "ENVKEY")
    prof = Profile(name="p", hostname="h", api_key="DISKKEY")
    src = prof.to_live_source()
    assert src._api_key == "ENVKEY"
    assert src.hostname == "h"
