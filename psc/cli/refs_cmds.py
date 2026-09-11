"""`psc refs` — where-used, unused, and dangling references."""

from __future__ import annotations

import typer

from psc.cli._options import location_from_name
from psc.cli.runtime import Runtime
from psc.core.livedag import LiveDagMembership
from psc.core.refs import ReferenceGraph
from psc.core.resolve import find_object
from psc.core.source import LiveSource
from psc.output.errors import ErrorType, PscError
from psc.output.format import render

app = typer.Typer(no_args_is_help=True)

_KINDS = ("address", "address-group", "service", "service-group", "tag")

_LIVE_DAG_HELP = (
    "Read the registered IPs of the connected firewalls. Add their tags to "
    "dynamic address-group membership. This option needs a live source."
)
_LIVE_DAG_PARTIAL_HELP = (
    "Continue when a firewall does not answer. The caveat then names the "
    "firewalls that psc could not read. This option needs --live-dag."
)


def _live_dag_membership(rt: Runtime, live_dag: bool, partial: bool) -> LiveDagMembership | None:
    """Read the live registered IPs, or return None when the caller opted out.

    The guard runs before any graph work, so an offline run fails before it
    prints a row. The refusal is a CONFIG error, not a NOT_FOUND error: an agent
    must be able to tell an unusable source from an empty result.
    """
    if partial and not live_dag:
        raise PscError("--live-dag-partial needs --live-dag", ErrorType.CONFIG)
    if not live_dag:
        return None
    src = rt.source()
    if not isinstance(src, LiveSource):
        raise PscError(
            "--live-dag needs a live source, because registered IPs are runtime "
            "state. Pass --profile <name>, or drop --live-dag.",
            ErrorType.CONFIG,
        )
    return src.live_dag_membership(partial=partial)


def _unused_caveat(live: LiveDagMembership | None, unmatched: int = 0) -> str:
    """The stderr blind-spot notice, in its config-only or live form.

    `unused` only reads device-group objects and the policy rulebases. An object
    that a template or the network configuration references is not scanned, and
    it looks unused here. Dynamic address-group membership from a config tag is
    scanned. Membership from an externally registered IP is runtime state, and
    only `--live-dag` resolves it. A pure function keeps the two texts testable
    without a device (#183).

    `unmatched` is how many registered values no address object carries. The
    count belongs in the caveat, not in `graph.warnings`: on a real estate most
    registered IPs have no address object, so a warning would fire on every run
    and it would devalue that channel. `--no-caveat` silences this line too.
    """
    head = (
        "[yellow]caveat[/yellow]: candidates only — these are unreferenced by the "
        "scanned objects/policy rulebases. NOT scanned: templates & network/device "
        "config"
    )
    tail = "Verify before deleting (esp. shared). See docs: Coverage and blind spots."
    if live is None:
        return (
            f"{head}, and DAG membership from externally registered IPs (config-tag DAG "
            f"membership is scanned). {tail}"
        )
    count = len(live.devices)
    plural = "" if count == 1 else "s"
    scope = (
        f"psc read the registered IPs of {count} firewall{plural}, so DAG membership "
        "from externally registered IPs IS scanned."
    )
    if live.is_partial:
        scope = (
            f"psc read the registered IPs of {count} firewall{plural}, so DAG membership "
            "from externally registered IPs is scanned in part. Coverage is partial: "
            f"{', '.join(live.failed_devices)} did not answer."
        )
    if unmatched:
        scope += f" No address object carries {unmatched} of the registered values."
    return f"{head}. {scope} {tail}"


def _emit_graph_warnings(rt: Runtime, graph: ReferenceGraph) -> None:
    """Surface non-fatal coverage gaps (e.g. an unparseable DAG filter whose
    membership could not be resolved) on stderr, so stdout stays pure machine
    output."""
    for w in graph.warnings:
        rt.stderr.print(f"[yellow]warning[/yellow]: {w}", soft_wrap=True, highlight=False)


def _emit_live_warnings(rt: Runtime, live: LiveDagMembership | None) -> None:
    """Surface a row of live data that psc could not read (#183).

    A device row with no `ip` attribute, or a registered value that is not an
    address value, is a real coverage gap. It is rare, so it belongs on the
    warning channel. The per-run coverage count does not; it goes in the caveat.
    """
    if live is None:
        return
    for w in live.warnings:
        rt.stderr.print(f"[yellow]warning[/yellow]: {w}", soft_wrap=True, highlight=False)


