"""CLI tests for `psc move` (issue #74), driven as a subprocess.

Uses the nested device-group fixture: `h-dc` lives in EMEA-DC and is referenced
(via grp-prod) from the EMEA-DC-PROD descendant, so promoting it to shared is a
clean, reference-safe move. `h-shared` exists at both shared and EMEA-DC with
*different* values, so promoting that one must be refused.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from defusedxml.ElementTree import fromstring as xml_fromstring

FIXTURE = Path(__file__).parent / "fixtures" / "nested-device-groups.xml"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PSC_CONFIG": "/nonexistent/psc-test-config.yaml"}
    return subprocess.run(
        [sys.executable, "-m", "psc", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_dry_run_exit_zero() -> None:
    cp = run("-c", str(FIXTURE), "move", "address", "h-dc", "--from", "EMEA-DC", "--to", "shared")
    assert cp.returncode == 0, cp.stderr


def test_json_plan_shape() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "json",
        "move",
        "address",
        "h-dc",
        "--from",
        "EMEA-DC",
        "--to",
        "shared",
    )
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert not data["blockers"]
    assert data["reference_edits"] == []  # promotion never repoints
    upsert = data["upserts"][0]
    assert (upsert["name"], upsert["location"], upsert["exists"]) == ("h-dc", "shared", False)
    delete = data["deletes"][0]
    assert (delete["name"], delete["location"]) == ("h-dc", "EMEA-DC")


def test_different_value_collision_blocked_exit_six() -> None:
    # h-shared is 10.0.0.1/32 in shared but 10.9.9.9/32 in EMEA-DC — promoting
    # the DG copy would collide with a different value, so refuse (exit 6).
    cp = run(
        "-c",
        str(FIXTURE),
        "move",
        "address",
        "h-shared",
        "--from",
        "EMEA-DC",
        "--to",
        "shared",
    )
    assert cp.returncode == 6, cp.stdout + cp.stderr


def test_sibling_direction_blocked_exit_six() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "move",
        "address",
        "h-dc",
        "--from",
        "EMEA-DC",
        "--to",
        "EMEA-DC-PROD",
    )
    assert cp.returncode == 6, cp.stdout + cp.stderr


def test_apply_out_round_trips(tmp_path: Path) -> None:
    out = tmp_path / "rewritten.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "move",
        "address",
        "h-dc",
        "--from",
        "EMEA-DC",
        "--to",
        "shared",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stderr
    root = xml_fromstring(out.read_text())

    shared_names = {e.get("name") for e in root.findall("./shared/address/entry")}
    assert "h-dc" in shared_names  # created at shared

    dc = next(
        e for e in root.findall("./devices/entry/device-group/entry") if e.get("name") == "EMEA-DC"
    )
    dc_names = {e.get("name") for e in dc.findall("./address/entry")}
    assert "h-dc" not in dc_names  # removed from source

    # The group member text is unchanged — it now resolves to the shared copy.
    assert "grp-prod" in out.read_text()


def test_set_output_renders_create_and_delete() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "set",
        "move",
        "address",
        "h-dc",
        "--from",
        "EMEA-DC",
        "--to",
        "shared",
    )
    assert cp.returncode == 0, cp.stderr
    out = cp.stdout
    assert "shared" in out and "h-dc" in out
    assert "delete" in out
