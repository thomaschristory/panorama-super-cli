from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_CONFIG_A = """<?xml version="1.0"?>
<config version="11.0.0">
  <shared>
    <address>
      <entry name="web"><ip-netmask>10.0.0.1</ip-netmask></entry>
      <entry name="gone"><ip-netmask>10.0.0.9</ip-netmask></entry>
    </address>
  </shared>
</config>
"""

_CONFIG_B = """<?xml version="1.0"?>
<config version="11.0.0">
  <shared>
    <address>
      <entry name="web"><ip-netmask>10.0.0.99</ip-netmask></entry>
      <entry name="fresh"><ip-netmask>10.0.0.5</ip-netmask></entry>
    </address>
  </shared>
</config>
"""

_DG_CONFIG = """<?xml version="1.0"?>
<config version="11.0.0">
  <shared>
    <address>
      <entry name="shared-host"><ip-netmask>10.0.0.1</ip-netmask></entry>
    </address>
  </shared>
  <devices>
    <entry name="localhost.localdomain">
      <device-group>
        <entry name="A">
          <address>
            <entry name="only-a"><ip-netmask>10.1.0.1</ip-netmask></entry>
          </address>
        </entry>
        <entry name="B">
          <address>
            <entry name="only-b"><ip-netmask>10.2.0.1</ip-netmask></entry>
          </address>
        </entry>
      </device-group>
    </entry>
  </devices>
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


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


def test_diff_files_json_shape(tmp_path: Path) -> None:
    a = _write(tmp_path, "a.xml", _CONFIG_A)
    b = _write(tmp_path, "b.xml", _CONFIG_B)
    cp = run("-o", "json", "diff", str(a), str(b))
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert [x["name"] for x in data["addresses"]["added"]] == ["fresh"]
    assert [x["name"] for x in data["addresses"]["removed"]] == ["gone"]
    assert [x["name"] for x in data["addresses"]["changed"]] == ["web"]
    ch = data["addresses"]["changed"][0]
    assert ch["before"]["value"] == "10.0.0.1"
    assert ch["after"]["value"] == "10.0.0.99"


def test_diff_files_table(tmp_path: Path) -> None:
    a = _write(tmp_path, "a.xml", _CONFIG_A)
    b = _write(tmp_path, "b.xml", _CONFIG_B)
    cp = run("-o", "table", "diff", str(a), str(b))
    assert cp.returncode == 0, cp.stderr
    out = cp.stdout
    assert "added" in out
    assert "removed" in out
    assert "changed" in out
    assert "fresh" in out
    assert "gone" in out
    assert "web" in out


def test_diff_identical_files_exit_zero(tmp_path: Path) -> None:
    a = _write(tmp_path, "a.xml", _CONFIG_A)
    b = _write(tmp_path, "b.xml", _CONFIG_A)
    cp = run("-o", "json", "diff", str(a), str(b))
    assert cp.returncode == 0
    data = json.loads(cp.stdout)
    assert data["addresses"]["added"] == []
    assert data["addresses"]["removed"] == []
    assert data["addresses"]["changed"] == []


def test_diff_device_groups(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "cfg.xml", _DG_CONFIG)
    cp = run("-c", str(cfg), "-o", "json", "diff", "--device-group", "A", "--against", "B")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert [x["name"] for x in data["addresses"]["added"]] == ["only-b"]
    assert [x["name"] for x in data["addresses"]["removed"]] == ["only-a"]
    # shared-host visible to both -> neither
    names = {x["name"] for x in data["addresses"]["added"]}
    names |= {x["name"] for x in data["addresses"]["removed"]}
    assert "shared-host" not in names


def test_diff_no_args_is_error() -> None:
    cp = run("-o", "json", "diff")
    assert cp.returncode == 4
    assert json.loads(cp.stdout)["type"] == "validation"


def test_diff_one_file_only_is_error(tmp_path: Path) -> None:
    a = _write(tmp_path, "a.xml", _CONFIG_A)
    cp = run("-o", "json", "diff", str(a))
    assert cp.returncode == 4
    assert json.loads(cp.stdout)["type"] == "validation"


def test_diff_mixing_files_and_dg_flags_is_error(tmp_path: Path) -> None:
    a = _write(tmp_path, "a.xml", _CONFIG_A)
    b = _write(tmp_path, "b.xml", _CONFIG_B)
    cp = run("-o", "json", "diff", str(a), str(b), "--device-group", "A", "--against", "B")
    assert cp.returncode == 4
    assert json.loads(cp.stdout)["type"] == "validation"


def test_diff_dg_against_without_device_group_is_error(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "cfg.xml", _DG_CONFIG)
    cp = run("-c", str(cfg), "-o", "json", "diff", "--against", "B")
    assert cp.returncode == 4
    assert json.loads(cp.stdout)["type"] == "validation"


def test_diff_missing_file_is_input_error(tmp_path: Path) -> None:
    a = _write(tmp_path, "a.xml", _CONFIG_A)
    missing = tmp_path / "nope.xml"
    cp = run("-o", "json", "diff", str(a), str(missing))
    assert cp.returncode == 3
    assert json.loads(cp.stdout)["type"] == "input"


def test_diff_help() -> None:
    cp = run("diff", "--help")
    combined = cp.stdout + cp.stderr
    assert "diff" in combined.lower()
