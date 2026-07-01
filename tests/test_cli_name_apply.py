"""CLI tests for `psc name apply --all` (issue #15), driven as a subprocess.

Exercises the dry-run default, the JSON contract, the blocked-plan exit code on
a scheme-name collision, and an offline --apply round-trip that proves every
non-compliant object was renamed to its scheme name with references repointed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "name-apply-all.xml"
COLLISION = Path(__file__).parent / "fixtures" / "name-apply-all-collision.xml"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PSC_CONFIG": "/nonexistent/psc-test-config.yaml"}
    return subprocess.run(
        [sys.executable, "-m", "psc", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_dry_run_default_exit_zero_no_write(tmp_path: Path) -> None:
    # Dry-run is the default: exit 0, a plan on stdout, and (crucially) the
    # source fixture is untouched and no --out artifact appears.
    before = FIXTURE.read_text()
    cp = run("-c", str(FIXTURE), "name", "apply", "--all")
    assert cp.returncode == 0, cp.stderr
    assert "dry-run" in cp.stderr
    assert FIXTURE.read_text() == before


def test_json_plan_renames_all_noncompliant() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "name", "apply", "--all")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert not data["blockers"]
    renames = {r["old_name"]: r["new_name"] for r in data["renames"]}
    # h-a/h-b renamed; the already-compliant H-10.0.0.3 is left out.
    assert renames == {"h-a": "H-10.0.0.1", "h-b": "H-10.0.0.2"}
    # The group referrer names all three; its rewrite carries the final names.
    grp = next(e for e in data["reference_edits"] if e["referrer_name"] == "grp")
    assert grp["after"] == ["H-10.0.0.1", "H-10.0.0.2", "H-10.0.0.3"]


def test_collision_exit_six() -> None:
    # h-a → H-10.0.0.1 collides with an existing object of that name → blocked.
    cp = run("-c", str(COLLISION), "-o", "json", "name", "apply", "--all")
    assert cp.returncode == 6, cp.stdout + cp.stderr
    data = json.loads(cp.stdout)
    assert data["type"] == "conflict"
    assert any("H-10.0.0.1" in b for b in data["details"]["blockers"])


def test_object_and_all_are_mutually_exclusive() -> None:
    cp = run("-c", str(FIXTURE), "name", "apply", "--object", "h-a", "--all")
    assert cp.returncode != 0
    cp2 = run("-c", str(FIXTURE), "name", "apply")
    assert cp2.returncode != 0  # neither given


def test_apply_out_round_trips_renamed_objects(tmp_path: Path) -> None:
    out = tmp_path / "rewritten.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "name",
        "apply",
        "--all",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stderr
    text = out.read_text()
    # New scheme names present; old names gone from the rewritten config.
    assert 'name="H-10.0.0.1"' in text
    assert 'name="H-10.0.0.2"' in text
    assert 'name="h-a"' not in text
    assert 'name="h-b"' not in text
    # The group's member list was repointed to the new names.
    assert "<member>H-10.0.0.1</member>" in text
    assert "<member>H-10.0.0.2</member>" in text
