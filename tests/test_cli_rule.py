from __future__ import annotations

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


def test_dry_run_set_shows_delete_then_set() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "set",
        "rule",
        "edit-member",
        "--rule",
        "allow-web",
        "--field",
        "destination",
        "--add",
        "net-10b",
    )
    assert cp.returncode == 0, cp.stderr
    out = cp.stdout
    delete_idx = out.index("delete shared pre-rulebase security rules allow-web destination")
    set_idx = out.index("set shared pre-rulebase security rules allow-web destination")
    assert delete_idx < set_idx
    assert "net-10b" in out


def test_apply_out_adds_member_to_xml(tmp_path: Path) -> None:
    out = tmp_path / "fixed.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "rule",
        "edit-member",
        "--rule",
        "allow-web",
        "--field",
        "source",
        "--add",
        "net-10",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stderr
    text = out.read_text(encoding="utf-8")
    # source had only "any"; now also carries net-10
    assert "<member>net-10</member>" in text


def test_idempotent_add_is_noop() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "rule",
        "edit-member",
        "--rule",
        "allow-web",
        "--field",
        "destination",
        "--add",
        "net-10",
    )
    assert cp.returncode == 0
    assert "nothing to do" in cp.stderr


def test_neither_add_nor_remove_exit_4() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "rule",
        "edit-member",
        "--rule",
        "allow-web",
        "--field",
        "destination",
    )
    assert cp.returncode == 4


def test_both_add_and_remove_exit_4() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "rule",
        "edit-member",
        "--rule",
        "allow-web",
        "--field",
        "destination",
        "--add",
        "x",
        "--remove",
        "y",
    )
    assert cp.returncode == 4


def test_unknown_rule_exit_5() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "rule",
        "edit-member",
        "--rule",
        "ghost",
        "--field",
        "source",
        "--add",
        "x",
    )
    assert cp.returncode == 5


def test_nat_scalar_service_blocked_exit_6() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "rule",
        "edit-member",
        "--rule",
        "nat-web",
        "--field",
        "service",
        "--add",
        "udp-53",
    )
    assert cp.returncode == 6
