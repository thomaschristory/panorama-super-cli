"""Tests for the config-wide discovery spokes (#95): duplicates scan, diff,
export, and the audit well-known-ports mode."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import DataTable, Input, Select

from psc.core.source import OfflineSource
from psc.tui.app import WorkbenchApp
from psc.tui.screens.audit import AuditScreen
from psc.tui.screens.diff import DiffScreen, diff_rows
from psc.tui.screens.duplicates import (
    DuplicatesScreen,
    add_bucket_to_selection,
    duplicate_buckets,
)
from psc.tui.screens.export import ExportScreen
from psc.tui.session import WorkbenchSession
from psc.tui.state import OutputMode


def _session(xml: str) -> WorkbenchSession:
    return WorkbenchSession(source=OfflineSource(xml), output_mode=OutputMode.SET)


def _app(xml: str) -> WorkbenchApp:
    return WorkbenchApp(_session(xml))


# --- duplicates scan (pure engine wiring) ------------------------------------


def test_duplicate_buckets_addresses(workbench_xml_scan: str) -> None:
    buckets = duplicate_buckets(_session(workbench_xml_scan), "address")
    assert len(buckets) == 1
    assert {m.name for m in buckets[0].members} == {"a-dup1", "a-dup2"}


def test_duplicate_buckets_services(workbench_xml_scan: str) -> None:
    buckets = duplicate_buckets(_session(workbench_xml_scan), "service")
    assert len(buckets) == 1
    assert {m.name for m in buckets[0].members} == {"svc-443-a", "svc-443-b"}


def test_duplicate_buckets_groups(workbench_xml_scan: str) -> None:
    buckets = duplicate_buckets(_session(workbench_xml_scan), "address-group")
    assert len(buckets) == 1
    assert {m.name for m in buckets[0].members} == {"grp-a", "grp-b"}


def test_duplicate_buckets_unknown_kind_is_empty(workbench_xml_scan: str) -> None:
    assert duplicate_buckets(_session(workbench_xml_scan), "tag") == []


def test_add_bucket_to_selection_adds_all_members(workbench_xml_scan: str) -> None:
    session = _session(workbench_xml_scan)
    bucket = duplicate_buckets(session, "address")[0]
    added = add_bucket_to_selection(session, bucket)
    assert added == 2
    # Members landed as address-kind selection items.
    assert {(i.kind, i.name) for i in session.selection} == {
        ("address", "a-dup1"),
        ("address", "a-dup2"),
    }


def test_add_bucket_to_selection_is_idempotent(workbench_xml_scan: str) -> None:
    session = _session(workbench_xml_scan)
    bucket = duplicate_buckets(session, "address")[0]
    assert add_bucket_to_selection(session, bucket) == 2
    # Re-sending the same bucket adds nothing and never toggles a member off.
    assert add_bucket_to_selection(session, bucket) == 0
    assert len(session.selection) == 2


def test_add_bucket_to_selection_uses_bucket_kind(workbench_xml_scan: str) -> None:
    session = _session(workbench_xml_scan)
    bucket = duplicate_buckets(session, "address-group")[0]
    add_bucket_to_selection(session, bucket)
    assert {i.kind for i in session.selection} == {"address-group"}
    assert {i.name for i in session.selection} == {"grp-a", "grp-b"}


@pytest.mark.asyncio
async def test_duplicates_spoke_sends_bucket_to_selection(workbench_xml_scan: str) -> None:
    app = _app(workbench_xml_scan)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()  # off the search Input
        await pilot.press("D")
        await pilot.pause()
        assert isinstance(app.screen, DuplicatesScreen)
        # Highlight the (only) address bucket and send it to the selection.
        await pilot.press("space")
        await pilot.pause()
        assert {(i.kind, i.name) for i in app.session.selection} == {
            ("address", "a-dup1"),
            ("address", "a-dup2"),
        }
        # The hub selection panel reflects the two new members underneath the spoke.
        assert app.query_one("#selection", DataTable).row_count == 2
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_duplicates_spoke_opens_and_toggles_kind(workbench_xml_scan: str) -> None:
    app = _app(workbench_xml_scan)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()  # off the search Input
        await pilot.press("D")
        await pilot.pause()
        assert isinstance(app.screen, DuplicatesScreen)
        table = app.screen.query_one("#dup-table", DataTable)
        assert table.row_count == 1  # one address bucket
        app.screen.query_one("#dup-kind", Select).value = "address-group"
        await pilot.pause()
        assert table.row_count == 1  # one group bucket
        await pilot.press("escape")
        await pilot.pause()


# --- diff (DG vs DG) ----------------------------------------------------------


def test_diff_rows_reports_dg_only_object(workbench_xml_two_dg: str) -> None:
    session = _session(workbench_xml_two_dg)
    # dg1 sees shared+dg1 ({anchor, dg-only}); dg2 sees shared only ({anchor}).
    removed = diff_rows(session, "dg1", "dg2")
    assert ("address", "removed", "dg-only", "") in removed
    added = diff_rows(session, "dg2", "dg1")
    assert ("address", "added", "dg-only", "") in added


def test_diff_rows_reports_changed_object(workbench_xml_shadow: str) -> None:
    # dg1 redefines shared 'anchor' with a different value -> reported as changed.
    # Guards against calling the `changed_fields` property as a method.
    rows = diff_rows(_session(workbench_xml_shadow), "shared", "dg1")
    changed = [r for r in rows if r[0] == "address" and r[1] == "changed" and r[2] == "anchor"]
    assert len(changed) == 1
    assert changed[0][3]  # non-empty detail listing the differing field(s)


@pytest.mark.asyncio
async def test_diff_spoke_single_scope_shows_guard(workbench_xml: str) -> None:
    # A config with no device-groups has only the 'shared' scope: the diff spoke
    # must render its guard message instead of crashing.
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("f")
        await pilot.pause()
        assert isinstance(app.screen, DiffScreen)
        app.screen.query_one("#diff-empty")  # raises if the guard did not render
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_diff_spoke_opens_and_lists(workbench_xml_two_dg: str) -> None:
    app = _app(workbench_xml_two_dg)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("f")
        await pilot.pause()
        assert isinstance(app.screen, DiffScreen)
        # Default base=shared, other=dg1 -> dg-only is added going shared->dg1.
        table = app.screen.query_one("#diff-table", DataTable)
        assert table.row_count >= 1
        await pilot.press("escape")
        await pilot.pause()


# --- export (NDJSON) ----------------------------------------------------------


@pytest.mark.asyncio
async def test_export_spoke_writes_ndjson(workbench_xml: str, tmp_path) -> None:
    out = tmp_path / "addresses.ndjson"
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("o")
        await pilot.pause()
        assert isinstance(app.screen, ExportScreen)
        app.screen.query_one("#export-path", Input).value = str(out)
        await pilot.press("ctrl+s")
        await pilot.pause()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3  # web-srv-01, web-srv-02, db-gw
    assert all("name" in json.loads(line) for line in lines)


@pytest.mark.asyncio
async def test_export_kind_toggle_writes_services(workbench_xml_scan: str, tmp_path) -> None:
    out = tmp_path / "services.ndjson"
    app = _app(workbench_xml_scan)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("o")
        await pilot.pause()
        app.screen.query_one("#export-kind", Select).value = "services"
        app.screen.query_one("#export-path", Input).value = str(out)
        await pilot.press("ctrl+s")
        await pilot.pause()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3  # svc-443-a, svc-443-b, svc-8443
    assert all("protocol" in json.loads(line) for line in lines)


@pytest.mark.asyncio
async def test_export_refuses_to_overwrite_source(workbench_xml: str) -> None:
    app = _app(workbench_xml)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("o")
        await pilot.pause()
        app.screen.query_one("#export-path", Input).value = workbench_xml  # the source
        await pilot.press("ctrl+s")
        await pilot.pause()
    # The guard must refuse: the source file is left untouched (still XML, not
    # overwritten with NDJSON).
    assert Path(workbench_xml).read_text(encoding="utf-8").lstrip().startswith("<?xml")


# --- audit well-known-ports mode ---------------------------------------------


@pytest.mark.asyncio
async def test_audit_overlaps_mode_renders_with_selection(workbench_xml_refs: str) -> None:
    # net-10-0-5 (10.0.5.0/24) contains web-srv-01 (10.0.5.10/32). Select both and
    # open audit in its default overlaps mode: the containment pair must render.
    app = _app(workbench_xml_refs)
    async with app.run_test() as pilot:
        # 10.0.5.10 matches web-srv-01 (/32) and the containing net-10-0-5 (/24).
        app.query_one("#search", Input).value = "10.0.5.10"
        await pilot.press("enter")
        await pilot.pause()
        results = app.query_one("#results", DataTable)
        results.focus()
        await pilot.press("space")
        results.move_cursor(row=1)
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AuditScreen)
        table = app.screen.query_one("#audit-table", DataTable)
        assert table.row_count >= 1  # the containment pair, in the default overlaps mode
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_audit_wellknown_mode_lists_matches(workbench_xml_scan: str) -> None:
    app = _app(workbench_xml_scan)
    async with app.run_test() as pilot:
        app.query_one("#results", DataTable).focus()
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AuditScreen)
        app.screen.query_one("#audit-mode", Select).value = "wellknown"
        await pilot.pause()
        table = app.screen.query_one("#audit-table", DataTable)
        # svc-443-a and svc-443-b both duplicate predefined service-https; svc-8443 does not.
        assert table.row_count == 2
        await pilot.press("escape")
        await pilot.pause()
