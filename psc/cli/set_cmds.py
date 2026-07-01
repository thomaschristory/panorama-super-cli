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

Bulk import: every subcommand also accepts `-f/--file <objs.ndjson>`, which
parses NDJSON and plans the *whole batch* as one combined `ChangeSet` via
`portability.plan_import` (the same crud validation, aggregated). It flows
through the identical dry-run-default + `--apply` gate — import never writes
objects directly, and one blocker refuses the whole file.
"""

from __future__ import annotations

import typer

from psc.cli._options import (
    APPLY_OPTION,
    LOCATION_OPTION,
    OUT_OPTION,
    location_from_name,
)
from psc.cli._plan import OUT_FORMAT_OPTION, complete
from psc.cli.runtime import Runtime
from psc.core import crud, portability
from psc.core.changeset import ObjectKind
from psc.core.models import AddressType, Location
from psc.core.source import ConfigFormat
from psc.output.errors import ErrorType, PscError

app = typer.Typer(no_args_is_help=True)

FILE_OPTION = typer.Option(
    None,
    "--file",
    "-f",
    help="Bulk import objects of this kind from an NDJSON file (one JSON object "
    "per line). Plans the whole batch as one reviewable ChangeSet; the flag "
    "options are ignored in this mode.",
)


def _resolve_location(rt: Runtime, location: str | None) -> Location:
    return location_from_name(location or rt.device_group or "shared")


def _import_file(
    rt: Runtime,
    path: str,
    kind: ObjectKind,
    *,
    apply: bool,
    out: str | None,
    out_format: ConfigFormat,
) -> None:
    """Plan a bulk NDJSON import and run it through the shared apply/out flow."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        raise PscError(f"cannot read {path}: {exc}", ErrorType.INPUT) from exc
    cs = portability.plan_import(rt.snapshot(), lines, kind)
    complete(rt, cs, apply=apply, out_path=out, out_format=out_format)


def _require(**options: object) -> None:
    """Enforce single-object options that are only optional in `-f` import mode.

    Typer requires these flags for a normal `set`, but `-f` supplies them from
    the file instead, so they default to `None`. Raise the usual VALIDATION
    error when neither a value nor a file was given.
    """
    missing = [f"--{k.rstrip('_')}" for k, v in options.items() if v is None]
    if missing:
        raise PscError(
            f"missing required option(s): {', '.join(missing)} (or pass -f <file> to import)",
            ErrorType.VALIDATION,
        )


@app.command("address")
def address(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", help="Object name."),
    type_: str | None = typer.Option(
        None, "--type", help="Value kind: ip-netmask | ip-range | ip-wildcard | fqdn."
    ),
    value: str | None = typer.Option(None, "--value", help="The address value (stored verbatim)."),
    description: str | None = typer.Option(None, "--description"),
    tag: list[str] = typer.Option([], "--tag", help="Tag name (repeatable)."),
    file: str | None = FILE_OPTION,
    location: str | None = LOCATION_OPTION,
    apply: bool = APPLY_OPTION,
    out: str | None = OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Create or update an address object (or bulk-import with -f).

    Live --apply only creates; updating an existing object live is refused
    (CONFIG) — use offline --apply (--config … --apply --out) for updates.
    """
    rt: Runtime = ctx.obj
    if file is not None:
        _import_file(rt, file, ObjectKind.ADDRESS, apply=apply, out=out, out_format=output_format)
        return
    _require(name=name, type=type_, value=value)
    assert name is not None and type_ is not None and value is not None
    addr_type = AddressType(type_) if type_ in AddressType._value2member_map_ else None
    if addr_type is None:
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
    name: str | None = typer.Option(None, "--name", help="Group name."),
    member: list[str] = typer.Option(
        [], "--member", help="Static member name (repeatable). Mutually exclusive with --filter."
    ),
    filter_: str | None = typer.Option(
        None, "--filter", help="Dynamic tag filter expression. Mutually exclusive with --member."
    ),
    description: str | None = typer.Option(None, "--description"),
    tag: list[str] = typer.Option([], "--tag", help="Tag name (repeatable)."),
    file: str | None = FILE_OPTION,
    location: str | None = LOCATION_OPTION,
    apply: bool = APPLY_OPTION,
    out: str | None = OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Create/update an address-group (exactly one of --member.../--filter), or bulk-import with -f.

    Live --apply only creates; updating an existing group live is refused
    (CONFIG) — use offline --apply for updates.
    """
    rt: Runtime = ctx.obj
    if file is not None:
        _import_file(
            rt, file, ObjectKind.ADDRESS_GROUP, apply=apply, out=out, out_format=output_format
        )
        return
    _require(name=name)
    assert name is not None
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
    name: str | None = typer.Option(None, "--name", help="Service name."),
    protocol: str | None = typer.Option(None, "--protocol", help="tcp | udp."),
    dest_port: str | None = typer.Option(
        None, "--dest-port", help="Destination port(s) — required (PAN-OS mandates it)."
    ),
    source_port: str | None = typer.Option(
        None, "--source-port", help="Source port(s) — optional."
    ),
    description: str | None = typer.Option(None, "--description"),
    tag: list[str] = typer.Option([], "--tag", help="Tag name (repeatable)."),
    file: str | None = FILE_OPTION,
    location: str | None = LOCATION_OPTION,
    apply: bool = APPLY_OPTION,
    out: str | None = OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Create/update a service (--dest-port required; --source-port optional), or import with -f.

    Live --apply only creates; updating an existing service live is refused
    (CONFIG) — use offline --apply for updates.
    """
    rt: Runtime = ctx.obj
    if file is not None:
        _import_file(rt, file, ObjectKind.SERVICE, apply=apply, out=out, out_format=output_format)
        return
    _require(name=name, protocol=protocol)
    assert name is not None and protocol is not None
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
    name: str | None = typer.Option(None, "--name", help="Group name."),
    member: list[str] = typer.Option([], "--member", help="Member name (repeatable, >=1)."),
    tag: list[str] = typer.Option([], "--tag", help="Tag name (repeatable)."),
    file: str | None = FILE_OPTION,
    location: str | None = LOCATION_OPTION,
    apply: bool = APPLY_OPTION,
    out: str | None = OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Create or update a service-group, or bulk-import with -f.

    Live --apply only creates; updating an existing group live is refused
    (CONFIG) — use offline --apply for updates.
    """
    rt: Runtime = ctx.obj
    if file is not None:
        _import_file(
            rt, file, ObjectKind.SERVICE_GROUP, apply=apply, out=out, out_format=output_format
        )
        return
    _require(name=name)
    assert name is not None
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
    name: str | None = typer.Option(None, "--name", help="Tag name (<=127 chars)."),
    color: str | None = typer.Option(None, "--color", help="color1..color42."),
    comments: str | None = typer.Option(None, "--comments"),
    file: str | None = FILE_OPTION,
    location: str | None = LOCATION_OPTION,
    apply: bool = APPLY_OPTION,
    out: str | None = OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Create or update a tag, or bulk-import with -f.

    Live --apply only creates; updating an existing tag live is refused
    (CONFIG) — use offline --apply for updates.
    """
    rt: Runtime = ctx.obj
    if file is not None:
        _import_file(rt, file, ObjectKind.TAG, apply=apply, out=out, out_format=output_format)
        return
    _require(name=name)
    assert name is not None
    cs = crud.plan_tag(
        rt.snapshot(),
        name,
        color=color,
        comments=comments,
        location=_resolve_location(rt, location),
    )
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)
