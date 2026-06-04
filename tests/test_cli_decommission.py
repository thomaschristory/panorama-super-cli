"""CLI tests for `psc decommission` (issue #5), driven as a subprocess.

Exercises the dry-run default, the JSON contract, the blocked-plan exit code,
and an offline --apply round-trip that proves the decommissioned host is absent
from the rewritten config.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "decommission-config.xml"


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
    cp = run("-c", str(FIXTURE), "decommission", "10.1.0.5")
    assert cp.returncode == 0, cp.stderr


def test_json_plan_shape() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "decommission", "10.1.0.5")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert "reference_edits" in data
    assert "rule_deletes" in data
    assert "deletes" in data
    assert "blockers" in data
    # h-dead is sole source of r-sole-source, sole dest of r-sole-dest, and the
    # sole member of g-dead-only which r-group-only sourced — 3 orphans (the
    # third proves the cascade orphans a rule naming a deleted GROUP).
    orphan_names = {rd["name"] for rd in data["rule_deletes"]}
    assert orphan_names == {"r-sole-source", "r-sole-dest", "r-group-only"}
    # g-dead-only is emptied and deleted; the object itself is deleted.
    deleted = {d["name"] for d in data["deletes"]}
    assert {"g-dead-only", "h-dead"} <= deleted
    # net-supernet CONTAINS the /32 but is broader — never torn down.
    assert "net-supernet" not in deleted
    # g-parent survives (keeps h-keep) — it merely loses g-dead-only.
    assert "g-parent" not in deleted
    assert not data["blockers"]
    # No surviving op (edit or delete) still names the deleted group.
    for e in data["reference_edits"]:
        assert "g-dead-only" not in e["after"]


def test_blocked_nat_translation_exit_six() -> None:
    # 10.9.9.9 resolves to nat-host, named only in a NAT source-translation
    # field (unmappable) — tearing it down must block.
    cp = run("-c", str(FIXTURE), "-o", "json", "decommission", "10.9.9.9")
    assert cp.returncode == 6, cp.stdout + cp.stderr
    data = json.loads(cp.stdout)
    assert data["type"] == "conflict"
    assert data["details"]["blockers"]


def test_apply_out_writes_config_without_target(tmp_path: Path) -> None:
    out = tmp_path / "rewritten.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "decommission",
        "10.1.0.5",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stderr
    text = out.read_text()
    assert "h-dead" not in text  # object gone
    assert "r-sole-source" not in text  # orphan rule gone
    assert "g-dead-only" not in text  # emptied group gone EVERYWHERE (cascade)
    assert "r-group-only" not in text  # rule sourcing the deleted group orphaned
    assert "h-keep" in text  # untouched survivor remains
    assert "r-mixed" in text  # mixed rule survives
    assert "g-parent" in text  # parent group survives (repointed, not deleted)
    assert "net-supernet" in text  # broader CONTAINING object never touched


def test_contains_object_excluded(tmp_path: Path) -> None:
    # Decommissioning a /32 inside net-supernet (10.1.0.0/24) must NOT delete or
    # scrub the broader object — only EXACT + WITHIN are torn down.
    out = tmp_path / "rewritten.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "decommission",
        "10.1.0.5",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stderr
    cp2 = run("-c", str(FIXTURE), "-o", "json", "decommission", "10.1.0.5")
    data = json.loads(cp2.stdout)
    assert all(d["name"] != "net-supernet" for d in data["deletes"])
    assert all(e["referrer_name"] != "net-supernet" for e in data["reference_edits"])
    assert "net-supernet" in out.read_text()


def test_file_target_parsing(tmp_path: Path) -> None:
    f = tmp_path / "targets.txt"
    f.write_text("# decommission list\n10.1.0.5\n\n")
    cp = run("-c", str(FIXTURE), "-o", "json", "decommission", "--file", str(f))
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert any(d["name"] == "h-dead" for d in data["deletes"])


def test_keep_groups_skips_deletes() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "decommission", "10.1.0.5", "--keep-groups")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert data["deletes"] == []
    assert data["reference_edits"]
