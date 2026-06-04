"""`psc set` — create or update a single address/service/tag object.

Each subcommand maps to one `crud` planner, resolves the target location
(`--location` > global `--device-group` > `shared`), and hands the resulting
one-upsert `ChangeSet` to the shared dry-run/apply flow. Protocol/color choices
are validated in the engine (not via Typer enums) so a bad value surfaces
through the typed `ErrorType.VALIDATION` contract (exit 4) rather than a Typer
usage error (exit 2).

Note: live `--apply` of an *update* (the object already exists) is rejected with
`ErrorType.CONFIG` by `apply_live` — a live read-modify-write would drop
unlisted fields. Apply updates offline instead (`--config … --apply --out`).
"""

from __future__ import annotations

import typer

from psc.cli._plan import OUT_FORMAT_OPTION, complete
from psc.cli.runtime import Runtime
from psc.core import crud
from psc.core.models import AddressType, Location
from psc.core.source import ConfigFormat

app = typer.Typer(no_args_is_help=True)

_LOCATION_OPTION = typer.Option(
    None, "--location", help="Target location (default: global --device-group, else shared)."
)
_APPLY_OPTION = typer.Option(False, "--apply", help="Execute the change (default: dry-run).")
_OUT_OPTION = typer.Option(
    None,
    "--out",
    help="Write the plan artifact (set script or rewritten config) to this file; "
    "honoured even in a dry-run (see --output-format).",
)


def _resolve_location(rt: Runtime, location: str | None) -> Location:
    name = location or rt.device_group or "shared"
    return Location.shared() if name == "shared" else Location.dg(name)


@app.command("address")
def address(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="Object name."),
    type_: str = typer.Option(
        ..., "--type", help="Value kind: ip-netmask | ip-range | ip-wildcard | fqdn."
    ),
    value: str = typer.Option(..., "--value", help="The address value (stored verbatim)."),
    description: str | None = typer.Option(None, "--description"),
    tag: list[str] = typer.Option([], "--tag", help="Tag name (repeatable)."),
    location: str | None = _LOCATION_OPTION,
    apply: bool = _APPLY_OPTION,
    out: str | None = _OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Create or update an address object.

    Live --apply only creates; updating an existing object live is refused
    (CONFIG) — use offline --apply (--config … --apply --out) for updates.
    """
    rt: Runtime = ctx.obj
    addr_type = AddressType(type_) if type_ in AddressType._value2member_map_ else None
    if addr_type is None:
        from psc.output.errors import ErrorType, PscError  # noqa: PLC0415 — engine contract

        raise PscError(
            f"--type '{type_}' is invalid (ip-netmask | ip-range | ip-wildcard | fqdn)",
            ErrorType.VALIDATION,
        )
    cs = crud.plan_address(
        rt.snapshot(),
        name,
        addr_type,
        value,
        description=description,
        tags=tag,
        location=_resolve_location(rt, location),
    )
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)


@app.command("address-group")
def address_group(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="Group name."),
    member: list[str] = typer.Option(
        [], "--member", help="Static member name (repeatable). Mutually exclusive with --filter."
    ),
    filter_: str | None = typer.Option(
        None, "--filter", help="Dynamic tag filter expression. Mutually exclusive with --member."
    ),
    description: str | None = typer.Option(None, "--description"),
    tag: list[str] = typer.Option([], "--tag", help="Tag name (repeatable)."),
    location: str | None = _LOCATION_OPTION,
    apply: bool = _APPLY_OPTION,
    out: str | None = _OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Create or update an address-group (exactly one of --member.../--filter).

    Live --apply only creates; updating an existing group live is refused
    (CONFIG) — use offline --apply for updates.
    """
    rt: Runtime = ctx.obj
    cs = crud.plan_address_group(
        rt.snapshot(),
        name,
        static_members=list(member) or None,
        dynamic_filter=filter_,
        description=description,
        tags=tag,
        location=_resolve_location(rt, location),
    )
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)


@app.command("service")
def service(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="Service name."),
    protocol: str = typer.Option(..., "--protocol", help="tcp | udp."),
    dest_port: str | None = typer.Option(None, "--dest-port", help="Destination port(s)."),
    source_port: str | None = typer.Option(None, "--source-port", help="Source port(s)."),
    description: str | None = typer.Option(None, "--description"),
    tag: list[str] = typer.Option([], "--tag", help="Tag name (repeatable)."),
    location: str | None = _LOCATION_OPTION,
    apply: bool = _APPLY_OPTION,
    out: str | None = _OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Create or update a service object (at least one of --dest-port/--source-port).

    Live --apply only creates; updating an existing service live is refused
    (CONFIG) — use offline --apply for updates.
    """
    rt: Runtime = ctx.obj
    cs = crud.plan_service(
        rt.snapshot(),
        name,
        protocol,
        destination_port=dest_port,
        source_port=source_port,
        description=description,
        tags=tag,
        location=_resolve_location(rt, location),
    )
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)


@app.command("service-group")
def service_group(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="Group name."),
    member: list[str] = typer.Option(..., "--member", help="Member name (repeatable, >=1)."),
    tag: list[str] = typer.Option([], "--tag", help="Tag name (repeatable)."),
    location: str | None = _LOCATION_OPTION,
    apply: bool = _APPLY_OPTION,
    out: str | None = _OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Create or update a service-group.

    Live --apply only creates; updating an existing group live is refused
    (CONFIG) — use offline --apply for updates.
    """
    rt: Runtime = ctx.obj
    cs = crud.plan_service_group(
        rt.snapshot(),
        name,
        list(member),
        tags=tag,
        location=_resolve_location(rt, location),
    )
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)


@app.command("tag")
def tag(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="Tag name (<=127 chars)."),
    color: str | None = typer.Option(None, "--color", help="color1..color42."),
    comments: str | None = typer.Option(None, "--comments"),
    location: str | None = _LOCATION_OPTION,
    apply: bool = _APPLY_OPTION,
    out: str | None = _OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Create or update a tag.

    Live --apply only creates; updating an existing tag live is refused
    (CONFIG) — use offline --apply for updates.
    """
    rt: Runtime = ctx.obj
    cs = crud.plan_tag(
        rt.snapshot(),
        name,
        color=color,
        comments=comments,
        location=_resolve_location(rt, location),
    )
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)
