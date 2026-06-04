from __future__ import annotations

import io
import json

from rich.console import Console

from psc.core.models import SHARED, Location
from psc.core.refs import Reference, Target
from psc.output.format import OutputFormat, render, to_jsonable


def test_location_serializes_as_name() -> None:
    assert to_jsonable(SHARED) == "shared"
    assert to_jsonable(Location.dg("DG1")) == "DG1"


def test_location_roundtrips_from_string() -> None:
    loc = Location.model_validate("DG1")
    assert loc.device_group == "DG1"
    assert Location.model_validate("shared").is_shared


def test_dataclass_is_jsonable() -> None:
    ref = Reference(
        target_name="x",
        namespace="address",
        referrer_kind="security-rule",
        referrer_name="r",
        referrer_location=SHARED,
        field="source",
        resolved=Target("address", "x", SHARED),
    )
    payload = to_jsonable(ref)
    # round-trips through json without error
    json.dumps(payload)
    assert payload["resolved"]["location"] == "shared"
    assert payload["referrer_location"] == "shared"


def _render_table(rows: list[dict[str, object]], **kw: object) -> str:
    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=False, color_system=None)
    render(console, OutputFormat.TABLE, rows=rows, table_title="t", **kw)
    return buf.getvalue()


def test_table_group_by_inserts_divider_between_groups() -> None:
    rows = [
        {"query": "10.0.0.1", "object": "a"},
        {"query": "10.0.0.1", "object": "b"},
        {"query": "10.0.0.2", "object": "c"},
    ]
    plain = _render_table(rows)
    grouped = _render_table(rows, group_by="query")
    # Grouping draws one extra horizontal rule between the two query blocks.
    assert grouped.count("\n") == plain.count("\n") + 1


def test_table_group_by_single_group_no_extra_divider() -> None:
    rows = [
        {"query": "10.0.0.1", "object": "a"},
        {"query": "10.0.0.1", "object": "b"},
    ]
    assert _render_table(rows, group_by="query") == _render_table(rows)
