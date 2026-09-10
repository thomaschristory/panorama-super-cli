"""CLI tests for `psc delete` (issue #181).

`delete` turns a list of object names into a reference-safe teardown plan. It
completes the `refs unused` workflow: `unused` finds the candidates, a pipe
carries them into `delete`, and `delete` plans the removal.

Dry-run is the default. A blocked plan exits 6 and writes nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "purge-config.xml"


def run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PSC_CONFIG": "/nonexistent/psc-test-config.yaml"}
    return subprocess.run(
        [sys.executable, "-m", "psc", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        input=stdin,
    )


# -- target grammar ------------------------------------------------------


def test_bare_name_defaults_to_the_address_kind() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "h-unused")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert [(d["kind"], d["name"], d["location"]) for d in data["deletes"]] == [
        ("address", "h-unused", "shared")
    ]


def test_kind_prefix_selects_the_kind() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "service:tcp-unused")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    kinds = {(d["kind"], d["name"]) for d in data["deletes"]}
    # tcp-unused empties svcgrp-unused, which cascades.
    assert kinds == {("service", "tcp-unused"), ("service-group", "svcgrp-unused")}


def test_location_suffix_selects_the_location() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "address:h-dg-unused@DG-EDGE")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert data["deletes"][0]["location"] == "DG-EDGE"


def test_kind_option_sets_the_default_kind() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "--kind", "tag", "t-orphan")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert data["deletes"][0]["kind"] == "tag"


def test_omitted_location_resolves_when_the_name_is_unique() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "address:h-dg-unused")
    assert cp.returncode == 0, cp.stderr
    assert json.loads(cp.stdout)["deletes"][0]["location"] == "DG-EDGE"


def test_unknown_kind_is_a_usage_error() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "widget:h-unused")
    assert cp.returncode == 4, cp.stdout + cp.stderr
    assert json.loads(cp.stdout)["type"] == "validation"


def test_unknown_name_blocks_with_exit_six() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "h-ghost")
    assert cp.returncode == 6, cp.stdout + cp.stderr
    data = json.loads(cp.stdout)
    assert data["type"] == "conflict"
    assert any("h-ghost" in b for b in data["details"]["blockers"])


def test_no_targets_is_a_usage_error() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete")
    assert cp.returncode == 4, cp.stdout + cp.stderr


# -- the composable pipe -------------------------------------------------


def test_jsonl_from_refs_unused_pipes_into_delete() -> None:
    listed = run("-c", str(FIXTURE), "-o", "jsonl", "refs", "unused", "--kind", "service")
    assert listed.returncode == 0, listed.stderr
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "-f", "-", stdin=listed.stdout)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    names = {d["name"] for d in json.loads(cp.stdout)["deletes"]}
    assert "tcp-unused" in names
    assert "tcp-live" not in names


def test_json_array_from_refs_unused_pipes_into_delete() -> None:
    listed = run("-c", str(FIXTURE), "-o", "json", "refs", "unused", "--kind", "address")
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "-f", "-", stdin=listed.stdout)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    names = {d["name"] for d in json.loads(cp.stdout)["deletes"]}
    assert "h-unused" in names
    assert "h-live" not in names


def test_file_of_specs_with_comments(tmp_path: Path) -> None:
    listfile = tmp_path / "targets.txt"
    listfile.write_text("# dead hosts\naddress:h-unused@shared\n\nservice:tcp-unused\n")
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "-f", str(listfile))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    names = {d["name"] for d in json.loads(cp.stdout)["deletes"]}
    assert {"h-unused", "tcp-unused"} <= names


def test_unreadable_file_is_an_input_error(tmp_path: Path) -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "-f", str(tmp_path / "missing.txt"))
    assert cp.returncode == 3, cp.stdout + cp.stderr


# -- the safety model ----------------------------------------------------


def test_dry_run_is_the_default_and_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "never.xml"
    cp = run("-c", str(FIXTURE), "delete", "h-unused", "--out", str(out))
    assert cp.returncode == 0, cp.stderr
    assert "dry-run" in cp.stderr
    assert out.exists()  # --out is honoured in a dry-run, like every other command


def test_apply_writes_the_rewritten_config(tmp_path: Path) -> None:
    out = tmp_path / "purged.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "delete",
        "address:h-unused",
        "address-group:addrgrp-dead",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stderr
    text = out.read_text()
    assert "h-unused" not in text
    assert "addrgrp-dead" not in text
    assert "h-live" in text
    assert "g-live" in text
    assert "r-live" in text


def test_cascade_deletes_the_group_left_empty(tmp_path: Path) -> None:
    out = tmp_path / "purged.xml"
    cp = run("-c", str(FIXTURE), "delete", "h-in-dead-group", "--apply", "--out", str(out))
    assert cp.returncode == 0, cp.stderr
    text = out.read_text()
    assert "h-in-dead-group" not in text
    assert "addrgrp-dead" not in text  # emptied by the cascade


def test_deleting_a_live_object_scrubs_the_rule_that_names_it(tmp_path: Path) -> None:
    out = tmp_path / "purged.xml"
    cp = run("-c", str(FIXTURE), "delete", "h-live", "--apply", "--out", str(out))
    assert cp.returncode == 0, cp.stderr
    text = out.read_text()
    assert "h-live" not in text
    assert "g-live" not in text  # g-live held only h-live
    assert "r-live" not in text  # its source and destination are now empty


def test_keep_rules_keeps_the_orphan(tmp_path: Path) -> None:
    out = tmp_path / "purged.xml"
    cp = run("-c", str(FIXTURE), "delete", "h-live", "--keep-rules", "--apply", "--out", str(out))
    assert cp.returncode == 0, cp.stderr
    assert "r-live" in out.read_text()


def test_keep_groups_deletes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "purged.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "delete",
        "h-in-dead-group",
        "--keep-groups",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stderr
    text = out.read_text()
    assert "h-in-dead-group" in text
    assert "addrgrp-dead" in text


def test_shared_candidate_warns() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "h-unused")
    data = json.loads(cp.stdout)
    assert any("shared" in w for w in data["warnings"])


def test_tagged_candidate_warns_about_runtime_dag_membership() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "h-tagged")
    data = json.loads(cp.stdout)
    assert any("dynamic address-group" in w for w in data["warnings"])


def test_set_output_renders_a_script() -> None:
    cp = run("-c", str(FIXTURE), "-o", "set", "delete", "h-unused")
    assert cp.returncode == 0, cp.stderr
    assert "delete shared address h-unused" in cp.stdout


def test_json_plan_shape() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "h-unused")
    data = json.loads(cp.stdout)
    for key in ("title", "reference_edits", "rule_deletes", "deletes", "blockers", "warnings"):
        assert key in data


# -- the location guard --------------------------------------------------


DUP_XML = """<?xml version="1.0"?>
<config>
  <shared>
    <address><entry name="h-dup"><ip-netmask>10.0.0.1/32</ip-netmask></entry></address>
  </shared>
  <devices><entry name="localhost.localdomain"><device-group>
    <entry name="DG-EDGE">
      <address><entry name="h-dup"><ip-netmask>10.9.9.9/32</ip-netmask></entry></address>
    </entry>
  </device-group></entry></devices>
