from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
from pathlib import Path

import click
import pytest
from typer.testing import CliRunner

from psc.cli import app, find_cmds
from psc.cli.app import app as cli_app

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


def test_unused_prints_scope_caveat_to_stderr() -> None:
    # `unused` is a candidate list, not a kill list — the scan-scope blind spot
    # must be surfaced at point of use, on stderr so machine rows stay clean.
    cp = run("-c", str(FIXTURE), "-o", "json", "refs", "unused", "--kind", "address")
    assert cp.returncode == 0
    json.loads(cp.stdout)  # stdout stays pure machine output
    assert "caveat" not in cp.stdout.lower()
    low = cp.stderr.lower()
    assert "not scanned" in low or "candidate" in low
    assert "template" in low  # names the most dangerous blind spot


def test_unused_no_caveat_suppresses_stderr_caveat() -> None:
    # --no-caveat opts out of the blind-spot notice (e.g. for known-clean scripts).
    cp = run("-c", str(FIXTURE), "-o", "json", "refs", "unused", "--kind", "address", "--no-caveat")
    assert cp.returncode == 0
    json.loads(cp.stdout)
    assert "caveat" not in cp.stderr.lower()


def test_unused_ignore_disabled_surfaces_disabled_only_object(tmp_path: Path) -> None:
    # An address referenced only by a DISABLED rule is used by default but
    # surfaces under `refs unused --ignore-disabled` (#9).
    cfg = tmp_path / "disabled-only.xml"
    cfg.write_text(
        """<config><shared>
          <address><entry name="h-off"><ip-netmask>10.9.9.9/32</ip-netmask></entry></address>
          <pre-rulebase><security><rules><entry name="r">
            <source><member>any</member></source>
            <destination><member>h-off</member></destination>
            <disabled>yes</disabled></entry>
          </rules></security></pre-rulebase>
        </shared></config>"""
    )
    default = run("-c", str(cfg), "-o", "json", "refs", "unused", "--kind", "address")
    assert default.returncode == 0
    assert "h-off" not in {row["name"] for row in json.loads(default.stdout)}

    flagged = run(
        "-c", str(cfg), "-o", "json", "refs", "unused", "--kind", "address", "--ignore-disabled"
    )
    assert flagged.returncode == 0
    assert "h-off" in {row["name"] for row in json.loads(flagged.stdout)}


def test_unparseable_dag_filter_warns_on_stderr(tmp_path: Path) -> None:
    # An unparseable DAG filter must not crash the audit; psc warns on stderr
    # (naming the DAG) that its membership is unverified, and stdout stays clean.
    cfg = tmp_path / "bad-dag.xml"
    cfg.write_text(
        """<config><shared>
          <address><entry name="h"><ip-netmask>10.0.0.1/32</ip-netmask>
            <tag><member>prod</member></tag></entry></address>
          <address-group><entry name="dag-bad">
            <dynamic><filter>'prod' and</filter></dynamic></entry></address-group>
          <pre-rulebase><security><rules><entry name="r">
            <source><member>any</member></source>
            <destination><member>dag-bad</member></destination></entry>
          </rules></security></pre-rulebase>
        </shared></config>"""
    )
    cp = run("-c", str(cfg), "-o", "json", "refs", "unused", "--kind", "address")
    assert cp.returncode == 0
    json.loads(cp.stdout)  # stdout stays pure machine output
    assert "dag-bad" in cp.stderr


def test_find_ip_json_contract() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "find", "ip", "10.0.0.10")
    assert cp.returncode == 0
    data = json.loads(cp.stdout)
    assert data["exists"] is True
    assert {m["name"] for m in data["matches"]} >= {"h-web1", "web-primary"}


def test_find_ip_json_carries_tags() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "find", "ip", "10.0.0.10")
    assert cp.returncode == 0
    data = json.loads(cp.stdout)
    tags_by_name = {m["name"]: m["tags"] for m in data["matches"]}
    assert tags_by_name["h-web1"] == ["t-prod"]


def test_find_ip_table_has_tags_column() -> None:
    cp = run("-c", str(FIXTURE), "-o", "table", "find", "ip", "10.0.0.10")
    assert cp.returncode == 0
    assert "tags" in cp.stdout
    assert "t-prod" in cp.stdout


def test_find_object_json_carries_tags() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "find", "object", "grp-web")
    assert cp.returncode == 0
    assert json.loads(cp.stdout)[0]["tags"] == ["t-prod"]


