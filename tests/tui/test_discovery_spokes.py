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
from psc.tui.screens.duplicates import DuplicatesScreen, duplicate_buckets
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
