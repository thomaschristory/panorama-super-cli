from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "panorama-config.xml"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PSC_CONFIG": "/nonexistent/psc-test-config.yaml"}
    return subprocess.run(
        [sys.executable, "-m", "psc", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_version() -> None:
    cp = run("--version")
    assert cp.returncode == 0
    assert "psc" in cp.stdout


def test_find_ip_json_contract() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "find", "ip", "10.0.0.10")
    assert cp.returncode == 0
    data = json.loads(cp.stdout)
    assert data["exists"] is True
    assert {m["name"] for m in data["matches"]} >= {"h-web1", "web-primary"}


def test_find_ip_exact_flag_json() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "find", "ip", "--exact", "10.0.0.10")
    assert cp.returncode == 0
    data = json.loads(cp.stdout)
    assert data["exists"] is True
    matches = data["matches"]
    assert all(m["match"] == "exact" for m in matches)
    names = {m["name"] for m in matches}
    assert {"h-web1", "web-primary"} <= names
    assert "net-10" not in names  # the /24 is CONTAINS, dropped under --exact


def test_find_ip_exact_short_flag() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "find", "ip", "-e", "10.0.0.10")
    assert cp.returncode == 0
    assert all(m["match"] == "exact" for m in json.loads(cp.stdout)["matches"])


def test_strict_not_found_exit_5() -> None:
    cp = run("-c", str(FIXTURE), "--strict", "-o", "json", "find", "ip", "203.0.113.9")
    assert cp.returncode == 5
    assert json.loads(cp.stdout)["type"] == "not_found"


def test_merge_dry_run_exit_0_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "x.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "set",
        "dedup",
        "merge",
        "--keep",
        "h-web1",
        "--remove",
        "web-primary",
    )
    assert cp.returncode == 0
    assert "delete shared address web-primary" in cp.stdout
    assert not out.exists()


def test_merge_value_mismatch_exit_6() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "json",
        "dedup",
        "merge",
        "--keep",
        "net-10",
        "--remove",
        "local-only",
        "--remove-location",
        "DG-EDGE",
    )
    assert cp.returncode == 6
    assert json.loads(cp.stdout)["type"] == "conflict"


def test_merge_apply_writes_out(tmp_path: Path) -> None:
    out = tmp_path / "fixed.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "dedup",
        "merge",
        "--keep",
        "h-web1",
        "--remove",
        "web-primary",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0
    assert out.exists()
    assert "web-primary" not in out.read_text(encoding="utf-8")


def test_no_source_errors_config() -> None:
    cp = run("-o", "json", "find", "ip", "10.0.0.10")
    assert cp.returncode == 9
    assert json.loads(cp.stdout)["type"] == "config"
