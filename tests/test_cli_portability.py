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


def test_export_addresses_is_ndjson(tmp_path: Path) -> None:
    cp = run("-c", str(FIXTURE), "export", "addresses")
    assert cp.returncode == 0, cp.stderr
    lines = [line for line in cp.stdout.splitlines() if line.strip()]
    assert lines
    for line in lines:
        obj = json.loads(line)  # each line valid JSON on its own
        assert "name" in obj


def test_export_to_out_file(tmp_path: Path) -> None:
    out = tmp_path / "addrs.ndjson"
    cp = run("-c", str(FIXTURE), "export", "addresses", "--out", str(out))
    assert cp.returncode == 0, cp.stderr
    lines = [line for line in out.read_text().splitlines() if line.strip()]
    assert lines
    assert all(json.loads(line)["name"] for line in lines)


def test_import_dry_run_plan(tmp_path: Path) -> None:
    f = tmp_path / "objs.ndjson"
    f.write_text(
        '{"name": "imp-a", "location": "shared", "type": "ip-netmask", "value": "5.5.5.5"}\n'
        '{"name": "imp-b", "location": "shared", "type": "ip-netmask", "value": "6.6.6.6"}\n'
    )
    cp = run("-c", str(FIXTURE), "-o", "json", "set", "address", "-f", str(f))
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    names = {u["name"] for u in data["upserts"]}
    assert names == {"imp-a", "imp-b"}


def test_import_apply_roundtrip(tmp_path: Path) -> None:
    f = tmp_path / "objs.ndjson"
    f.write_text(
        '{"name": "imp-a", "location": "shared", "type": "ip-netmask", "value": "5.5.5.5"}\n'
        '{"name": "imp-b", "location": "shared", "type": "ip-netmask", "value": "6.6.6.6"}\n'
    )
    out = tmp_path / "out.xml"
    cp = run("-c", str(FIXTURE), "set", "address", "-f", str(f), "--apply", "--out", str(out))
    assert cp.returncode == 0, cp.stderr
    text = out.read_text()
    assert "imp-a" in text
    assert "5.5.5.5" in text
    assert "imp-b" in text


def test_import_malformed_line_is_input_error(tmp_path: Path) -> None:
    f = tmp_path / "bad.ndjson"
    f.write_text(
        '{"name": "ok", "location": "shared", "type": "ip-netmask", "value": "1.1.1.1"}\n'
        "{ not json\n"
    )
    cp = run("-c", str(FIXTURE), "-o", "json", "set", "address", "-f", str(f))
    assert cp.returncode == 3
    assert json.loads(cp.stdout)["type"] == "input"


def test_import_missing_file_is_input_error() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "set", "address", "-f", "/nonexistent/objs.ndjson")
    assert cp.returncode == 3
    assert json.loads(cp.stdout)["type"] == "input"


def test_import_collision_blocks_batch(tmp_path: Path) -> None:
    # grp-web is an existing address-group; importing it as an address collides.
    f = tmp_path / "objs.ndjson"
    f.write_text(
        '{"name": "fine", "location": "shared", "type": "ip-netmask", "value": "1.1.1.1"}\n'
        '{"name": "grp-web", "location": "shared", "type": "ip-netmask", "value": "2.2.2.2"}\n'
    )
    cp = run("-c", str(FIXTURE), "-o", "json", "set", "address", "-f", str(f))
    assert cp.returncode == 6
    assert json.loads(cp.stdout)["type"] == "conflict"
