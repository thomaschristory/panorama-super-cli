"""Pure state types for the workbench. No Textual, no engine logic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from psc.core.changeset import ChangeSet


class OutputMode(str, Enum):
    """How a staged batch is finally applied."""

    SET = "set"  # render combined PAN-OS set script, push nothing
    OFFLINE_APPLY = "offline-apply"  # write the compounded config to a file
    LIVE_APPLY = "live-apply"  # replay changesets to the live candidate


@dataclass(frozen=True)
class SelectionItem:
    """One object reference held in the selection buffer (heterogeneous)."""

    kind: str  # "address" | "address-group" | "service" | "service-group" | "tag"
    name: str
    location: str  # location *name* ("shared" or a device-group name)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.name, self.location)


@dataclass
class StagedChange:
    """One entry in the git-like staged changelist."""

    label: str
    changeset: ChangeSet


@dataclass
class ApplyOutcome:
    """Result of applying the staged batch."""

    mode: OutputMode
    ops: int
    out_path: str | None
    detail: str
