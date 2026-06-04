"""`psc decommission` — reference-safe teardown of address objects by IP/CIDR.

Resolve a host/subnet/list to the address objects that represent it, then plan
their safe removal: scrub from groups, scrub from rule source/destination,
delete orphaned rules, delete emptied groups, delete the objects — in that
order. Dry-run by default; `--apply` (offline → `--out`, or live) executes.
"""

from __future__ import annotations

from pathlib import Path

import typer

from psc.cli._plan import OUT_FORMAT_OPTION, complete
from psc.cli.runtime import Runtime
from psc.core.decommission import plan_decommission
from psc.core.models import Address, Location
from psc.core.normalize import MatchKind
from psc.core.refs import ReferenceGraph
from psc.core.resolve import find_ips
from psc.core.source import ConfigFormat
from psc.output.errors import ErrorType, PscError

# Only objects whose value is EXACTLY the target or WITHIN the target cidr/range
# are torn down. A broader object that merely CONTAINS a narrow target denotes
# more than what the operator named, so removing it would be destructive beyond
# intent — it is deliberately excluded.
_DECOMMISSION_MATCHES = (MatchKind.EXACT, MatchKind.WITHIN)


def decommission(
    ctx: typer.Context,
    targets: list[str] | None = typer.Argument(
        None, help="IP / CIDR / range to decommission (repeatable)."
    ),
    target: list[str] | None = typer.Option(
        None, "--target", help="Additional target (repeatable); same as a positional arg."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", help="Read targets from a file (one per line; # comments)."
    ),
    scope: str | None = typer.Option(
        None,
        "--scope",
        help="Limit object search to this device-group (plus inherited shared). "
        "Default: the global --device-group, else search everywhere.",
    ),
    keep_groups: bool = typer.Option(
        False,
        "--keep-groups",
        help="Scrub group/rule member fields but delete neither groups nor objects.",
    ),
    keep_rules: bool = typer.Option(
        False,
        "--keep-rules",
        help="Keep rules that become orphaned (empty source/destination) instead of deleting them.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Execute the teardown (default: dry-run)."),
    out: str | None = typer.Option(
        None,
        "--out",
        help="Write the plan artifact (set script or rewritten config) to this file.",
    ),
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Reference-safe teardown of every address object matching an IP/CIDR/list.

    Dry-run by default: prints the ordered plan (group scrub → rule scrub →
    orphan-rule delete → empty-group delete → object delete) plus any warnings
    and blockers. Only objects whose value is EXACTLY the target or WITHIN the
    given CIDR/range are removed; a broader object that merely contains the
    target is left in place.
    """
    rt: Runtime = ctx.obj
    items = list(targets or []) + list(target or [])
    if file:
        try:
            text = file.read_text(encoding="utf-8")
        except OSError as exc:
            raise PscError(f"cannot read {file}: {exc}", ErrorType.INPUT) from exc
        items += [
            ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
        ]
    if not items:
        raise PscError("provide one or more IP/CIDR/range targets, or --file", ErrorType.VALIDATION)

    snap = rt.snapshot()
    loc = _resolve_scope(scope) if scope is not None else rt.scope()

    results = find_ips(snap, items, loc)
    index = snap.address_index()
    matched: list[Address] = []
    seen: set[tuple[str, str]] = set()
    for res in results:
        for m in res.matches:
            if m.match not in _DECOMMISSION_MATCHES:
                continue
            ident = (m.location, m.name)
            if ident in seen:
                continue
            obj = index.get(ident)
            if obj is not None:
                seen.add(ident)
                matched.append(obj)

    graph = ReferenceGraph.build(snap)
    cs = plan_decommission(
        snap, graph, matched, scope=loc, keep_groups=keep_groups, keep_rules=keep_rules
    )
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)


def _resolve_scope(name: str) -> Location:
    return Location.shared() if name == "shared" else Location.dg(name)