def test_find_object_tag_kind_reports_empty_tags() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "find", "object", "t-prod")
    assert cp.returncode == 0
    hit = json.loads(cp.stdout)[0]
    assert hit["kind"] == "tag"
    assert hit["tags"] == []


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


def test_find_ip_resolve_fqdn_flag_wires_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    # --resolve-fqdn must construct the default resolver and pass it through so
    # an FQDN object resolving to the queried IP is surfaced. We stub the
    # default resolver factory to stay off the network and deterministic.
    monkeypatch.setattr(find_cmds, "default_resolver", lambda: lambda fqdn: {"93.184.216.34"})
    runner = CliRunner()
    res = runner.invoke(
        cli_app,
        ["-c", str(FIXTURE), "-o", "json", "find", "ip", "--resolve-fqdn", "93.184.216.34"],
    )
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert "fqdn-example" in {m["name"] for m in data["matches"]}


def test_find_ip_no_resolve_fqdn_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without the flag the resolver factory must never be called (offline safe).
    def _boom() -> object:
        raise AssertionError("resolver constructed without --resolve-fqdn")

    monkeypatch.setattr(find_cmds, "default_resolver", _boom)
    runner = CliRunner()
    res = runner.invoke(cli_app, ["-c", str(FIXTURE), "-o", "json", "find", "ip", "93.184.216.34"])
    assert res.exit_code == 0
    assert "fqdn-example" not in {m["name"] for m in json.loads(res.stdout)["matches"]}


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


_TWO_DUP_GROUPS_CONFIG = """<?xml version="1.0"?>
<config version="11.0.0">
  <shared>
    <address>
      <entry name="a1"><ip-netmask>10.0.0.1</ip-netmask></entry>
      <entry name="a2"><ip-netmask>10.0.0.1</ip-netmask></entry>
      <entry name="b1"><ip-netmask>10.0.0.2</ip-netmask></entry>
      <entry name="b2"><ip-netmask>10.0.0.2</ip-netmask></entry>
    </address>
  </shared>
</config>
"""


def test_dedup_addresses_table_separates_each_group(tmp_path: Path) -> None:
    # Issue #72: dedup table output must draw a rule between each group of
    # duplicates so the blocks are easy to scan. Interior divider uses '├'.
    cfg = tmp_path / "cfg.xml"
    cfg.write_text(_TWO_DUP_GROUPS_CONFIG)
    cp = run("-c", str(cfg), "-o", "table", "dedup", "addresses")
    assert cp.returncode == 0
    assert "├" in cp.stdout


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


def test_merge_out_set_dry_run_writes_file(tmp_path: Path) -> None:
    """#47: `--out` is an artifact request, honored even without `--apply`.

    Writing a user-named file never touches the source export, so a dry-run
    must still produce it — the silent no-op was the bug.
    """
    out = tmp_path / "plan.set"
    cp = run(
        "-c",
        str(FIXTURE),
        "dedup",
        "merge",
        "--keep",
        "h-web1",
        "--remove",
        "web-primary",
        "-of",
        "set",
        "--out",
        str(out),
    )
    assert cp.returncode == 0
    assert out.exists(), "--out must write a file even in dry-run"
    text = out.read_text(encoding="utf-8")
    assert "delete shared address web-primary" in text
    assert "<entry" not in text  # set script, not XML
    # The source export is never touched by a dry-run artifact write.
    assert "web-primary" in FIXTURE.read_text(encoding="utf-8")


def test_merge_out_xml_dry_run_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "rewritten.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "dedup",
        "merge",
        "--keep",
        "h-web1",
        "--remove",
        "web-primary",
        "--out",
        str(out),
    )
    assert cp.returncode == 0
    assert out.exists()
    assert "web-primary" not in out.read_text(encoding="utf-8")  # rewritten XML, object gone


def test_merge_no_out_no_apply_still_dry_run(tmp_path: Path) -> None:
    """Without `--out` and without `--apply`, nothing is written anywhere."""
    out = tmp_path / "x.set"
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
    assert "re-run with --apply" in cp.stderr
    assert not out.exists()


def test_merge_group_collapses_whole_bucket_dry_run() -> None:
    # FIXTURE bucket {h-web1, web-primary, h-web1-slash} all == 10.0.0.10/32.
    # Collapsing toward h-web1 drops the other two in one plan.
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "set",
        "dedup",
        "merge",
        "--group",
        "10.0.0.10/32",
        "--keep",
        "h-web1",
    )
    assert cp.returncode == 0
    assert "delete shared address web-primary" in cp.stdout
    assert "delete shared address h-web1-slash" in cp.stdout


