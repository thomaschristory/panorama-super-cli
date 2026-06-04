from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_OVERLAP_CONFIG = """<?xml version="1.0"?>
<config version="11.0.0">
  <shared>
    <address>
      <entry name="net-24"><ip-netmask>10.0.0.0/24</ip-netmask></entry>
      <entry name="host-10"><ip-netmask>10.0.0.10</ip-netmask></entry>
    </address>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group>
        <entry name="DG1">
          <address>
            <entry name="dg-host"><ip-netmask>10.0.0.20</ip-netmask></entry>
          </address>
        </entry>
      </device-group>
    </entry>
  </devices>
</config>
"""

_NO_OVERLAP_CONFIG = """<?xml version="1.0"?>
<config version="11.0.0">
  <shared>
    <address>
      <entry name="a"><ip-netmask>10.0.0.0/24</ip-netmask></entry>
      <entry name="b"><ip-netmask>192.168.0.0/24</ip-netmask></entry>
    </address>
  </shared>
</config>
"""


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PSC_CONFIG": "/nonexistent/psc-test-config.yaml"}
    return subprocess.run(
        [sys.executable, "-m", "psc", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _cfg(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "cfg.xml"
    cfg.write_text(body)
    return cfg


def test_audit_overlaps_json_valid(tmp_path: Path) -> None:
    cp = run("-c", str(_cfg(tmp_path, _OVERLAP_CONFIG)), "-o", "json", "audit", "overlaps")
    assert cp.returncode == 0
    data = json.loads(cp.stdout)
    pairs = {(p["left_name"], p["right_name"], p["relationship"]) for p in data}
    # net-24 contains both hosts.
    assert ("net-24", "host-10", "contains") in pairs
    assert ("net-24", "dg-host", "contains") in pairs


def test_audit_overlaps_table_headers(tmp_path: Path) -> None:
    cp = run("-c", str(_cfg(tmp_path, _OVERLAP_CONFIG)), "-o", "table", "audit", "overlaps")
    assert cp.returncode == 0
    for col in ("left", "relationship", "right"):
        assert col in cp.stdout


def test_audit_overlaps_strict_exit_5_when_none(tmp_path: Path) -> None:
    cp = run(
        "-c", str(_cfg(tmp_path, _NO_OVERLAP_CONFIG)), "--strict", "-o", "json", "audit", "overlaps"
    )
    assert cp.returncode == 5
    assert json.loads(cp.stdout)["type"] == "not_found"


def test_audit_overlaps_scope_flag(tmp_path: Path) -> None:
    # Scoped to DG1: dg-host (DG1) pairs with the shared net; nothing else.
    cp = run(
        "-c", str(_cfg(tmp_path, _OVERLAP_CONFIG)), "-d", "DG1", "-o", "json", "audit", "overlaps"
    )
    assert cp.returncode == 0
    data = json.loads(cp.stdout)
    rights = {p["right_name"] for p in data}
    assert "dg-host" in rights
    assert "host-10" in rights  # shared host visible from DG1


def test_audit_no_args_shows_help() -> None:
    cp = run("audit")
    combined = cp.stdout + cp.stderr
    assert "Usage:" in combined
    assert "overlaps" in combined
