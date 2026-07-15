"""`psc dedup promote` — CLI surface for the promote engine (issue #154).

Follows `test_cli_move.py`'s subprocess-driven idiom: `psc` is a real installed
console entry point, and every mutating command's safety contract (dry-run
default, blocked-plan exit 6, offline `--apply --out` round-trip) is exercised
end-to-end rather than through internal Typer/Click test runners.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from defusedxml.ElementTree import fromstring as xml_fromstring

# An empty `<shared>` element is required: `apply_xml` looks up the destination
# scope by name and raises if it isn't present in the source tree at all, even
# though promoting into it only ever *adds* an entry.
_XML = """<config><shared></shared><devices><entry name="localhost.localdomain"><device-group>
  <entry name="DG-A">
    <address><entry name="web"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
  </entry>
  <entry name="DG-B">
    <address><entry name="web"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
  </entry>
</device-group></entry></devices></config>"""

# Same value across DG-A and DG-B would make this one bucket in the usual case;
# here the two copies carry different *names* for the same value instead, which
# is what actually trips promote's "bucket names diverge" blocker. Using a
# genuine value mismatch would fail earlier — `find_duplicate_addresses` groups
# by value, so two different values are never bucketed together in the first
# place, and `--group` would 404 before `plan_promote` ever runs.
_DIVERGENT_NAMES_XML = """<config><shared></shared><devices><entry \
name="localhost.localdomain"><device-group>
  <entry name="DG-A">
    <address><entry name="web1"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
  </entry>
  <entry name="DG-B">
    <address><entry name="web2"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
  </entry>
</device-group></entry></devices></config>"""


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PSC_CONFIG": "/nonexistent/psc-test-config.yaml"}
    return subprocess.run(
        [sys.executable, "-m", "psc", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _cfg(tmp_path: Path, xml: str = _XML) -> Path:
    p = tmp_path / "panorama.xml"
    p.write_text(xml)
    return p


def test_dry_run_prints_the_plan_and_writes_nothing(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    before = cfg.read_text()
    cp = run("-c", str(cfg), "dedup", "promote", "address", "--group", "10.0.0.1/32")
    assert cp.returncode == 0, cp.stderr
    assert "shared" in cp.stdout
    assert cfg.read_text() == before  # source export untouched


def test_apply_out_round_trips_offline(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    out = tmp_path / "out.xml"
    cp = run(
        "-c",
        str(cfg),
        "dedup",
        "promote",
        "address",
        "--group",
        "10.0.0.1/32",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stderr
    root = xml_fromstring(out.read_text())

    shared_names = {e.get("name") for e in root.findall("./shared/address/entry")}
    assert shared_names == {"web"}  # promoted to shared

    for dg in ("DG-A", "DG-B"):
        entry = next(
            e for e in root.findall("./devices/entry/device-group/entry") if e.get("name") == dg
        )
        assert entry.findall("./address/entry") == []  # both DG copies gone


def test_blocked_plan_exits_6_and_writes_nothing(tmp_path: Path) -> None:
    # DG-A/DG-B carry the same value under different names ("web1"/"web2") -> one
    # bucket by value, but promote refuses to pick a survivor name for you.
    cfg = _cfg(tmp_path, _DIVERGENT_NAMES_XML)
    out = tmp_path / "out.xml"
    cp = run(
        "-c",
        str(cfg),
        "dedup",
        "promote",
        "address",
        "--group",
        "10.0.0.1/32",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 6, cp.stdout + cp.stderr
    assert not out.exists()


def test_set_output_renders_create_and_delete_lines(tmp_path: Path) -> None:
    cp = run(
        "-c",
        str(_cfg(tmp_path)),
        "-o",
        "set",
        "dedup",
        "promote",
        "address",
        "--group",
        "10.0.0.1/32",
    )
    assert cp.returncode == 0, cp.stderr
    assert "set shared address web ip-netmask 10.0.0.1/32" in cp.stdout
    assert "delete device-group DG-A address web" in cp.stdout
    assert "delete device-group DG-B address web" in cp.stdout


def test_all_promotes_every_bucket(tmp_path: Path) -> None:
    cp = run("-c", str(_cfg(tmp_path)), "dedup", "promote", "address", "--all")
    assert cp.returncode == 0, cp.stderr
    assert "shared" in cp.stdout


def test_group_and_all_together_is_a_usage_error(tmp_path: Path) -> None:
    cp = run(
        "-c",
        str(_cfg(tmp_path)),
        "dedup",
        "promote",
        "address",
        "--group",
        "10.0.0.1/32",
        "--all",
    )
    assert cp.returncode != 0


def test_neither_group_nor_all_is_a_usage_error(tmp_path: Path) -> None:
    cp = run("-c", str(_cfg(tmp_path)), "dedup", "promote", "address")
    assert cp.returncode != 0
