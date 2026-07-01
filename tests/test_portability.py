"""Engine tests for NDJSON bulk export/import (`psc.core.portability`).

These are framework-free: they exercise the (de)serialization + `plan_import`
composition directly against a parsed `Snapshot`, mirroring the safety contract
of a single `set` (crud validation, cross-kind collision blockers, in-place
type/mode-change blockers) aggregated over many lines.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from psc.core import portability
from psc.core.changeset import ObjectKind
from psc.core.models import Snapshot
from psc.core.parse import parse_config_file
from psc.output.errors import ErrorType, PscError

FIXTURE = Path(__file__).parent / "fixtures" / "panorama-config.xml"


@pytest.fixture
def snapshot() -> Snapshot:
    return parse_config_file(FIXTURE)


# --- export ----------------------------------------------------------------


def test_export_addresses_one_json_object_per_line(snapshot: Snapshot) -> None:
    lines = portability.export_ndjson(snapshot, ObjectKind.ADDRESS, scope=None)
    assert lines, "fixture has addresses"
    for line in lines:
        obj = json.loads(line)  # each line is valid standalone JSON
        assert "name" in obj
        assert "location" in obj


def test_export_is_deterministic_by_location_then_name(snapshot: Snapshot) -> None:
    lines = portability.export_ndjson(snapshot, ObjectKind.ADDRESS, scope=None)
    keys = [(json.loads(line)["location"], json.loads(line)["name"]) for line in lines]
    assert keys == sorted(keys)


def test_export_kinds_all_supported(snapshot: Snapshot) -> None:
    for kind in ObjectKind:
        # Every set/object kind must be exportable without error.
        portability.export_ndjson(snapshot, kind, scope=None)


# --- round-trip ------------------------------------------------------------


def test_roundtrip_addresses_export_then_import(snapshot: Snapshot) -> None:
    lines = portability.export_ndjson(snapshot, ObjectKind.ADDRESS, scope=None)
    cs = portability.plan_import(snapshot, lines, ObjectKind.ADDRESS)
    assert not cs.blockers
    # Every exported address round-trips into an upsert of the same object.
    assert len(cs.upserts) == len(lines)
    exported_names = {json.loads(line)["name"] for line in lines}
    assert {u.name for u in cs.upserts} == exported_names
    # All existing => updates, not creates.
    assert all(u.exists for u in cs.upserts)


def test_roundtrip_preserves_address_value(snapshot: Snapshot) -> None:
    lines = portability.export_ndjson(snapshot, ObjectKind.ADDRESS, scope=None)
    cs = portability.plan_import(snapshot, lines, ObjectKind.ADDRESS)
    by_name = {u.name: u for u in cs.upserts}
    a = snapshot.address_index()[("shared", "h-web1")]
    up = by_name["h-web1"]
    assert up.fields[a.type.value] == a.value


# --- per-line validation ---------------------------------------------------


def test_malformed_json_line_is_input_error_with_line_number(snapshot: Snapshot) -> None:
    lines = [
        '{"name": "ok1", "location": "shared", "type": "ip-netmask", "value": "1.1.1.1"}',
        "{not json",
    ]
    with pytest.raises(PscError) as exc:
        portability.plan_import(snapshot, lines, ObjectKind.ADDRESS)
    assert exc.value.error_type is ErrorType.INPUT
    assert "line 2" in exc.value.message


def test_line_failing_crud_validation_is_validation_error_with_line_number(
    snapshot: Snapshot,
) -> None:
    lines = [
        '{"name": "good", "location": "shared", "type": "ip-netmask", "value": "1.1.1.1"}',
        '{"name": "-bad", "location": "shared", "type": "ip-netmask", "value": "2.2.2.2"}',
    ]
    with pytest.raises(PscError) as exc:
        portability.plan_import(snapshot, lines, ObjectKind.ADDRESS)
    assert exc.value.error_type is ErrorType.VALIDATION
    assert "line 2" in exc.value.message


def test_blank_lines_are_skipped(snapshot: Snapshot) -> None:
    lines = [
        "",
        '{"name": "n1", "location": "shared", "type": "ip-netmask", "value": "1.1.1.1"}',
        "   ",
    ]
    cs = portability.plan_import(snapshot, lines, ObjectKind.ADDRESS)
    assert len(cs.upserts) == 1
    assert cs.upserts[0].name == "n1"


# --- combined blockers gate the batch --------------------------------------


def test_combined_blockers_aggregate_across_lines(snapshot: Snapshot) -> None:
    # "grp-web" is an existing address-group in shared: creating an address of
    # that name is a cross-kind collision that must block the whole batch.
    lines = [
        '{"name": "fresh", "location": "shared", "type": "ip-netmask", "value": "1.1.1.1"}',
        '{"name": "grp-web", "location": "shared", "type": "ip-netmask", "value": "2.2.2.2"}',
    ]
    cs = portability.plan_import(snapshot, lines, ObjectKind.ADDRESS)
    assert cs.is_blocked
    assert any("grp-web" in b for b in cs.blockers)


def test_in_place_type_change_blocks(snapshot: Snapshot) -> None:
    # "fqdn-example" exists as an fqdn; re-importing it as ip-netmask must block.
    lines = [
        '{"name": "fqdn-example", "location": "shared", "type": "ip-netmask", "value": "9.9.9.9"}',
    ]
    cs = portability.plan_import(snapshot, lines, ObjectKind.ADDRESS)
    assert cs.is_blocked


# --- other kinds -----------------------------------------------------------


def test_import_services_roundtrip(snapshot: Snapshot) -> None:
    lines = portability.export_ndjson(snapshot, ObjectKind.SERVICE, scope=None)
    cs = portability.plan_import(snapshot, lines, ObjectKind.SERVICE)
    assert not cs.blockers
    assert len(cs.upserts) == len(lines)


def test_import_tags_roundtrip(snapshot: Snapshot) -> None:
    lines = portability.export_ndjson(snapshot, ObjectKind.TAG, scope=None)
    cs = portability.plan_import(snapshot, lines, ObjectKind.TAG)
    assert not cs.blockers
    assert len(cs.upserts) == len(lines)


def test_import_address_groups_roundtrip(snapshot: Snapshot) -> None:
    lines = portability.export_ndjson(snapshot, ObjectKind.ADDRESS_GROUP, scope=None)
    cs = portability.plan_import(snapshot, lines, ObjectKind.ADDRESS_GROUP)
    assert not cs.blockers
    assert len(cs.upserts) == len(lines)


def test_import_service_groups_roundtrip(snapshot: Snapshot) -> None:
    lines = portability.export_ndjson(snapshot, ObjectKind.SERVICE_GROUP, scope=None)
    cs = portability.plan_import(snapshot, lines, ObjectKind.SERVICE_GROUP)
    assert not cs.blockers
    assert len(cs.upserts) == len(lines)


def test_import_accepts_record_dicts_not_only_strings(snapshot: Snapshot) -> None:
    # plan_import must accept already-parsed dict records too (for a web UI).
    record = {"name": "d1", "location": "shared", "type": "ip-netmask", "value": "1.2.3.4"}
    cs = portability.plan_import(snapshot, [record], ObjectKind.ADDRESS)
    assert cs.upserts[0].name == "d1"
