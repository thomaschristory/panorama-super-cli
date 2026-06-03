"""The mutation plan: every write the tool can make, as inspectable data.

A `ChangeSet` is what `--apply` applies and what dry-run prints. It is a pure
value — engines build one, the renderer turns it into `set` commands or JSON,
and the executor walks it against a live `PanoramaSource`. Decoupling the plan
from its execution is what makes dry-run trustworthy: the exact same object is
shown and then applied.

`blockers` is the safety gate. A non-empty `blockers` list means the plan is
unsafe (e.g. a reference that can't be repointed); the executor refuses to run
it even with `--apply`.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ObjectKind(str, Enum):
    ADDRESS = "address"
    ADDRESS_GROUP = "address-group"
    SERVICE = "service"
    SERVICE_GROUP = "service-group"
    TAG = "tag"


class ReferenceEdit(BaseModel):
    """Rewrite the member list of one referrer field. `before`/`after` make the
    change auditable; the renderer uses `after` (PAN-OS `set` on a member field
    appends, so a member edit becomes delete-field + set-new-list).
    """

    referrer_kind: str
    referrer_name: str
    referrer_location: str
    field: str
    rulebase: str | None = None
    before: list[str] = Field(default_factory=list)
    after: list[str] = Field(default_factory=list)

    @property
    def summary(self) -> str:
        loc = self.referrer_location
        rb = f" {self.rulebase}" if self.rulebase else ""
        return (
            f"{self.referrer_kind} '{self.referrer_name}' @{loc}{rb} "
            f"{self.field}: {self.before} -> {self.after}"
        )


class ObjectDelete(BaseModel):
    kind: ObjectKind
    name: str
    location: str

    @property
    def summary(self) -> str:
        return f"delete {self.kind.value} '{self.name}' @{self.location}"


class ObjectRename(BaseModel):
    kind: ObjectKind
    location: str
    old_name: str
    new_name: str

    @property
    def summary(self) -> str:
        return f"rename {self.kind.value} '{self.old_name}' -> '{self.new_name}' @{self.location}"


class ObjectUpsert(BaseModel):
    """Create-or-update a single object. `fields` holds rendered leaf values
    keyed by their PAN-OS element (e.g. `ip-netmask`, `protocol/tcp/port`).
    """

    kind: ObjectKind
    name: str
    location: str
    fields: dict[str, str] = Field(default_factory=dict)
    members: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    exists: bool = False  # True => update, False => create

    @property
    def summary(self) -> str:
        verb = "update" if self.exists else "create"
        return f"{verb} {self.kind.value} '{self.name}' @{self.location}"


class ChangeSet(BaseModel):
    """An ordered, inspectable set of mutations.

    Ordering matters for safety: reference rewrites and upserts must precede
    deletes (never delete an object still referenced). Engines append in safe
    order; the executor preserves it.
    """

    title: str
    upserts: list[ObjectUpsert] = Field(default_factory=list)
    reference_edits: list[ReferenceEdit] = Field(default_factory=list)
    renames: list[ObjectRename] = Field(default_factory=list)
    deletes: list[ObjectDelete] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)

    @property
    def op_count(self) -> int:
        return len(self.upserts) + len(self.reference_edits) + len(self.renames) + len(self.deletes)

    @property
    def is_empty(self) -> bool:
        return self.op_count == 0

    def summaries(self) -> list[str]:
        out: list[str] = []
        out += [u.summary for u in self.upserts]
        out += [e.summary for e in self.reference_edits]
        out += [r.summary for r in self.renames]
        out += [d.summary for d in self.deletes]
        return out
