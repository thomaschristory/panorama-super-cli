"""`psc dedup` — find duplicate objects and merge them safely."""

from __future__ import annotations

import typer
from rich.markup import escape

from psc.cli._options import OUT_OPTION, optional_location
from psc.cli._plan import OUT_FORMAT_OPTION, complete
from psc.cli.runtime import Runtime
from psc.core.changeset import ObjectKind
from psc.core.dedup import (
    DuplicateGroup,
    ObjectRef,
    find_duplicate_addresses,
    find_duplicate_groups,
    find_duplicate_services,
    plan_merge,
    plan_merge_bucket,
    plan_merge_group,
    select_address_bucket,
)
from psc.core.promote import SkippedBucket, plan_promote, plan_promote_all, select_bucket
from psc.core.refs import ReferenceGraph
from psc.core.source import ConfigFormat
from psc.output.errors import ErrorType, PscError
from psc.output.format import render

app = typer.Typer(no_args_is_help=True)


def _dup_rows(groups: list[DuplicateGroup]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i, g in enumerate(groups, 1):
        for m in g.members:
            rows.append({"group": i, "value": g.value, "object": m.name, "location": m.location})
    return rows


@app.command("addresses")
def addresses(
    ctx: typer.Context,
    not_strict: bool = typer.Option(
        False,
        "--not-strict",
        help="Also group host objects onto their network (mask host bits). "
        "Default: only byte-identical values are duplicates.",
    ),
) -> None:
    """List address objects that share an identical value under different names.

    By default this is *strict*: a host written with a subnet mask
    (`10.1.1.50/24`) is not treated as a duplicate of the network `10.1.1.0/24`.
    Pass `--not-strict` for the looser, host-bit-masking behaviour.
    """
    rt: Runtime = ctx.obj
    groups = find_duplicate_addresses(rt.snapshot(), strict=not not_strict)
    if rt.strict and not groups:
        raise PscError("no duplicate addresses", ErrorType.NOT_FOUND)
    render(
        rt.stdout,
        rt.output,
        model=groups,
        rows=_dup_rows(groups),
        table_title="duplicate addresses",
        group_by="group",
    )


@app.command("services")
def services(ctx: typer.Context) -> None:
    """List service objects that share an identical protocol/port definition."""
    rt: Runtime = ctx.obj
    groups = find_duplicate_services(rt.snapshot())
    if rt.strict and not groups:
        raise PscError("no duplicate services", ErrorType.NOT_FOUND)
    render(
        rt.stdout,
        rt.output,
        model=groups,
        rows=_dup_rows(groups),
        table_title="duplicate services",
        group_by="group",
    )


@app.command("groups")
def groups(
    ctx: typer.Context,
    location: str | None = typer.Option(
        None,
        "--location",
        help="Only compare address-groups at this location (default: --device-group "
        "if set, else compare across all locations).",
    ),
) -> None:
    """Audit address-groups that resolve to the SAME effective member set.

    Groups are bucketed by the canonical set of leaf addresses they expand to
    (nested groups are flattened), so two groups with different names — or even
    different members that resolve to the same hosts — are flagged as redundant.
    Dynamic groups (runtime-only) and unresolvable groups (dangling/malformed
    members) are excluded and reported on stderr: the audit is not exhaustive.
    """
    rt: Runtime = ctx.obj
    snap = rt.snapshot()
    graph = ReferenceGraph.build(snap)
    loc = optional_location(location or rt.device_group)
    result = find_duplicate_groups(snap, graph, loc)
    if result.dynamic_skipped or result.unresolvable_skipped:
        rt.stderr.print(
            f"[yellow]note[/yellow] audit is not exhaustive: skipped "
            f"{len(result.dynamic_skipped)} dynamic and "
            f"{len(result.unresolvable_skipped)} unresolvable (dangling/malformed) "
            "group(s)"
        )
    if rt.strict and not result.buckets:
        raise PscError("no duplicate address-groups", ErrorType.NOT_FOUND)
    render(
        rt.stdout,
        rt.output,
        model=result.buckets,
        rows=_dup_rows(result.buckets),
        table_title="duplicate address-groups",
        group_by="group",
    )


@app.command("merge")
def merge(
    ctx: typer.Context,
    keep: str | None = typer.Option(
        None, "--keep", help="Survivor object name. Required with --remove; optional with --group."
    ),
    remove: str | None = typer.Option(
        None, "--remove", help="Object to collapse into --keep and delete (pairwise merge)."
    ),
    group: str | None = typer.Option(
        None,
        "--group",
        help="Collapse the WHOLE duplicate-address bucket with this value "
        "(e.g. '10.0.0.10/32') toward --keep in one plan. Run `dedup addresses` "
        "to list buckets.",
    ),
    location: str | None = typer.Option(
        None, "--location", help="Location of both objects (default: --device-group or shared)."
    ),
    keep_location: str | None = typer.Option(None, "--keep-location"),
    remove_location: str | None = typer.Option(None, "--remove-location"),
    not_strict: bool = typer.Option(
        False,
        "--not-strict",
        help="With --group, match the bucket under host-bit masking (see `dedup addresses`).",
    ),
    allow_value_change: bool = typer.Option(
        False, "--allow-value-change", help="Permit merging objects with different values."
    ),
    apply: bool = typer.Option(False, "--apply", help="Execute the merge (default: dry-run)."),
    out: str | None = OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Collapse address duplicates into one survivor, repointing every reference.

    Two modes: pairwise (`--keep X --remove Y`) collapses one object; group
    (`--group <value> [--keep X]`) collapses the ENTIRE duplicate bucket sharing
    that value into one survivor in a single plan. Dry-run by default (use
    `-o set` for the PAN-OS script). Repoints all groups/rules/NAT *before*
    deleting; refuses if any reference can't be safely repointed.
    """
    rt: Runtime = ctx.obj
    default_loc = location or rt.device_group or "shared"
    snap = rt.snapshot()
    graph = ReferenceGraph.build(snap)

    if group is not None:
        if remove is not None:
            raise PscError("--group and --remove are mutually exclusive", ErrorType.INPUT)
        bucket = select_address_bucket(snap, group, strict=not not_strict)
        members = [ObjectRef(name=m.name, location=m.location) for m in bucket.members]
        keep_ref = (
            ObjectRef(name=keep, location=keep_location or default_loc)
            if keep is not None
            else None
        )
        cs = plan_merge_bucket(
            snap,
            graph,
            members=members,
            keep=keep_ref,
            allow_value_change=allow_value_change,
        )
        complete(rt, cs, apply=apply, out_path=out, out_format=output_format)
        return

    if keep is None or remove is None:
        raise PscError("pairwise merge needs both --keep and --remove", ErrorType.INPUT)
    cs = plan_merge(
        snap,
        graph,
        keep=ObjectRef(name=keep, location=keep_location or default_loc),
        drop=ObjectRef(name=remove, location=remove_location or default_loc),
        allow_value_change=allow_value_change,
    )
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)


@app.command("merge-group")
def merge_group(
    ctx: typer.Context,
    keep: str = typer.Option(..., "--keep", help="Survivor address-group name."),
    remove: str = typer.Option(
        ..., "--remove", help="Address-group to collapse into --keep and delete."
    ),
    location: str | None = typer.Option(
        None, "--location", help="Location of both groups (default: --device-group or shared)."
    ),
    keep_location: str | None = typer.Option(None, "--keep-location"),
    remove_location: str | None = typer.Option(None, "--remove-location"),
    apply: bool = typer.Option(False, "--apply", help="Execute the merge (default: dry-run)."),
    out: str | None = OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Collapse one address-group into an equivalent one, repointing references.

    Dry-run by default. Refuses unless the two groups expand to the *same*
    effective set of leaf addresses — there is no value-change override, because
    merging groups that mean different things would silently change rule
    matching. Repoints every referrer *before* deleting the dropped group.
    """
    rt: Runtime = ctx.obj
    default_loc = location or rt.device_group or "shared"
    snap = rt.snapshot()
    graph = ReferenceGraph.build(snap)
    cs = plan_merge_group(
        snap,
        graph,
        keep=ObjectRef(name=keep, location=keep_location or default_loc),
        drop=ObjectRef(name=remove, location=remove_location or default_loc),
    )
    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)


def _report_skipped(rt: Runtime, skipped: list[SkippedBucket]) -> None:
    """Surface every bucket `--all` did not promote. Never let a sweep look total."""
    if not skipped:
        return
    rt.stderr.print(f"[yellow]note[/yellow] skipped {len(skipped)} bucket(s):")
    for s in skipped:
        rt.stderr.print(f"  [yellow]-[/yellow] {escape(s.value)}: {escape(s.reason)}")


@app.command("promote")
def promote(
    ctx: typer.Context,
    kind: ObjectKind = typer.Argument(..., help="Object kind: address | service."),
    group: str | None = typer.Option(
        None,
        "--group",
        help="Promote the duplicate bucket with this value (e.g. '10.0.0.10/32'). "
        "Run `dedup addresses` / `dedup services` to list buckets.",
    ),
    all_buckets: bool = typer.Option(
        False,
        "--all",
        help="Promote EVERY promotable bucket of this kind in one plan. Buckets that "
        "cannot be promoted are skipped and reported on stderr, never silently dropped.",
    ),
    to: str = typer.Option(
        "shared",
        "--to",
        help="Destination: 'shared' (default), or a device-group that is a common "
        "ancestor of every bucket member.",
    ),
    keep: str | None = typer.Option(
        None,
        "--keep",
        help="Survivor NAME when the bucket's copies are named differently. Must be one "
        "of the bucket's own names; every other copy's references are repointed onto it. "
        "Without this, a divergently-named bucket is a blocker.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Execute the promotion (default: dry-run)."),
    out: str | None = OUT_OPTION,
    output_format: ConfigFormat = OUT_FORMAT_OPTION,
) -> None:
    """Promote a cross-device-group duplicate into shared (or a common ancestor).

    This is the merge `dedup merge` cannot do: when the same object is defined in
    several device-groups and NOWHERE above them, there is no existing survivor to
    collapse onto. `promote` creates it once at the destination and deletes every
    device-group copy — references fall through by PAN-OS shadowing, so nothing is
    repointed. Dry-run by default (use `-o set` for the PAN-OS script).

    When the copies are named differently (`h-web1@DG-A` vs `web-primary@DG-B`),
    pass `--keep h-web1`: the survivor is created under that name and every
    reference to the other copies is repointed onto it before they are deleted.
    """
    rt: Runtime = ctx.obj
    snap = rt.snapshot()
    graph = ReferenceGraph.build(snap)

    if all_buckets == (group is not None):
        raise PscError("promote needs exactly one of --group or --all", ErrorType.INPUT)
    if all_buckets and keep is not None:
        raise PscError(
            "--all and --keep are mutually exclusive (one survivor name cannot span "
            "many buckets); promote the divergent bucket on its own with --group",
            ErrorType.INPUT,
        )

    if all_buckets:
        cs, skipped = plan_promote_all(snap, graph, kind=kind, dest_name=to)
        _report_skipped(rt, skipped)
    else:
        assert group is not None  # guarded by the exactly-one check above
        bucket = select_bucket(snap, graph, kind=kind, value=group)
        cs = plan_promote(
            snap, graph, kind=kind, members=list(bucket.members), dest_name=to, keep_name=keep
        )

    complete(rt, cs, apply=apply, out_path=out, out_format=output_format)
