"""`psc find` — resolve IPs/values/names to objects."""

from __future__ import annotations

from pathlib import Path

import typer

from psc.cli.runtime import Runtime
from psc.core.resolve import find_ips, find_object
from psc.core.resolver import Resolver, default_resolver
from psc.output.errors import ErrorType, PscError
from psc.output.format import render

app = typer.Typer(no_args_is_help=True)


@app.command("ip")
def ip(
    ctx: typer.Context,
    targets: list[str] | None = typer.Argument(None, help="IP / CIDR / range / FQDN."),
    file: Path | None = typer.Option(
        None, "--file", "-f", help="Read targets from a file (one per line; # comments)."
    ),
    exact: bool = typer.Option(
        False, "--exact", "-e", help="Only exact matches (drop contains/within)."
    ),
    resolve_fqdn: bool = typer.Option(
        False,
        "--resolve-fqdn/--no-resolve-fqdn",
        help="DNS-resolve FQDN objects and match those whose A/AAAA include the IP.",
    ),
) -> None:
    """Find which address objects/groups match an IP (or a whole list).

    Reports exact matches, broader objects that *contain* the target, and
    narrower objects *within* it, plus the address-groups that carry them.
    With --exact, only objects equal to the target are reported (netmask and
    bare-host forms still count as equal, e.g. 10.0.0.10 == 10.0.0.10/32).
    With --resolve-fqdn, FQDN objects are DNS-resolved (cached, timeout-bounded)
    and match when their addresses include the queried IP; the offline default
    never touches DNS.
    """
    rt: Runtime = ctx.obj
    snap = rt.snapshot()
    items = list(targets or [])
    if file:
        try:
            text = file.read_text(encoding="utf-8")
        except OSError as exc:
            raise PscError(f"cannot read {file}: {exc}", ErrorType.INPUT) from exc
        items += [
            ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
        ]
    if not items:
        raise PscError(
            "provide one or more IP/CIDR/range/FQDN targets, or --file", ErrorType.VALIDATION
        )

    # The CLI owns construction of the real resolver; core stays UI-free and
    # only gets a resolver when the flag is on, so the offline path is untouched.
    resolver: Resolver | None = default_resolver() if resolve_fqdn else None
    results = find_ips(
        snap, items, rt.scope(), exact=exact, resolve_fqdn=resolve_fqdn, resolver=resolver
    )

    failures = sum(r.fqdn_resolution_failures for r in results)
    if failures:
        rt.stderr.print(
            f"warning: {failures} FQDN object(s) skipped — DNS resolution failed", style="yellow"
        )

    rows: list[dict[str, object]] = []
    for res in results:
        if not res.matches:
            rows.append(
                {
                    "query": res.query,
                    "match": "(none)",
                    "object": "",
                    "location": "",
                    "type": "",
                    "value": "",
                }
            )
        for m in res.matches:
            rows.append(
                {
                    "query": res.query,
                    "match": m.match.value,
                    "object": m.name,
                    "location": m.location,
                    "type": m.type,
                    "value": m.value,
                }
            )

    if rt.strict and not any(r.matches for r in results):
        raise PscError("no matching objects", ErrorType.NOT_FOUND)

    model = results if len(results) != 1 else results[0]
    # Group the table by query so multi-target output (e.g. --file) draws a rule
    # between each target's matches instead of one undifferentiated block.
    render(rt.stdout, rt.output, model=model, rows=rows, table_title="find ip", group_by="query")


@app.command("object")
def obj(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Exact object name to locate."),
) -> None:
    """Find every object (any kind, any location) with this exact name."""
    rt: Runtime = ctx.obj
    hits = find_object(rt.snapshot(), name)
    rows = [
        {"kind": h.kind, "name": h.name, "location": h.location, "detail": h.detail} for h in hits
    ]
    if rt.strict and not hits:
        raise PscError(f"no object named '{name}'", ErrorType.NOT_FOUND)
    render(rt.stdout, rt.output, model=hits, rows=rows, table_title=f"objects named '{name}'")
