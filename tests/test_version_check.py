"""`psc version` / `psc version check` — update detection (issue #33).

The PyPI fetch is monkeypatched everywhere; no test touches the network.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from psc import __version__
from psc.cli.app import app
from psc.core import version_check
from psc.output.errors import ErrorType, PscError

runner = CliRunner()


def _stub_latest(monkeypatch: pytest.MonkeyPatch, latest: str) -> None:
    monkeypatch.setattr(version_check, "_fetch_latest", lambda url, timeout: latest)


# --- core engine -----------------------------------------------------------


def test_update_available_when_remote_newer(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_latest(monkeypatch, "999.0.0")
    info = version_check.check_for_update()
    assert info.installed == __version__
    assert info.latest == "999.0.0"
    assert info.update_available is True


def test_no_update_when_equal(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_latest(monkeypatch, __version__)
    info = version_check.check_for_update()
    assert info.update_available is False


def test_no_update_when_remote_older(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_latest(monkeypatch, "0.0.1")
    assert version_check.check_for_update().update_available is False


def test_transport_error_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(url: str, timeout: float) -> str:
        raise OSError("network down")

    monkeypatch.setattr(version_check, "_fetch_latest", _boom)
    with pytest.raises(PscError) as exc:
        version_check.check_for_update()
    assert exc.value.error_type is ErrorType.TRANSPORT


def test_malformed_response_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _bad(url: str, timeout: float) -> str:
        raise KeyError("info")

    monkeypatch.setattr(version_check, "_fetch_latest", _bad)
    with pytest.raises(PscError) as exc:
        version_check.check_for_update()
    assert exc.value.error_type is ErrorType.TRANSPORT


def test_non_json_body_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    # PyPI serving an HTML error page → json.load raises JSONDecodeError, a
    # ValueError subclass — must still surface as a typed TRANSPORT error.
    def _html(url: str, timeout: float) -> str:
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(version_check, "_fetch_latest", _html)
    with pytest.raises(PscError) as exc:
        version_check.check_for_update()
    assert exc.value.error_type is ErrorType.TRANSPORT


def test_unparseable_remote_version_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_latest(monkeypatch, "not-a-version")
    info = version_check.check_for_update()
    # Cannot PEP 440-compare, so a differing string counts as "available".
    assert info.update_available is True


# --- CLI -------------------------------------------------------------------


def test_version_command_matches_flag() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.output
    assert __version__ in result.stdout


def test_version_command_json() -> None:
    result = runner.invoke(app, ["-o", "json", "version"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["version"] == __version__


def test_version_check_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_latest(monkeypatch, "999.0.0")
    result = runner.invoke(app, ["-o", "json", "version", "check"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["installed"] == __version__
    assert data["latest"] == "999.0.0"
    assert data["update_available"] is True
