"""Compare two snapshots and report what was added, removed, or changed.

Two comparison modes share one engine:

- **File vs file** — drift between two exported configs. Identity is
  ``(name, location)``: an object is *the same* only when both its name and its
  location match across the two snapshots. A same-named object that moved
  between ``shared`` and a device-group is therefore reported as removed-here /
  added-there, not "changed", because in PAN-OS those are genuinely two
  different objects (one shadows the other).

- **Device-group vs device-group** (``scope_base`` / ``scope_other`` set) —
  drift between the *effective visible object sets* of two device-groups in one
  loaded config. "Effective" means what a rule inside that device-group would
  actually see: the device-group's own objects plus everything inherited from
  its ancestors and ``shared``, with a nearer definition shadowing an inherited
  same-named one (exactly ``Snapshot.ancestors`` resolution). Identity here is
  the bare ``name`` within each scope — the intent is "what does device-group A
  expose that B doesn't, and vice-versa", so a name present in both scopes with
  a differing definition is "changed" even though the two definitions live at
  different locations.

The engine is pure model→model: it never parses XML or touches IO. The CLI
loads the snapshot(s) and formats the returned :class:`SnapshotDiff`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, Field

from psc.core.models import (
    Address,
    AddressGroup,
    Location,
    NatRule,
    SecurityRule,
    Service,
    ServiceGroup,
    Snapshot,
    Tag,
)

# The seven diffable object kinds. Used as a PEP 695 *constraint set* on the
# generic engine so pydantic resolves `KindDiff`'s member lists to a concrete
# union (a plain Protocol bound has no pydantic-core schema), and as the union
# type where a non-generic signature is clearer.
type _Diffable = Address | AddressGroup | Service | ServiceGroup | Tag | SecurityRule | NatRule


class ChangedItem(BaseModel):
    """One object present in both snapshots whose *definition* differs.

    `before`/`after` carry the object's own fields (name/location stripped) so
    the CLI can show exactly which attributes moved. Only the fields that
    actually differ are guaranteed to be present in both maps, but the full
    field set is kept for context.
    """

    name: str
    location: str
    before: dict[str, Any]
    after: dict[str, Any]

    @property
    def changed_fields(self) -> list[str]:
        keys = set(self.before) | set(self.after)
        return sorted(k for k in keys if self.before.get(k) != self.after.get(k))


class KindDiff[T: (Address, AddressGroup, Service, ServiceGroup, Tag, SecurityRule, NatRule)](
    BaseModel
):
    """Per-object-kind result: what was added, removed, and changed."""

    added: list[T] = Field(default_factory=list)
    removed: list[T] = Field(default_factory=list)
    changed: list[ChangedItem] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


class SnapshotDiff(BaseModel):
    """The full structured comparison of two snapshots, one `KindDiff` per kind."""

    addresses: KindDiff[Address] = Field(default_factory=KindDiff)
    address_groups: KindDiff[AddressGroup] = Field(default_factory=KindDiff)
    services: KindDiff[Service] = Field(default_factory=KindDiff)
    service_groups: KindDiff[ServiceGroup] = Field(default_factory=KindDiff)
    tags: KindDiff[Tag] = Field(default_factory=KindDiff)
    security_rules: KindDiff[SecurityRule] = Field(default_factory=KindDiff)
    nat_rules: KindDiff[NatRule] = Field(default_factory=KindDiff)

    @property
    def is_empty(self) -> bool:
        return all(
            kd.is_empty
            for kd in (
                self.addresses,
                self.address_groups,
                self.services,
                self.service_groups,
                self.tags,
                self.security_rules,
                self.nat_rules,
            )
        )


def _definition(obj: _Diffable) -> dict[str, Any]:
    """The object's comparable definition — every field except identity.

    `name` and `location` are excluded: they are the *identity*, not part of
    what "changed". Dropping `location` is what lets DG-vs-DG report a same-name
    object with differing value as "changed" even though the two live at
    different device-groups.
    """
    data = obj.model_dump(mode="json")
    data.pop("name", None)
    data.pop("location", None)
    # PAN-OS membership and rule-field lists are unordered *sets* — a re-export
    # that merely reorders `<member>` elements must not read as "changed". Sort
    # every string-list field so only genuine membership changes are reported.
    for field, value in data.items():
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            data[field] = sorted(value)
    return data


def _rulebase_of(obj: _Diffable) -> str:
    """The rulebase discriminator for a rule (`pre`/`post`), else `""`.

    Security/NAT rules are only unique *within* a rulebase — the same name can
    exist in both pre and post — so identity must include it or same-named
    pre/post rules collapse and one is silently dropped."""
    rb = getattr(obj, "rulebase", None)
    return rb.value if rb is not None else ""


# Identity within a snapshot's flat object list: (name, location-name, rulebase).
_KindKey = tuple[str, str, str]


def _diff_kind[T: (Address, AddressGroup, Service, ServiceGroup, Tag, SecurityRule, NatRule)](
    base_items: Sequence[T],
    other_items: Sequence[T],
    key: Callable[[T], _KindKey],
) -> KindDiff[T]:
    """Compute added/removed/changed for one object kind.

    `key` maps an object to its identity. Objects only in `other` are *added*,
    only in `base` are *removed*, in both with a differing definition are
    *changed*. Output lists are sorted by identity for stable, testable output.
    """
    base_by = {key(o): o for o in base_items}
    other_by = {key(o): o for o in other_items}

    added = [other_by[k] for k in other_by.keys() - base_by.keys()]
    removed = [base_by[k] for k in base_by.keys() - other_by.keys()]
    changed: list[ChangedItem] = []
    for k in base_by.keys() & other_by.keys():
        b, o = base_by[k], other_by[k]
        before, after = _definition(b), _definition(o)
        if before != after:
            # Report the *other* (post-change) location so DG-vs-DG names the
            # scope whose definition now wins; in file mode both locations are
            # identical so the choice is immaterial.
            changed.append(
                ChangedItem(name=o.name, location=o.location.name, before=before, after=after)
            )

    added.sort(key=key)
    removed.sort(key=key)
    changed.sort(key=lambda c: (c.name, c.location))
    return KindDiff(added=added, removed=removed, changed=changed)


def _visible_effective[
    T: (Address, AddressGroup, Service, ServiceGroup, Tag, SecurityRule, NatRule)
](items: Sequence[T], snapshot: Snapshot, scope: Location) -> list[T]:
    """The objects `scope` effectively sees, with shadowing resolved by name.

    Walk the ancestor chain closest-first (the device-group, its parents, then
    ``shared``); the first definition of each name wins, so a nearer object
    shadows an inherited same-named one — exactly PAN-OS resolution. The
    returned objects keep their real (defining) location.
    """
    chain = snapshot.ancestors(scope)  # closest first, shared last
    rank = {loc.name: i for i, loc in enumerate(chain)}
    winner: dict[tuple[str, str], T] = {}
    for obj in items:
        loc_name = obj.location.name
        if loc_name not in rank:
            continue  # not visible from this scope
        ident = (obj.name, _rulebase_of(obj))  # keep pre/post rules distinct
        prev = winner.get(ident)
        if prev is None or rank[loc_name] < rank[prev.location.name]:
            winner[ident] = obj
    return list(winner.values())


def diff_snapshots(
    base: Snapshot,
    other: Snapshot,
    *,
    scope_base: Location | None = None,
    scope_other: Location | None = None,
) -> SnapshotDiff:
    """Diff two snapshots per object kind (added / removed / changed).

    File mode (no scopes): compares every object by ``(name, location)`` across
    the two snapshots. DG mode (both ``scope_base`` and ``scope_other`` set):
    restricts each side to that scope's *effective visible object set* and
    compares by bare ``name`` (see the module docstring for the semantics).

    It's both scopes or neither — a one-sided scope is rejected, since scoping
    only one side while comparing the other's flat list would collapse
    cross-location same-names by key and silently drop objects. The result is
    deterministic — every list is sorted by identity.
    """
    if (scope_base is None) != (scope_other is None):
        raise ValueError(
            "diff_snapshots requires both scopes (device-group mode) or neither (file mode)"
        )

    # In DG mode identity is the bare name (each side is scoped and shadow-
    # resolved to one object per name); in file mode it is (name, location) so
    # cross-location same-names stay distinct. Rulebase always joins the key so
    # same-named pre/post rules never collapse. Fixed once for every kind.
    dg_mode = scope_base is not None

    def key(o: _Diffable) -> _KindKey:
        loc = "" if dg_mode else o.location.name
        return (o.name, loc, _rulebase_of(o))

    def sel[T: (Address, AddressGroup, Service, ServiceGroup, Tag, SecurityRule, NatRule)](
        base_items: Sequence[T],
        other_items: Sequence[T],
    ) -> KindDiff[T]:
        b = (
            _visible_effective(base_items, base, scope_base)
            if scope_base is not None
            else base_items
        )
        o = (
            _visible_effective(other_items, other, scope_other)
            if scope_other is not None
            else other_items
        )
        return _diff_kind(b, o, key)

    return SnapshotDiff(
        addresses=sel(base.addresses, other.addresses),
        address_groups=sel(base.address_groups, other.address_groups),
        services=sel(base.services, other.services),
        service_groups=sel(base.service_groups, other.service_groups),
        tags=sel(base.tags, other.tags),
        security_rules=sel(base.security_rules, other.security_rules),
        nat_rules=sel(base.nat_rules, other.nat_rules),
    )
