"""`psc export <kind>` — dump objects of one kind as NDJSON.

The read-side counterpart to `set … -f`: one JSON object per line, each the
domain model's canonical `model_dump(mode="json")`, deterministically ordered
by `(location, name)`. Output goes to stdout by default or to `--out` (a plain
artifact write, never a mutation). Scope honours the global `--device-group`
like every other read command.

This is a thin wrapper: all serialization lives in `psc.core.portability`.
"""

from __future__ import annotations

import typer

from psc.cli.runtime import Runtime
from psc.core import portability
from psc.core.changeset import ObjectKind
from psc.output.errors import ErrorType, PscError

# The plural CLI kind names, mirroring the `set` singular subcommands.
_KINDS: dict[str, ObjectKind] = {
    "addresses": ObjectKind.ADDRESS,
    "address-groups": ObjectKind.ADDRESS_GROUP,
    "services": ObjectKind.SERVICE,
    "service-groups": ObjectKind.SERVICE_GROUP,
    "tags": ObjectKind.TAG,
}


def _resolve_kind(kind: str) -> ObjectKind:
    resolved = _KINDS.get(kind)
    if resolved is None:
        allowed = " | ".join(_KINDS)
        raise PscError(f"unknown kind '{kind}' (use: {allowed})", ErrorType.VALIDATION)
    return resolved


def export(
    ctx: typer.Context,
    kind: str = typer.Argument(
        ..., help="Object kind: addresses | address-groups | services | service-groups | tags."
    ),
    out: str | None = typer.Option(
        None, "--out", help="Write the NDJSON to this file instead of stdout."
    ),
) -> None:
    """Export objects of KIND as NDJSON (one JSON object per line).

    Ordered by (location, name) for stable, diff-friendly output. Feed the
    result straight into `psc set <kind> -f <file>` on another config.
    """
    rt: Runtime = ctx.obj
    lines = portability.export_ndjson(rt.snapshot(), _resolve_kind(kind), scope=rt.scope())
    text = "\n".join(lines)
    if out is not None:
        try:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(text + ("\n" if text else ""))
        except OSError as exc:
            raise PscError(f"cannot write {out}: {exc}", ErrorType.INPUT) from exc
        rt.stderr.print(f"[green]wrote[/green] {len(lines)} object(s) → {out}")
    else:
        rt.stdout.print(text, markup=False, highlight=False, soft_wrap=True)
