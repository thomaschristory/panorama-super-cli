"""`psc audit` — read-only hygiene checks: address overlap/containment and
custom services duplicating predefined/well-known ports."""

from __future__ import annotations

import typer

from psc.cli.runtime import Runtime
from psc.core.audit import find_overlapping_addresses, find_wellknown_duplicate_services
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


@app.command("services-vs-wellknown")
def services_vs_wellknown(ctx: typer.Context) -> None:
    """Flag custom services that duplicate a predefined/well-known port.

    Pure read: lists each custom service whose single destination port matches a
    predefined PAN-OS service (e.g. service-http) or an IANA well-known port
    (e.g. ssh), so it can be consolidated. The `kind` column distinguishes a
    real predefined object from a mere well-known port number. Honours
    `--device-group` scope; ranges and multi-port objects are never flagged.
    """
    rt: Runtime = ctx.obj
    matches = find_wellknown_duplicate_services(rt.snapshot(), rt.scope())
    rows = [
        {
            "service": f"{m.service_location}/{m.service_name}",
            "protocol": m.protocol,
            "port": m.port,
            "canonical": m.canonical_name,
            "kind": m.kind.value,
        }
        for m in matches
    ]
    if rt.strict and not matches:
        raise PscError("no services duplicating well-known ports", ErrorType.NOT_FOUND)
    render(rt.stdout, rt.output, model=matches, rows=rows, table_title="services vs well-known")
