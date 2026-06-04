"""`psc profile list` surfaces the config file location (issue #48).

Driven through `python -m psc` so stdout (machine rows) and stderr (the
informational `config file:` line) are captured separately — the whole point is
that the path does not pollute machine output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run(cfg_path: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PSC_CONFIG": cfg_path}
    return subprocess.run(
        [sys.executable, "-m", "psc", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_profile_list_prints_config_path_on_stderr(tmp_path: Path) -> None:
    cfg = tmp_path / "psc" / "config.yaml"
    cp = _run(str(cfg), "profile", "list")
    assert cp.returncode == 0
    # Path goes to stderr, flagged as not-yet-created when absent.
    assert "config file:" in cp.stderr
    assert str(cfg) in cp.stderr
    assert "not created yet" in cp.stderr


def test_config_path_absent_from_json_stdout(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cp = _run(str(cfg), "-o", "json", "profile", "list")
    assert cp.returncode == 0
    # stdout stays a clean, parseable rows array — no path leaked in.
    assert json.loads(cp.stdout) == []
    assert str(cfg) not in cp.stdout


def test_existing_config_not_flagged_as_missing(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("profiles: []\n", encoding="utf-8")
    cp = _run(str(cfg), "profile", "list")
    assert cp.returncode == 0
    assert str(cfg) in cp.stderr
    assert "not created yet" not in cp.stderr


def test_path_printed_with_profiles_present_stdout_carries_rows(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "default_profile: prod\n"
        "profiles:\n"
        "  - name: prod\n"
        "    hostname: pano.example\n"
        "    api_key: SECRET\n"
        "    port: 443\n",
        encoding="utf-8",
    )
    cp = _run(str(cfg), "-o", "json", "profile", "list")
    assert cp.returncode == 0
    # The path (the file we actually loaded) is on stderr...
    assert str(cfg) in cp.stderr
    assert "not created yet" not in cp.stderr
    # ...and the rows — never the secret — are the clean stdout payload.
    rows = json.loads(cp.stdout)
    assert [r["name"] for r in rows] == ["prod"]
    assert "SECRET" not in cp.stdout
