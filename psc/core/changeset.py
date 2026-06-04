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

from psc.core.rulebases import FLAT_RULE_FIELDS, rule_container


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


class RuleDelete(BaseModel):
    """Delete an entire rule that a teardown left non-functional.

    Emitted (only) by `decommission` when scrubbing an address from a rule's
    source/destination empties that field: a rule with no real source *or* no
    real destination can never match traffic, so it is removed rather than left
    as a dead entry. `referrer_kind` is the `*-rule` tag (`security-rule`,
    `nat-rule`, `pbf-rule`, …); `rulebase` is `pre`/`post`. Like `ObjectDelete`
    this is a teardown, so the unmappable-reference gate treats it accordingly.
    """

    referrer_kind: str
    name: str
    location: str
    rulebase: str

    @property
    def summary(self) -> str:
        return f"delete {self.referrer_kind} rule '{self.name}' @{self.location} {self.rulebase}"


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
    rule_deletes: list[RuleDelete] = Field(default_factory=list)
    renames: list[ObjectRename] = Field(default_factory=list)
    deletes: list[ObjectDelete] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)

    @property
    def op_count(self) -> int:
        return (
            len(self.upserts)
            + len(self.reference_edits)
            + len(self.rule_deletes)
            + len(self.renames)
            + len(self.deletes)
        )

    @property
    def is_empty(self) -> bool:
        return self.op_count == 0

    def summaries(self) -> list[str]:
        # Order mirrors safe execution: scrub member fields (reference_edits)
        # *before* deleting whole rules they emptied, and delete rules before
        # the objects they referenced are themselves removed.
        out: list[str] = []
        out += [u.summary for u in self.upserts]
        out += [e.summary for e in self.reference_edits]
        out += [rd.summary for rd in self.rule_deletes]
        out += [r.summary for r in self.renames]
        out += [d.summary for d in self.deletes]
        return out


def reference_edit_is_mappable(edit: ReferenceEdit) -> bool:
    """Whether both appliers can express `edit` as a flat member-field rewrite.

    The single source of truth behind `apply_xml._referrer_field_element` and
    `apply_live._referrer_field_xpath`: a member field they know how to address
    (a group's `static`/`members`, a security-rule field, a NAT src/dst list, or
    any other rulebase's source/destination/service/tag — see
    `psc.core.rulebases`). A NAT *translation* field and a PBF `nexthop` are
    nested with no flat list, and a rule edit with no `rulebase` can't be
    located — all unmappable, and an applier silently skips them. The planner
    uses this to refuse such a skip when the same plan tears the target down,
    instead of leaving a dangling reference (#28).
    """
    kind = edit.referrer_kind
    if kind in ("address-group", "service-group"):
        return True
    container = rule_container(kind)
    if container is None or edit.rulebase is None:
        return False
    if container == "nat":
        # NAT keeps only flat match fields; its translation fields are nested.
        return edit.field in ("source", "destination")
    # security + every policy rulebase: the shared flat member fields.
    return edit.field in FLAT_RULE_FIELDS


def gate_unmappable_reference_edits(cs: ChangeSet) -> None:
    """Promote unsafe unmappable reference edits to blockers, in place.

    An unmappable edit (see `reference_edit_is_mappable`) is silently skipped at
    apply time. If the plan also deletes or renames an object, that skip drops
    the repoint that kept the teardown safe — a dangling reference, the exact
    failure repoint-before-delete exists to prevent. Make it a hard blocker. With
    no teardown the edit is advisory only, so it stays a warning.

    Clearing the ops is the planner's job (a blocked plan carries zero ops); this
    only classifies.
    """
    unmappable = [e for e in cs.reference_edits if not reference_edit_is_mappable(e)]
    if not unmappable:
        return
    torn_down = (
        [d.name for d in cs.deletes]
        + [r.old_name for r in cs.renames]
        + [rd.name for rd in cs.rule_deletes]
    )
    for e in unmappable:
        referrer = f"{e.referrer_kind} '{e.referrer_name}'@{e.referrer_location} {e.field}"
        if torn_down:
            targets = ", ".join(f"'{t}'" for t in torn_down)
            cs.blockers.append(
                f"cannot repoint {referrer} automatically (no flat member list to rewrite); "
                f"removing/renaming {targets} would leave a dangling reference — edit that "
                "field manually, then re-run"
            )
        else:
            cs.warnings.append(
                f"{referrer} can't be repointed automatically (no flat member list); "
                "review and edit it by hand"
            )