def test_merge_group_default_keep_is_first_member() -> None:
    # No --keep: deterministic default survivor is the sorted-first bucket member
    # (h-web1); the other two are dropped.
    cp = run("-c", str(FIXTURE), "-o", "set", "dedup", "merge", "--group", "10.0.0.10/32")
    assert cp.returncode == 0
    assert "delete shared address web-primary" in cp.stdout
    assert "delete shared address h-web1-slash" in cp.stdout
    assert "delete shared address h-web1\n" not in cp.stdout + "\n"


def test_merge_group_selects_survivor_via_keep() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "json",
        "dedup",
        "merge",
        "--group",
        "10.0.0.10/32",
        "--keep",
        "web-primary",
    )
    assert cp.returncode == 0
    dropped = {d["name"] for d in json.loads(cp.stdout)["deletes"]}
    assert dropped == {"h-web1", "h-web1-slash"}


def test_merge_group_invalid_keep_exit_3() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "json",
        "dedup",
        "merge",
        "--group",
        "10.0.0.10/32",
        "--keep",
        "net-10",  # not in the bucket
    )
    assert cp.returncode == 3
    assert json.loads(cp.stdout)["type"] == "input"


def test_merge_group_unknown_value_exit_3() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "dedup", "merge", "--group", "203.0.113.9/32")
    assert cp.returncode == 3
    assert json.loads(cp.stdout)["type"] == "input"


def test_merge_group_apply_roundtrips_out(tmp_path: Path) -> None:
    out = tmp_path / "fixed.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "dedup",
        "merge",
        "--group",
        "10.0.0.10/32",
        "--keep",
        "h-web1",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0
    text = out.read_text(encoding="utf-8")
    assert "web-primary" not in text
    assert "h-web1-slash" not in text
    assert "h-web1" in text  # survivor stays


DEDUP_GROUPS_FIXTURE = Path(__file__).parent / "fixtures" / "dedup-groups.xml"


def test_dedup_groups_json_contract() -> None:
    cp = run("-c", str(DEDUP_GROUPS_FIXTURE), "-o", "json", "dedup", "groups")
    assert cp.returncode == 0
    groups = json.loads(cp.stdout)
    names = {m["name"] for g in groups for m in g["members"]}
    assert names == {"grp-a", "grp-b"}  # equivalent via the nested group


def test_dedup_groups_strict_exit_5_when_none(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.xml"
    cfg.write_text(_HOST_AND_NET_CONFIG)  # no address-groups at all
    cp = run("-c", str(cfg), "--strict", "-o", "json", "dedup", "groups")
    assert cp.returncode == 5
    assert json.loads(cp.stdout)["type"] == "not_found"


def test_dedup_merge_group_dry_run_set_contains_delete() -> None:
    cp = run(
        "-c",
        str(DEDUP_GROUPS_FIXTURE),
        "-o",
        "set",
        "dedup",
        "merge-group",
        "--keep",
        "grp-a",
        "--remove",
        "grp-b",
    )
    assert cp.returncode == 0
    assert "delete shared address-group grp-b" in cp.stdout


def test_dedup_merge_group_blocked_exit_6_for_non_equivalent() -> None:
    cp = run(
        "-c",
        str(DEDUP_GROUPS_FIXTURE),
        "-o",
        "json",
        "dedup",
        "merge-group",
        "--keep",
        "grp-a",
        "--remove",
        "grp-c",
    )
    assert cp.returncode == 6
    assert json.loads(cp.stdout)["type"] == "conflict"


def test_dedup_groups_location_shared_finds_shared_buckets() -> None:
    # `--location shared` must target the shared scope, not a device-group
    # literally named "shared"; the fixture's equivalent pair lives in shared.
    cp = run(
        "-c", str(DEDUP_GROUPS_FIXTURE), "-o", "json", "dedup", "groups", "--location", "shared"
    )
    assert cp.returncode == 0
    names = {m["name"] for g in json.loads(cp.stdout) for m in g["members"]}
    assert names == {"grp-a", "grp-b"}


def test_dedup_groups_no_local_d_alias() -> None:
    # `dedup groups` must NOT declare its own `-d` (it would collide with the
    # global `-d`/--device-group). After removal, `-d` is unknown *as a local
    # option* on the subcommand.
    cp = run("-c", str(DEDUP_GROUPS_FIXTURE), "-o", "json", "dedup", "groups", "-d", "shared")
    assert cp.returncode == 2
    assert "No such option: -d" in cp.stderr


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