@app.command("used")
def used(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Object name to trace."),
    kind: str | None = typer.Option(None, "--kind", help=f"One of: {', '.join(_KINDS)}."),
    location: str | None = typer.Option(None, "--location", help="shared or a device-group."),
    live_dag: bool = typer.Option(False, "--live-dag", help=_LIVE_DAG_HELP),
    live_dag_partial: bool = typer.Option(False, "--live-dag-partial", help=_LIVE_DAG_PARTIAL_HELP),
) -> None:
    """List every reference that resolves to a given object (the delete/rename pre-flight)."""
    rt: Runtime = ctx.obj
    live = _live_dag_membership(rt, live_dag, live_dag_partial)
    snap = rt.snapshot()
    graph = ReferenceGraph.build(snap, live_dag=live)

    if kind is None or location is None:
        hits = find_object(snap, name)
        if not hits:
            raise PscError(f"no object named '{name}'", ErrorType.NOT_FOUND)
        if len(hits) > 1 and (kind is None or location is None):
            raise PscError(
                f"'{name}' is ambiguous ({len(hits)} objects); pass --kind and --location",
                ErrorType.VALIDATION,
                details={"candidates": [{"kind": h.kind, "location": h.location} for h in hits]},
            )
        kind = kind or hits[0].kind
        location = location or hits[0].location

    loc = location_from_name(location)
    refs = graph.where_used(kind, name, loc)
    _emit_graph_warnings(rt, graph)
    # Every other field describes the referrer, so `tags` does too. It holds the
    # tags of the rule or the group that points at the object. It does not hold
    # the tags of the object that you trace. A rule tag often records a ticket
    # or an owner. Thus a delete pre-flight can route on it (#184). These rows
    # feed table and csv only. The json/jsonl/yaml views render `model=refs`, so
    # `Reference` carries the field as well.
    rows = [
        {
            "referrer_kind": r.referrer_kind,
            "referrer": r.referrer_name,
            "location": r.referrer_location.name,
            "rulebase": r.rulebase.value if r.rulebase else "",
            "field": r.field,
            "tags": list(r.tags),
        }
        for r in refs
    ]
    if rt.strict and not refs:
        raise PscError(f"'{name}' is unused", ErrorType.NOT_FOUND)
    render(rt.stdout, rt.output, model=refs, rows=rows, table_title=f"where '{name}' is used")


@app.command("unused")
def unused(
    ctx: typer.Context,
    kind: str = typer.Option("address", "--kind", help=f"One of: {', '.join(_KINDS)}."),
    ignore_disabled: bool = typer.Option(
        False,
        "--ignore-disabled",
        help="Treat disabled rules as non-references; surface objects used only by disabled rules.",
    ),
    caveat: bool = typer.Option(
        True,
        "--caveat/--no-caveat",
        help="Print the scan-scope blind-spot caveat on stderr (--no-caveat to suppress).",
    ),
    live_dag: bool = typer.Option(False, "--live-dag", help=_LIVE_DAG_HELP),
    live_dag_partial: bool = typer.Option(False, "--live-dag-partial", help=_LIVE_DAG_PARTIAL_HELP),
) -> None:
    """List objects no rule reaches — directly or transitively through groups."""
    rt: Runtime = ctx.obj
    live = _live_dag_membership(rt, live_dag, live_dag_partial)
    snap = rt.snapshot()
    graph = ReferenceGraph.build(snap, live_dag=live)
    targets = graph.unused(kind, ignore_disabled=ignore_disabled)
    # `tags` surfaces the DAG-via-tag blind spot the caveat below warns about: an
    # object matched into a DAG by an externally-registered IP looks unused here,
    # but its tags reveal it may be live at runtime (#180). Table/csv join the
    # list; json/jsonl/yaml carry it verbatim.
    rows = [
        {
            "kind": t.kind,
            "name": t.name,
            "location": t.location.name,
            "tags": graph.tags_for(t),
        }
        for t in targets
    ]
    if rt.strict and not targets:
        raise PscError(f"no unused {kind}", ErrorType.NOT_FOUND)
    render(rt.stdout, rt.output, model=rows, rows=rows, table_title=f"unused {kind}")
    _emit_graph_warnings(rt, graph)
    _emit_live_warnings(rt, live)
    if targets and caveat:
        # Warn on stderr so stdout stays pure machine output (#56). The text
        # changes when live data resolved the registered-IP clause, and
        # `--no-caveat` still silences every line (#183).
        unmatched = live.unmatched_values(snap.addresses) if live else 0
        rt.stderr.print(_unused_caveat(live, unmatched), soft_wrap=True, highlight=False)


@app.command("dangling")
def dangling(ctx: typer.Context) -> None:
    """List references that point at names that don't resolve to any object."""
    rt: Runtime = ctx.obj
    graph = ReferenceGraph.build(rt.snapshot())
    refs = graph.dangling()
    _emit_graph_warnings(rt, graph)
    # `dangling` renders the same `Reference` model as `used`, so its
    # json/jsonl/yaml views already carry the referrer `tags` field. The table
    # and the csv view read `rows`, so they need the field here to agree (#184).
    rows = [
        {
            "referrer_kind": r.referrer_kind,
            "referrer": r.referrer_name,
            "location": r.referrer_location.name,
            "field": r.field,
            "missing": r.target_name,
            "tags": list(r.tags),
        }
        for r in refs
    ]
    if rt.strict and refs:
        raise PscError(f"{len(refs)} dangling references", ErrorType.CONFLICT)
    render(rt.stdout, rt.output, model=refs, rows=rows, table_title="dangling references")
