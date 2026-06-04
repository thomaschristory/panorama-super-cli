"""Shared Typer Option constants and location helpers for the CLI layer.

These canonical Option objects exist so the mutating commands that genuinely
share a flag (`--apply`, `--out`, `--location`) declare it once. They are only
reused where the flags, defaults, AND help text are byte-identical — commands
that phrase their `--apply`/`--location`/`--out` help differently keep their own
local Option (sharing would silently change `--help`).

The two helpers collapse the shared-aware `Location` conversion duplicated
across command modules. They live here (in `cli/`, never `core/`) because the
"shared" sentinel string is a CLI-surface concern, not a domain-model one.
"""

from __future__ import annotations

import typer

from psc.core.models import Location

# Canonical mutating-command flags. Shared only where help/default are identical
# to these strings (currently `psc set` for all three; the `--out` help is also
# shared by dedup/name/rule, whose wording matches verbatim).
LOCATION_OPTION = typer.Option(
    None, "--location", help="Target location (default: global --device-group, else shared)."
)
APPLY_OPTION = typer.Option(False, "--apply", help="Execute the change (default: dry-run).")
OUT_OPTION = typer.Option(
    None,
    "--out",
    help="Write the plan artifact (set script or rewritten config) to this file; "
    "honoured even in a dry-run (see --output-format).",
)


def location_from_name(name: str) -> Location:
    """Convert a location *name* to a `Location`, honouring the "shared" sentinel."""
    return Location.shared() if name == "shared" else Location.dg(name)


def optional_location(name: str | None) -> Location | None:
    """Like `location_from_name`, but pass `None` through unchanged (no scope)."""
    return None if name is None else location_from_name(name)
