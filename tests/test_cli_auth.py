"""`psc init` / `psc login` command wiring: profile bootstrap + verify/rotate.

`LiveSource.fetch_api_key` / `.verify` are monkeypatched so nothing touches a
device; we assert exit codes, the 0600 config write, and key rotation.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from psc.cli.app import app
from psc.config.loader import load_config
from psc.core.source import LiveSource, SystemInfo
from psc.output.errors import ErrorType, PscError

runner = CliRunner()

_INFO = SystemInfo(hostname="pano.example", version="11.1.0", model="Panorama", serial="0123")


@pytest.fixture
def cfg_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("PSC_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("PSC_PASSWORD", "s3cret")
    return {"path": str(tmp_path / "config.yaml")}


def test_login_no_profile_real_exit_code_9() -> None:
    """End-to-end through main(): a missing profile is a CONFIG error → exit 9."""
    env = {**os.environ, "PSC_CONFIG": "/nonexistent/psc-test-config.yaml"}
    cp = subprocess.run(
        [sys.executable, "-m", "psc", "login"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert cp.returncode == 9


def _stub_ok(monkeypatch: pytest.MonkeyPatch, key: str = "NEWKEY123") -> None:
    monkeypatch.setattr(LiveSource, "fetch_api_key", staticmethod(lambda *a, **k: key))
    monkeypatch.setattr(LiveSource, "verify", lambda self: _INFO)


def test_init_fetches_key_and_writes_0600(
    cfg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_ok(monkeypatch, key="GENERATED")
    result = runner.invoke(
        app, ["init", "--name", "prod", "--host", "pano.example", "--user", "admin"]
    )
    assert result.exit_code == 0, result.output
    path = Path(cfg_env["path"])
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    cfg = load_config()
    prof = cfg.profile("prod")
    assert prof is not None
    assert prof.api_key == "GENERATED"
    assert cfg.default_profile == "prod"


def test_init_accepts_existing_key_without_creds(
    cfg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LiveSource, "verify", lambda self: _INFO)
    # fetch_api_key must NOT be called when --api-key is supplied.
    monkeypatch.setattr(
        LiveSource,
        "fetch_api_key",
        staticmethod(lambda *a, **k: pytest.fail("keygen should be skipped")),
    )
    result = runner.invoke(app, ["init", "--name", "p", "--host", "h", "--api-key", "PASTEDKEY"])
    assert result.exit_code == 0, result.output
    assert load_config().profile("p").api_key == "PASTEDKEY"  # type: ignore[union-attr]


def test_init_non_interactive_requires_host(
    cfg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_ok(monkeypatch)
    result = runner.invoke(app, ["init", "--user", "admin"])
    # CliRunner drives the app directly, so PscError surfaces as result.exception
    # rather than the SystemExit code main() maps it to. .exit_code is the contract.
    assert isinstance(result.exception, PscError)
    assert result.exception.exit_code == 9


def test_init_auth_failure_exits_8(
    cfg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **k: object) -> str:
        raise PscError("bad creds", ErrorType.AUTH)

    monkeypatch.setattr(LiveSource, "fetch_api_key", staticmethod(boom))
    result = runner.invoke(app, ["init", "--name", "p", "--host", "h", "--user", "admin"])
    assert isinstance(result.exception, PscError)
    assert result.exception.exit_code == 8
    # nothing persisted on failure
    assert load_config().profile("p") is None


def test_init_no_verify_skips_probe(
    cfg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LiveSource, "fetch_api_key", staticmethod(lambda *a, **k: "K"))
    monkeypatch.setattr(LiveSource, "verify", lambda self: pytest.fail("verify should be skipped"))
    result = runner.invoke(
        app,
        ["init", "--name", "p", "--host", "h", "--user", "admin", "--no-verify"],
    )
    assert result.exit_code == 0, result.output
    assert load_config().profile("p").api_key == "K"  # type: ignore[union-attr]


def _seed_profile(monkeypatch: pytest.MonkeyPatch, key: str = "OLDKEY") -> None:
    _stub_ok(monkeypatch, key=key)
    runner.invoke(app, ["init", "--name", "prod", "--host", "pano.example", "--api-key", key])


def test_login_no_profile_exits_9(cfg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LiveSource, "verify", lambda self: _INFO)
    result = runner.invoke(app, ["login"])
    assert isinstance(result.exception, PscError)
    assert result.exception.exit_code == 9


def test_login_verifies_existing_key(
    cfg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_profile(monkeypatch, key="OLDKEY")
    monkeypatch.setattr(LiveSource, "verify", lambda self: _INFO)
    result = runner.invoke(app, ["login"])
    assert result.exit_code == 0, result.output
    # no rotation: key unchanged
    assert load_config().profile("prod").api_key == "OLDKEY"  # type: ignore[union-attr]


def test_login_rotates_key_with_user(
    cfg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_profile(monkeypatch, key="OLDKEY")
    monkeypatch.setattr(LiveSource, "fetch_api_key", staticmethod(lambda *a, **k: "ROTATED"))
    monkeypatch.setattr(LiveSource, "verify", lambda self: _INFO)
    result = runner.invoke(app, ["login", "--user", "admin"])
    assert result.exit_code == 0, result.output
    assert load_config().profile("prod").api_key == "ROTATED"  # type: ignore[union-attr]


def test_login_auth_failure_exits_8_no_save(
    cfg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_profile(monkeypatch, key="OLDKEY")
    monkeypatch.setattr(LiveSource, "fetch_api_key", staticmethod(lambda *a, **k: "ROTATED"))

    def boom(self: object) -> SystemInfo:
        raise PscError("stale", ErrorType.AUTH)

    monkeypatch.setattr(LiveSource, "verify", boom)
    result = runner.invoke(app, ["login", "--user", "admin"])
    assert isinstance(result.exception, PscError)
    assert result.exception.exit_code == 8
    # rotation not persisted when the verify probe fails
    assert load_config().profile("prod").api_key == "OLDKEY"  # type: ignore[union-attr]
