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


def test_set_address_set_format() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "set",
        "set",
        "address",
        "--name",
        "new-h",
        "--type",
        "ip-netmask",
        "--value",
        "2.2.2.2",
    )
    assert cp.returncode == 0, cp.stderr
    assert "set shared address new-h ip-netmask 2.2.2.2" in cp.stdout


def test_set_address_json_changeset_shape() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "json",
        "set",
        "address",
        "--name",
        "new-h",
        "--type",
        "ip-netmask",
        "--value",
        "2.2.2.2",
    )
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert data["upserts"][0]["kind"] == "address"
    assert data["upserts"][0]["name"] == "new-h"


def test_set_address_apply_out_writes_xml(tmp_path: Path) -> None:
    out = tmp_path / "out.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "set",
        "address",
        "--name",
        "new-h",
        "--type",
        "ip-netmask",
        "--value",
        "2.2.2.2",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stderr
    text = out.read_text()
    assert "new-h" in text
    assert "2.2.2.2" in text


def test_set_address_invalid_name_exit4() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "json",
        "set",
        "address",
        "--name",
        "-bad",
        "--type",
        "ip-netmask",
        "--value",
        "2.2.2.2",
    )
    assert cp.returncode == 4
    assert json.loads(cp.stdout)["type"] == "validation"


def test_set_address_collision_exit6() -> None:
    # "grp-web" is an existing address-group in shared.
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "json",
        "set",
        "address",
        "--name",
        "grp-web",
        "--type",
        "ip-netmask",
        "--value",
        "2.2.2.2",
    )
    assert cp.returncode == 6
    assert json.loads(cp.stdout)["type"] == "conflict"


def test_set_address_location_prefix() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "set",
        "--location",
        "DG-EDGE",
        "set",
        "address",
        "--name",
        "new-h",
        "--type",
        "ip-netmask",
        "--value",
        "2.2.2.2",
    )
    # --location is a per-subcommand option; pass it after the subcommand instead
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "set",
        "set",
        "address",
        "--name",
        "new-h",
        "--type",
        "ip-netmask",
        "--value",
        "2.2.2.2",
        "--location",
        "DG-EDGE",
    )
    assert cp.returncode == 0, cp.stderr
    assert "set device-group DG-EDGE address new-h ip-netmask 2.2.2.2" in cp.stdout


def test_set_address_group_static() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "set",
        "set",
        "address-group",
        "--name",
        "ag1",
        "--member",
        "h-web1",
        "--member",
        "net-10",
    )
    assert cp.returncode == 0, cp.stderr
    assert "set shared address-group ag1 static [ h-web1 net-10 ]" in cp.stdout


def test_set_address_group_dynamic() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "set",
        "set",
        "address-group",
        "--name",
        "ag1",
        "--filter",
        "'t-prod'",
    )
    assert cp.returncode == 0, cp.stderr
    # The free-text filter is rendered as one quoted token so the `set` script
    # matches what --apply stores as element text (dry-run/apply equivalence).
    assert """set shared address-group ag1 dynamic filter "'t-prod'\"""" in cp.stdout


def test_set_service_tcp_apply(tmp_path: Path) -> None:
    out = tmp_path / "out.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "set",
        "service",
        "--name",
        "svc-x",
        "--protocol",
        "tcp",
        "--dest-port",
        "8080",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stderr
    text = out.read_text()
    assert "svc-x" in text
    assert "8080" in text


def test_set_service_group() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "set",
        "set",
        "service-group",
        "--name",
        "sg1",
        "--member",
        "tcp-443",
    )
    assert cp.returncode == 0, cp.stderr
    assert "set shared service-group sg1 members [ tcp-443 ]" in cp.stdout


def test_set_tag_apply(tmp_path: Path) -> None:
    out = tmp_path / "out.xml"
    cp = run(
        "-c",
        str(FIXTURE),
        "set",
        "tag",
        "--name",
        "t-new",
        "--color",
        "color5",
        "--comments",
        "hello",
        "--apply",
        "--out",
        str(out),
    )
    assert cp.returncode == 0, cp.stderr
    text = out.read_text()
    assert "t-new" in text
    assert "color5" in text


def test_set_tag_bad_color_exit4() -> None:
    cp = run(
        "-c",
        str(FIXTURE),
        "-o",
        "json",
        "set",
        "tag",
        "--name",
        "t-new",
        "--color",
        "red",
    )
    assert cp.returncode == 4
    assert json.loads(cp.stdout)["type"] == "validation"


def test_set_no_source_exit9() -> None:
    cp = run(
        "-o",
        "json",
        "set",
        "address",
        "--name",
        "new-h",
        "--type",
        "ip-netmask",
        "--value",
        "2.2.2.2",
        "--apply",
        "--out",
        "/tmp/psc-x.xml",
    )
    assert cp.returncode == 9
    assert json.loads(cp.stdout)["type"] == "config"


def test_set_address_no_flags_is_validation_error() -> None:
    # The `-f/--file` bulk-import option relaxed the required flags to None; a
    # bare `set address` (no -f, no --name) must still be a validation error.
    cp = run("-c", str(FIXTURE), "-o", "json", "set", "address")
    assert cp.returncode == 4
    assert json.loads(cp.stdout)["type"] == "validation"


def test_set_address_missing_value_is_validation_error() -> None:
    cp = run(
        "-c", str(FIXTURE), "-o", "json", "set", "address", "--name", "x", "--type", "ip-netmask"
    )
    assert cp.returncode == 4


def test_set_service_missing_dest_port_is_validation_error() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "set", "service", "--name", "x", "--protocol", "tcp")
    assert cp.returncode == 4


def test_set_address_group_no_member_or_filter_is_validation_error() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "set", "address-group", "--name", "x")
    assert cp.returncode == 4


def test_set_service_group_no_member_is_validation_error() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "set", "service-group", "--name", "x")
    assert cp.returncode == 4


def test_set_tag_no_name_is_validation_error() -> None:
    cp = run("-c", str(FIXTURE), "-o", "json", "set", "tag")
    assert cp.returncode == 4
