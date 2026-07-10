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


def test_add_member_dry_run_plans_reference_edit() -> None:
    cp = run(
        "-c", str(FIXTURE), "-o", "json", "group", "edit-member", "--group", "grp-a", "--add", "h3"
    )
    assert cp.returncode == 0
    cs = json.loads(cp.stdout)
    edit = cs["reference_edits"][0]
    assert edit["referrer_kind"] == "address-group"
    assert edit["after"] == ["h1", "h2", "h3"]


def test_set_output_uses_static_leaf() -> None:
    cp = run(
        "-c", str(FIXTURE), "-o", "set", "group", "edit-member", "--group", "grp-a", "--add", "h3"
    )
    assert cp.returncode == 0
    assert "delete shared address-group grp-a static" in cp.stdout
    assert "set shared address-group grp-a static [ h1 h2 h3 ]" in cp.stdout


def test_requires_exactly_one_of_add_remove() -> None:
    both = run(
        "-c",
        str(FIXTURE),
        "group",
        "edit-member",
        "--group",
        "grp-a",
        "--add",
        "h3",
        "--remove",
        "h1",
    )
    assert both.returncode == 4
    neither = run("-c", str(FIXTURE), "group", "edit-member", "--group", "grp-a")
    assert neither.returncode == 4


def test_dynamic_group_rejected() -> None:
    cp = run("-c", str(FIXTURE), "group", "edit-member", "--group", "grp-dyn", "--add", "h1")
    assert cp.returncode == 4
    assert "dynamic" in cp.stderr.lower() or "dynamic" in cp.stdout.lower()


def test_apply_offline_writes_group_member(tmp_path: Path) -> None:
    out = tmp_path / "after.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "group",
        "edit-member",
        "--group",
        "grp-a",
        "--add",
        "h3",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0
    assert out.exists()
    text = out.read_text()
    # The member list now contains h3 under the group's static field.
    assert "h3" in text
