"""Shared dry-run/apply completion for every mutating command.

Centralising this is what guarantees the safety contract is identical across
`dedup merge`, `name rename`, and any future write command: blocked plans are
refused, dry-run is the default, and `--apply` is the only path to a write.
"""

from __future__ import annotations

from psc.cli.runtime import Runtime
from psc.core.changeset import ChangeSet
from psc.core.setcmd import render_changeset
from psc.output.errors import ErrorType, PscError
from psc.output.format import OutputFormat, render


def complete(rt: Runtime, cs: ChangeSet, *, apply: bool, out_path: str | None) -> None:
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
        rt.stderr.print("[dim]nothing to do[/dim]")
        return

    if apply:
        result = rt.source().apply(cs, out_path=out_path)
        rt.stderr.print(
            f"[green]applied[/green] {result.ops} operation(s)"
            + (f" → {result.out_path}" if result.out_path else "")
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
