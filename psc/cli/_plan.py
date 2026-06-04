"""Shared dry-run/apply completion for every mutating command.

Centralising this is what guarantees the safety contract is identical across
`dedup merge`, `name rename`, and any future write command: blocked plans are
refused, dry-run is the default, and `--apply` is the only path to mutating the
managed config (the live candidate; offline, the rewritten `--out` file). A
bare `--out` only ever writes a reviewable artifact file — never the device.
"""

from __future__ import annotations

import typer

from psc.cli.runtime import Runtime
from psc.core.changeset import ChangeSet
from psc.core.setcmd import render_changeset
from psc.core.source import ConfigFormat
from psc.output.errors import ErrorType, PscError
from psc.output.format import OutputFormat, render

# Shared across every mutating command so the `--out` artifact toggle reads and
# behaves identically wherever `complete()` is used (dedup merge, name rename,
# name apply). Default `xml` keeps existing scripts byte-for-byte compatible.
OUT_FORMAT_OPTION = typer.Option(
    ConfigFormat.XML,
    "--output-format",
    "-of",
    help=(
        "Format of the offline --out artifact. 'xml' (default) rewrites the whole "
        "config to load with `load config`; 'set' writes the equivalent PAN-OS set "
        "script (the creates/deletes/repoints) — easier to read and to paste into a "
        "config session. Only affects the --out file."
    ),
)


def complete(
    rt: Runtime,
    cs: ChangeSet,
    *,
    apply: bool,
    out_path: str | None,
    out_format: ConfigFormat = ConfigFormat.XML,
) -> None:
    """Render `cs` in the chosen format, then apply it iff `apply`.

    A blocked plan raises `CONFLICT` (exit 6) before any write. The structured
    formats emit the whole plan (warnings + ops); table prints a readable
    summary; `set` prints the PAN-OS script.
    """
    if cs.is_blocked:
        raise PscError(
            "plan blocked (unsafe): " + "; ".join(cs.blockers),
            ErrorType.CONFLICT,
            details={"blockers": cs.blockers, "warnings": cs.warnings},
        )

    fmt = rt.output
    if fmt is OutputFormat.SET:
        rt.stdout.print(
            "\n".join(render_changeset(cs)), markup=False, highlight=False, soft_wrap=True
        )
    elif fmt in (OutputFormat.JSON, OutputFormat.JSONL, OutputFormat.YAML):
        render(rt.stdout, fmt, model=cs)
    else:
        _print_human_plan(rt, cs)

    if cs.is_empty:
        # An empty plan has no artifact to write — say so explicitly when `--out`
        # was asked for, rather than silently leaving the file absent.
        note = " — no artifact written" if out_path is not None else ""
        rt.stderr.print(f"[dim]nothing to do{note}[/dim]")
        return

    if apply:
        result = rt.source().apply(cs, out_path=out_path, out_format=out_format)
        rt.stderr.print(
            f"[green]applied[/green] {result.ops} operation(s)"
            + (f" → {result.out_path}" if result.out_path else "")
        )
    elif out_path is not None:
        # `--out` is an artifact request, not a mutation: writing a user-named
        # file never touches the source export or the live candidate, so honour
        # it even in a dry-run. This is the whole fix for #47.
        result = rt.source().write_out(cs, out_path=out_path, out_format=out_format)
        rt.stderr.print(
            f"[green]wrote[/green] {out_format.value} artifact → {result.out_path} "
            "[dim](dry-run — re-run with --apply to push)[/dim]"
        )
    else:
        rt.stderr.print("[yellow]dry-run[/yellow] — re-run with --apply to execute")


def _print_human_plan(rt: Runtime, cs: ChangeSet) -> None:
    rt.stdout.print(f"[bold]{cs.title}[/bold]")
    for w in cs.warnings:
        rt.stdout.print(f"  [yellow]! {w}[/yellow]")
    if cs.is_empty:
        return
    for line in cs.summaries():
        rt.stdout.print(f"  • {line}")
