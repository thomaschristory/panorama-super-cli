"""`psc skill` — install the bundled portable Skill into an agent harness.

The Skill (`skills/panorama-super-cli/SKILL.md`) ships inside the wheel, but an
agent harness only loads it from a well-known user-scoped directory. `install`
copies it there for a chosen harness; `export` copies it to an arbitrary
directory. Both are **dry-run by default** — they print the plan and exit 0
without writing unless `--apply` is passed, matching psc's safety model.
"""

from __future__ import annotations

import os
import shutil
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from psc.cli.runtime import Runtime
from psc.output.format import render
from psc.skill import BUNDLE_NAME, bundle_path

app = typer.Typer(no_args_is_help=True, help="Install the bundled Skill into an agent harness.")


class _Target(StrEnum):
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    GEMINI = "gemini"
    COPILOT = "copilot"


def _home() -> Path:
    return Path(os.environ.get("HOME") or os.path.expanduser("~"))


# Each harness loads user-scoped skills from a documented directory. Codex uses
# the agent-neutral `~/.agents/` prefix (also honoured by Gemini/Copilot as an
# interop alias, but each has its own native dir which we prefer).
_TARGET_SUBDIR: dict[_Target, str] = {
    _Target.CLAUDE_CODE: ".claude",
    _Target.CODEX: ".agents",
    _Target.GEMINI: ".gemini",
    _Target.COPILOT: ".copilot",
}


def _resolve(target: _Target) -> Path:
    return _home() / _TARGET_SUBDIR[target] / "skills" / BUNDLE_NAME / "SKILL.md"


def _emit(rt: Runtime, row: dict[str, object], title: str, human: str) -> None:
    render(rt.stdout, rt.output, model=row, rows=[row], table_title=title)
    rt.stderr.print(human)


@app.command("install")
def install(
    ctx: typer.Context,
    target: Annotated[
        _Target,
        typer.Option(
            "--target",
            "-t",
            help="Which agent harness to install the Skill into.",
            case_sensitive=False,
        ),
    ],
    apply_: Annotated[
        bool,
        typer.Option("--apply", help="Actually copy the file (default: dry-run)."),
    ] = False,
) -> None:
    """Copy the bundled Skill into a harness's user-scoped skills directory."""
    rt: Runtime = ctx.obj
    dest = _resolve(target)
    mode = "apply" if apply_ else "dry-run"
    written = False
    with bundle_path() as source:
        if apply_:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            written = True
        row: dict[str, object] = {
            "mode": mode,
            "target": target.value,
            "source": str(source.resolve()),
            "destination": str(dest),
            "written": written,
        }
        human = (
            f"[green]✓ installed[/green] {BUNDLE_NAME} skill at {dest}"
            if written
            else f"[yellow]dry-run[/yellow]: pass --apply to install → {dest}"
        )
        _emit(rt, row, "skill install", human)


@app.command("export")
def export(
    ctx: typer.Context,
    destination: Annotated[
        Path,
        typer.Argument(
            help=(
                "Directory to export the Skill into. Written to "
                "<destination>/panorama-super-cli/SKILL.md."
            ),
        ),
    ],
    apply_: Annotated[
        bool,
        typer.Option("--apply", help="Actually copy the file (default: dry-run)."),
    ] = False,
) -> None:
    """Copy the bundled Skill into an arbitrary directory."""
    rt: Runtime = ctx.obj
    dest = (destination.expanduser() / BUNDLE_NAME / "SKILL.md").resolve()
    mode = "apply" if apply_ else "dry-run"
    written = False
    with bundle_path() as source:
        if apply_:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            written = True
        row: dict[str, object] = {
            "mode": mode,
            "source": str(source.resolve()),
            "destination": str(dest),
            "written": written,
        }
        human = (
            f"[green]✓ exported[/green] {BUNDLE_NAME} skill to {dest}"
            if written
            else f"[yellow]dry-run[/yellow]: pass --apply to export → {dest}"
        )
        _emit(rt, row, "skill export", human)