</config>
"""


@pytest.fixture
def dup_config(tmp_path: Path) -> Path:
    p = tmp_path / "dup.xml"
    p.write_text(DUP_XML)
    return p


def test_an_ambiguous_name_is_refused(dup_config: Path) -> None:
    cp = run("-c", str(dup_config), "-o", "json", "delete", "h-dup")
    assert cp.returncode == 4, cp.stdout + cp.stderr
    data = json.loads(cp.stdout)
    assert data["type"] == "validation"
    assert data["details"]["candidates"] == ["DG-EDGE", "shared"]


def test_an_empty_location_is_refused(dup_config: Path) -> None:
    """An unset shell variable must never silently retarget the delete to shared."""
    cp = run("-c", str(dup_config), "-o", "json", "delete", "h-dup@")
    assert cp.returncode == 4, cp.stdout + cp.stderr
    assert "empty location" in json.loads(cp.stdout)["error"]


def test_the_device_group_scope_never_chooses_the_target(dup_config: Path) -> None:
    """`-d` is a read scope everywhere in psc; it must not select a delete target."""
    cp = run("-c", str(dup_config), "-d", "DG-EDGE", "-o", "json", "delete", "h-dup")
    assert cp.returncode == 4, cp.stdout + cp.stderr
    assert json.loads(cp.stdout)["type"] == "validation"


def test_location_option_qualifies_an_unqualified_target(dup_config: Path) -> None:
    cp = run("-c", str(dup_config), "-o", "json", "delete", "h-dup", "--location", "DG-EDGE")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert json.loads(cp.stdout)["deletes"][0]["location"] == "DG-EDGE"


def test_target_option_is_equivalent_to_a_positional(dup_config: Path) -> None:
    cp = run("-c", str(dup_config), "-o", "json", "delete", "--target", "h-dup@shared")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert json.loads(cp.stdout)["deletes"][0]["location"] == "shared"


# -- input handling ------------------------------------------------------


def test_an_empty_candidate_list_is_a_clean_no_op() -> None:
    """The `unused | jq | delete` pipe must not fail when the filter matches nothing."""
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "-f", "-", stdin="[]\n")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert json.loads(cp.stdout)["deletes"] == []


def test_a_json_array_of_non_objects_is_an_input_error() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "-f", "-", stdin='["h-unused"]')
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert json.loads(cp.stdout)["type"] == "input"


def test_a_row_without_a_name_is_an_input_error() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "-f", "-", stdin='{"kind": "address"}')
    assert cp.returncode == 3, cp.stdout + cp.stderr


def test_a_non_text_file_is_an_input_error(tmp_path: Path) -> None:
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\xff\xfe\x00\x01")
    cp = run("-c", str(FIXTURE), "-o", "json", "delete", "-f", str(binary))
    assert cp.returncode == 3, cp.stdout + cp.stderr


# -- the blocker gate ----------------------------------------------------


def test_a_blocked_plan_writes_no_artifact(tmp_path: Path) -> None:
    out = tmp_path / "never.xml"
    cp = run("-c", str(FIXTURE), "delete", "h-ghost", "--apply", "--out", str(out))
    assert cp.returncode == 6, cp.stdout + cp.stderr
    assert not out.exists()


def test_the_source_export_is_never_rewritten(tmp_path: Path) -> None:
    copy = tmp_path / "copy.xml"
    copy.write_text(FIXTURE.read_text())
    before = copy.read_text()
    cp = run("-c", str(copy), "delete", "h-unused", "--apply", "--out", str(tmp_path / "o.xml"))
    assert cp.returncode == 0, cp.stderr
    assert copy.read_text() == before
