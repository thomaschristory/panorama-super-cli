from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
from pathlib import Path

import click
import pytest

from psc.cli import app

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


def test_find_ip_table_separates_multiple_targets() -> None:
    # Issue #43: multi-target table output must draw a rule between each
    # target's matches. The interior divider uses box-drawing '├'.
    cp = run("-c", str(FIXTURE), "-o", "table", "find", "ip", "10.0.0.10", "10.0.0.99")
    assert cp.returncode == 0
    assert "├" in cp.stdout


_HOST_AND_NET_CONFIG = """<?xml version="1.0"?>
<config version="11.0.0">
  <shared>
    <address>
      <entry name="host-with-mask"><ip-netmask>10.1.1.50/24</ip-netmask></entry>
      <entry name="real-network"><ip-netmask>10.1.1.0/24</ip-netmask></entry>
    </address>
  </shared>
</config>
"""


def test_dedup_addresses_strict_default_finds_no_duplicates(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.xml"
    cfg.write_text(_HOST_AND_NET_CONFIG)
    cp = run("-c", str(cfg), "-o", "json", "dedup", "addresses")
    assert cp.returncode == 0
    assert json.loads(cp.stdout) == []


def test_dedup_addresses_not_strict_groups_host_with_network(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.xml"
    cfg.write_text(_HOST_AND_NET_CONFIG)
    cp = run("-c", str(cfg), "-o", "json", "dedup", "addresses", "--not-strict")
    assert cp.returncode == 0
    groups = json.loads(cp.stdout)
    assert len(groups) == 1
    assert {m["name"] for m in groups[0]["members"]} == {"host-with-mask", "real-network"}


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


def test_merge_apply_out_set_writes_set_script(tmp_path: Path) -> None:
    out = tmp_path / "fixed.set"
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
        "-of",
        "set",
    )
    assert cp.returncode == 0
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "delete shared address web-primary" in text
    assert "<entry" not in text  # the --out artifact is a set script, not XML


def test_merge_apply_out_xml_is_default(tmp_path: Path) -> None:
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
        "--output-format",
        "xml",
    )
    assert cp.returncode == 0
    assert "web-primary" not in out.read_text(encoding="utf-8")  # rewritten XML, object gone


def test_no_source_errors_config() -> None:
    cp = run("-o", "json", "find", "ip", "10.0.0.10")
    assert cp.returncode == 9
    assert json.loads(cp.stdout)["type"] == "config"


def test_no_args_prints_help_without_traceback() -> None:
    # Typer's no_args_is_help raises a *vendored* click NoArgsIsHelpError that
    # the main() wrapper (standalone_mode=False) must swallow cleanly (#31).
    cp = run()
    combined = cp.stdout + cp.stderr
    assert cp.returncode == 0
    assert "Usage:" in combined
    assert "Traceback" not in combined
    assert "NoArgsIsHelpError" not in combined


def test_unknown_command_usage_error_exit_2() -> None:
    cp = run("no-such-command")
    combined = cp.stdout + cp.stderr
    assert cp.returncode == 2
    assert "Traceback" not in combined
    assert "No such command" in combined


def test_click_exception_module_resolves_with_required_attrs() -> None:
    # main() reads ClickException/Exit/Abort off the resolved module, so it must
    # expose them whichever Typer flavour is installed.
    mod = app._click_exception_module()
    for attr in ("ClickException", "Exit", "Abort"):
        assert hasattr(mod, attr)


def test_click_exception_module_falls_back_when_typer_unvendored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Older Typer (<0.16) has no `typer._click`; a top-level import of it would
    # crash psc at import time — worse than #31. Resolution must degrade to the
    # real Click instead of raising.
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("typer._click"):
            raise ImportError("simulated Typer without vendored Click")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert app._click_exception_module() is click.exceptions
