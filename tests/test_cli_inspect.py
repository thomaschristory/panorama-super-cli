from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "dedup-groups.xml"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PSC_CONFIG": "/nonexistent/psc-test-config.yaml"}
    return subprocess.run(
        [sys.executable, "-m", "psc", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_find_object_without_expand_is_unchanged() -> None:
    # Regression guard: the default one-line-summary path must not change.
    cp = run("-c", str(FIXTURE), "-o", "json", "find", "object", "grp-nested")
    assert cp.returncode == 0
    payload = json.loads(cp.stdout)
    assert payload[0]["detail"].startswith("static[")
    assert "tree" not in payload[0]


def test_find_object_expand_serializes_object_view_json() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "find", "object", "grp-nested", "-x")
    assert cp.returncode == 0
    view = json.loads(cp.stdout)
    assert view["kind"] == "address-group"
    assert view["tree"]["children"][0]["kind"] == "address"
    assert view["effective_leaves"] == ["10.0.0.2/32"]


def test_find_object_expand_table_draws_tree() -> None:
    cp = run("-c", str(FIXTURE), "-o", "table", "find", "object", "grp-parent", "-x")
    assert cp.returncode == 0
    assert "grp-b" in cp.stdout
    assert "effective:" in cp.stdout
    assert "10.0.0.1/32" in cp.stdout


def test_show_matches_find_object_expand() -> None:
    a = run("-c", str(FIXTURE), "-o", "json", "show", "grp-parent")
    b = run("-c", str(FIXTURE), "-o", "json", "find", "object", "grp-parent", "-x")
    assert a.returncode == 0
    assert a.stdout == b.stdout


def test_show_dynamic_group_flags_incomplete() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "show", "grp-dyn")
    assert cp.returncode == 0
    view = json.loads(cp.stdout)
    assert view["tree"]["status"] == "dynamic"
    assert view["effective_complete"] is False
    assert view["warnings"]


def test_show_strict_exit_code_on_no_match() -> None:
    cp = run("-c", str(FIXTURE), "--strict", "show", "does-not-exist")
    assert cp.returncode != 0
