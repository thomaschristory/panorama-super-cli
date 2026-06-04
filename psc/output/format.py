"""Format dispatch: render a result as table / json / jsonl / yaml / csv / set.

Commands build up to three views of their result and hand them to `render`:

- `model` — the canonical structured object (used for json/jsonl/yaml).
- `rows` — a flat list of dicts (used for table/csv).
- `set_lines` — pre-rendered PAN-OS `set` commands (used for set).

A command only needs to supply the views that make sense for it; if a chosen
format has no matching view, `render` falls back to JSON so an agent always
gets *something* parseable rather than an error.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table
from ruamel.yaml import YAML


class OutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"
    JSONL = "jsonl"
    YAML = "yaml"
    CSV = "csv"
    SET = "set"


def to_jsonable(data: Any) -> Any:
    if isinstance(data, BaseModel):
        return data.model_dump(mode="json")
    if dataclasses.is_dataclass(data) and not isinstance(data, type):
        return {f.name: to_jsonable(getattr(data, f.name)) for f in dataclasses.fields(data)}
    if isinstance(data, Enum):
        return data.value
    if isinstance(data, list | tuple):
        return [to_jsonable(x) for x in data]
    if isinstance(data, dict):
        return {k: to_jsonable(v) for k, v in data.items()}
    return data


def _json(data: Any) -> str:
    return json.dumps(to_jsonable(data), indent=2, ensure_ascii=False)


def _jsonl(model: Any) -> str:
    payload = to_jsonable(model)
    items = payload if isinstance(payload, list) else [payload]
    return "\n".join(json.dumps(x, ensure_ascii=False) for x in items)


def make_yaml() -> YAML:
    """A block-style (non-flow) ruamel YAML emitter, shared with config save."""
    yaml = YAML()
    yaml.default_flow_style = False
    return yaml


def _yaml(data: Any) -> str:
    buf = io.StringIO()
    make_yaml().dump(to_jsonable(data), buf)
    return buf.getvalue().rstrip("\n")


def _csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    # dict.fromkeys preserves first-seen column order while de-duplicating.
    fields = list(dict.fromkeys(k for row in rows for k in row))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _cell(v) for k, v in row.items()})
    return buf.getvalue().rstrip("\n")


def _cell(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if value is None:
        return ""
    return str(value)


def _raw(console: Console, text: str) -> None:
    """Print machine output verbatim — no markup, no highlight, no wrapping."""
    console.print(text, markup=False, highlight=False, soft_wrap=True)


def render(
    console: Console,
    fmt: OutputFormat,
    *,
    model: Any = None,
    rows: list[dict[str, Any]] | None = None,
    set_lines: list[str] | None = None,
    table_title: str | None = None,
    group_by: str | None = None,
) -> None:
    """Print `model`/`rows`/`set_lines` in `fmt` to `console`.

    The console must be the *stdout* console; errors go elsewhere. Table output
    is the only format that uses rich styling — every other format is plain text
    so pipes and captures stay clean.

    `group_by` is a table-only hint: a column name whose changing value marks a
    new block. A horizontal rule is drawn between blocks so multi-target output
    (e.g. `find ip --file`) is easy to scan. It does not affect machine formats.
    """
    # `soft_wrap=True` is critical: without it rich wraps long lines to the
    # console width and injects newlines *inside* JSON string values, producing
    # invalid JSON. Machine formats must never be wrapped.
    if fmt is OutputFormat.SET:
        text = _json(model) if set_lines is None else "\n".join(set_lines)
        _raw(console, text)
        return
    if fmt is OutputFormat.JSON:
        _raw(console, _json(model))
        return
    if fmt is OutputFormat.JSONL:
        _raw(console, _jsonl(model))
        return
    if fmt is OutputFormat.YAML:
        _raw(console, _yaml(model))
        return
    if fmt is OutputFormat.CSV:
        _raw(console, _csv(rows or []))
        return
    # TABLE
    if rows:
        table = Table(title=table_title, header_style="bold cyan")
        for col in rows[0]:
            table.add_column(col)
        prev: Any = None
        for i, row in enumerate(rows):
            if group_by and i > 0 and row.get(group_by) != prev:
                table.add_section()
            table.add_row(*[_cell(row.get(col)) for col in rows[0]])
            if group_by:
                prev = row.get(group_by)
        console.print(table)
    else:
        console.print(_json(model), markup=False, highlight=False)
