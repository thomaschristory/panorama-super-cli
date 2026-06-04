"""`psc audit` — read-only checks on address objects (overlap/containment)."""

from __future__ import annotations

import typer

from psc.cli.runtime import Runtime
from psc.core.audit import find_overlapping_addresses
from psc.output.errors import ErrorType, PscError
from psc.output.format import render

app = typer.Typer(no_args_is_help=True)


@app.command("overlaps")
def overlaps(ctx: typer.Context) -> None:
    """List address objects whose IP ranges overlap or contain one another.

    Pure read: reports each containment/overlap pair once (broader object left
    for containment), honouring `--device-group` scope. ip-netmask and ip-range
    objects only — FQDN and ip-wildcard have no comparable range.
    """
    rt: Runtime = ctx.obj
    pairs = find_overlapping_addresses(rt.snapshot(), rt.scope())
    rows = [
        {
            "left": f"{p.left_location}/{p.left_name}",
            "left_value": p.left_value,
            "relationship": p.relationship.value,
            "right": f"{p.right_location}/{p.right_name}",
            "right_value": p.right_value,
        }
        for p in pairs
    ]
    if rt.strict and not pairs:
        raise PscError("no overlapping addresses", ErrorType.NOT_FOUND)
    render(rt.stdout, rt.output, model=pairs, rows=rows, table_title="overlapping addresses")
