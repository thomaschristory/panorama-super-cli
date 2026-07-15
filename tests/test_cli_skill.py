"""CLI tests for `psc skill install` / `psc skill export` (issue #165)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from psc.cli.app import app as cli_app

runner = CliRunner()


def _run(*args: str, home: Path) -> subprocess.CompletedProcess[str]:
    """Invoke `psc` as a subprocess so the real main() error contract runs."""
    env = {
        **os.environ,
        "HOME": str(home),
        "PSC_CONFIG": "/nonexistent/psc-test-config.yaml",
    }
    return subprocess.run(
        [sys.executable, "-m", "psc", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point HOME and PSC_CONFIG at throwaway locations so nothing real is touched."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PSC_CONFIG", str(tmp_path / "no-such-config.yaml"))


_TARGET_DESTS = {
    "claude-code": ".claude/skills/panorama-super-cli/SKILL.md",
    "codex": ".agents/skills/panorama-super-cli/SKILL.md",
    "gemini": ".gemini/skills/panorama-super-cli/SKILL.md",
    "copilot": ".copilot/skills/panorama-super-cli/SKILL.md",
}


@pytest.mark.parametrize("target", list(_TARGET_DESTS))
def test_install_dry_run_does_not_write(target: str, tmp_path: Path) -> None:
    res = runner.invoke(cli_app, ["-o", "json", "skill", "install", "-t", target])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["mode"] == "dry-run"
    assert payload["target"] == target
    dest = tmp_path / "home" / _TARGET_DESTS[target]
    assert payload["destination"] == str(dest)
    assert not dest.exists()  # dry-run must not write


@pytest.mark.parametrize("target", list(_TARGET_DESTS))
def test_install_apply_writes_the_skill(target: str, tmp_path: Path) -> None:
    res = runner.invoke(cli_app, ["-o", "json", "skill", "install", "-t", target, "--apply"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["mode"] == "apply"
    assert payload["written"] is True
    dest = tmp_path / "home" / _TARGET_DESTS[target]
    assert dest.is_file()
    text = dest.read_text(encoding="utf-8")
    assert "name: panorama-super-cli" in text


def test_install_apply_is_idempotent(tmp_path: Path) -> None:
    for _ in range(2):
        res = runner.invoke(
            cli_app, ["-o", "json", "skill", "install", "-t", "claude-code", "--apply"]
        )
        assert res.exit_code == 0, res.output
    dest = tmp_path / "home" / _TARGET_DESTS["claude-code"]
    assert dest.is_file()


def test_install_unknown_target_is_rejected_cleanly() -> None:
    res = runner.invoke(cli_app, ["skill", "install", "-t", "emacs"])
    assert res.exit_code != 0
    # A clean rejection, never a raw traceback.
    assert "Traceback" not in res.output


def test_install_table_output_renders() -> None:
    res = runner.invoke(cli_app, ["-o", "table", "skill", "install", "-t", "claude-code"])
    assert res.exit_code == 0, res.output
    assert "claude-code" in res.stdout


def test_export_dry_run_does_not_write(tmp_path: Path) -> None:
    out = tmp_path / "somewhere"
    res = runner.invoke(cli_app, ["-o", "json", "skill", "export", str(out)])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["mode"] == "dry-run"
    dest = out / "panorama-super-cli" / "SKILL.md"
    assert payload["destination"] == str(dest.resolve())
    assert not dest.exists()


def test_export_apply_writes_under_named_dir(tmp_path: Path) -> None:
    out = tmp_path / "somewhere"
    res = runner.invoke(cli_app, ["-o", "json", "skill", "export", str(out), "--apply"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["written"] is True
    dest = out / "panorama-super-cli" / "SKILL.md"
    assert dest.is_file()
    assert "name: panorama-super-cli" in dest.read_text(encoding="utf-8")


def test_overwrite_is_signalled_before_apply(tmp_path: Path) -> None:
    """A second install must flag `overwrite` in the (dry-run) plan."""
    res = runner.invoke(cli_app, ["-o", "json", "skill", "install", "-t", "claude-code", "--apply"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["overwrite"] is False  # first write, nothing there yet

    res = runner.invoke(cli_app, ["-o", "json", "skill", "install", "-t", "claude-code"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["overwrite"] is True
    assert payload["mode"] == "dry-run"
    assert payload["written"] is False


def test_export_onto_a_file_yields_typed_error_not_traceback(tmp_path: Path) -> None:
    """A bad --apply target must surface the typed JSON envelope, exit 3 — no traceback."""
    blocker = tmp_path / "occupied"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    cp = _run("-o", "json", "skill", "export", str(blocker), "--apply", home=tmp_path / "home")
    assert cp.returncode == 3, cp.stderr  # ErrorType.INPUT
    envelope = json.loads(cp.stdout)  # stdout stays a valid machine document
    assert envelope["type"] == "input"
    assert "Traceback" not in cp.stderr
