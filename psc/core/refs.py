"""The reference graph: who points at whom.

Every safe edit hinges on this. Before merging or renaming an object you must
know *every* place it is referenced — across `shared` and every device-group,
in address-groups, service-groups, security rules, and NAT rules (match *and*
translation fields). This module builds that graph once and answers:

- `where_used(...)` — every reference that resolves to a given object.
- `unused(...)` — objects no rule reaches, directly or through groups.
- `dangling()` — references to names that don't resolve to any object.

PAN-OS name resolution is modelled faithfully: a reference inside a
device-group resolves to a same-named object in that device-group if one
exists (a local *shadow*), otherwise to the `shared` object, otherwise it
dangles. This shadowing is exactly why renames are dangerous, so it lives
here, in one place, rather than being re-derived per feature.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from psc.core.models import Location, Rulebase, Snapshot

# Built-in names that are not user objects; references to them never dangle.
PREDEFINED = frozenset(
    {
        "any",
        "application-default",
        "service-http",
        "service-https",
        "service-dns",
    }
)

# Object kinds, grouped by the two PAN-OS namespaces plus tags.
ADDRESS_KINDS = ("address", "address-group")
SERVICE_KINDS = ("service", "service-group")


@dataclass(frozen=True)
class Target:
    """A resolved reference target: a concrete object's identity."""

    kind: str
    name: str
    location: Location


@dataclass(frozen=True)
class Reference:
    """One edge: a referrer field that names an object."""

    target_name: str
    namespace: str  # "address" | "service" | "tag"
    referrer_kind: str  # "address-group" | "service-group" | "security-rule" | "nat-rule"
    referrer_name: str
    referrer_location: Location
    field: str
    rulebase: Rulebase | None = None
    resolved: Target | None = None  # None => dangling or predefined

    @property
    def is_resolved(self) -> bool:
        return self.resolved is not None


class _NamespaceIndex:
    """Per-namespace name resolver honouring DG-shadows-shared."""

    def __init__(self) -> None:
        self._shared: dict[str, str] = {}  # name -> kind
        self._dg: dict[str, dict[str, str]] = defaultdict(dict)  # dg -> {name: kind}

    def add(self, name: str, kind: str, location: Location) -> None:
        if location.is_shared:
            self._shared[name] = kind
        else:
            self._dg[location.name][name] = kind

    def resolve(self, name: str, ref_location: Location) -> Target | None:
        if not ref_location.is_shared:
            local = self._dg.get(ref_location.name, {})
            if name in local:
                return Target(local[name], name, ref_location)
        if name in self._shared:
            return Target(self._shared[name], name, Location.shared())
        return None


@dataclass
class ReferenceGraph:
    snapshot: Snapshot
    references: list[Reference] = field(default_factory=list)
    _addr_idx: _NamespaceIndex = field(default_factory=_NamespaceIndex)
    _svc_idx: _NamespaceIndex = field(default_factory=_NamespaceIndex)
    _tag_idx: _NamespaceIndex = field(default_factory=_NamespaceIndex)
    _by_target: dict[Target, list[Reference]] = field(default_factory=lambda: defaultdict(list))

    @classmethod
    def build(cls, snapshot: Snapshot) -> ReferenceGraph:
        g = cls(snapshot=snapshot)
        g._index()
        g._walk()
        return g

    # -- indexing --------------------------------------------------------

    def _index(self) -> None:
        for a in self.snapshot.addresses:
            self._addr_idx.add(a.name, "address", a.location)
        for ag in self.snapshot.address_groups:
            self._addr_idx.add(ag.name, "address-group", ag.location)
        for s in self.snapshot.services:
            self._svc_idx.add(s.name, "service", s.location)
        for sg in self.snapshot.service_groups:
            self._svc_idx.add(sg.name, "service-group", sg.location)
        for t in self.snapshot.tags:
            self._tag_idx.add(t.name, "tag", t.location)

    def _idx_for(self, namespace: str) -> _NamespaceIndex:
        return {"address": self._addr_idx, "service": self._svc_idx, "tag": self._tag_idx}[
            namespace
        ]

    def _emit(
        self,
        *,
        target_name: str,
        namespace: str,
        referrer_kind: str,
        referrer_name: str,
        referrer_location: Location,
        field_name: str,
        rulebase: Rulebase | None = None,
    ) -> None:
        if target_name in PREDEFINED:
            return
        resolved = self._idx_for(namespace).resolve(target_name, referrer_location)
        ref = Reference(
            target_name=target_name,
            namespace=namespace,
            referrer_kind=referrer_kind,
            referrer_name=referrer_name,
            referrer_location=referrer_location,
            field=field_name,
            rulebase=rulebase,
            resolved=resolved,
        )
        self.references.append(ref)
        if resolved is not None:
            self._by_target[resolved].append(ref)

    def _walk(self) -> None:  # noqa: PLR0912 — one branch per object/field kind
        snap = self.snapshot
        for ag in snap.address_groups:
            for m in ag.static_members or []:
                self._emit(
                    target_name=m,
                    namespace="address",
                    referrer_kind="address-group",
                    referrer_name=ag.name,
                    referrer_location=ag.location,
                    field_name="static",
                )
            for t in ag.tags:
                self._emit(
                    target_name=t,
                    namespace="tag",
                    referrer_kind="address-group",
                    referrer_name=ag.name,
                    referrer_location=ag.location,
                    field_name="tag",
                )
        for sg in snap.service_groups:
            for m in sg.members:
                self._emit(
                    target_name=m,
                    namespace="service",
                    referrer_kind="service-group",
                    referrer_name=sg.name,
                    referrer_location=sg.location,
                    field_name="members",
                )
        for a in snap.addresses:
            for t in a.tags:
                self._emit(
                    target_name=t,
                    namespace="tag",
                    referrer_kind="address",
                    referrer_name=a.name,
                    referrer_location=a.location,
                    field_name="tag",
                )
        for s in snap.services:
            for t in s.tags:
                self._emit(
                    target_name=t,
                    namespace="tag",
                    referrer_kind="service",
                    referrer_name=s.name,
                    referrer_location=s.location,
                    field_name="tag",
                )
        for r in snap.security_rules:
            for fname, members in (("source", r.source), ("destination", r.destination)):
                for m in members:
                    self._emit(
                        target_name=m,
                        namespace="address",
                        referrer_kind="security-rule",
                        referrer_name=r.name,
                        referrer_location=r.location,
                        field_name=fname,
                        rulebase=r.rulebase,
                    )
            for m in r.service:
                self._emit(
                    target_name=m,
                    namespace="service",
                    referrer_kind="security-rule",
                    referrer_name=r.name,
                    referrer_location=r.location,
                    field_name="service",
                    rulebase=r.rulebase,
                )
            for t in r.tags:
                self._emit(
                    target_name=t,
                    namespace="tag",
                    referrer_kind="security-rule",
                    referrer_name=r.name,
                    referrer_location=r.location,
                    field_name="tag",
                    rulebase=r.rulebase,
                )
        for n in snap.nat_rules:
            for fname, members in (
                ("source", n.source),
                ("destination", n.destination),
                ("source-translation", n.source_translation),
            ):
                for m in members:
                    self._emit(
                        target_name=m,
                        namespace="address",
                        referrer_kind="nat-rule",
                        referrer_name=n.name,
                        referrer_location=n.location,
                        field_name=fname,
                        rulebase=n.rulebase,
                    )
            if n.destination_translation:
                self._emit(
                    target_name=n.destination_translation,
                    namespace="address",
                    referrer_kind="nat-rule",
                    referrer_name=n.name,
                    referrer_location=n.location,
                    field_name="destination-translation",
                    rulebase=n.rulebase,
                )
            self._emit(
                target_name=n.service,
                namespace="service",
                referrer_kind="nat-rule",
                referrer_name=n.name,
                referrer_location=n.location,
                field_name="service",
                rulebase=n.rulebase,
            )

    # -- queries ---------------------------------------------------------

    def resolve(self, namespace: str, name: str, ref_location: Location) -> Target | None:
        """Resolve a bare name in a referrer's scope (DG shadow then shared)."""
        return self._idx_for(namespace).resolve(name, ref_location)

    def where_used(self, kind: str, name: str, location: Location) -> list[Reference]:
        return list(self._by_target.get(Target(kind, name, location), []))

    def dangling(self) -> list[Reference]:
        """References whose name resolves to no object (and isn't predefined)."""
        return [r for r in self.references if not r.is_resolved]

    def _rule_seeded_targets(self) -> set[Target]:
        seeds: set[Target] = set()
        for r in self.references:
            if r.referrer_kind in ("security-rule", "nat-rule") and r.resolved is not None:
                seeds.add(r.resolved)
        return seeds

    def _members_of(self, target: Target) -> list[Target]:
        """Resolved members of a group target (empty for leaf objects)."""
        out: list[Target] = []
        if target.kind == "address-group":
            for ag in self.snapshot.address_groups:
                if ag.name == target.name and ag.location == target.location:
                    for m in ag.static_members or []:
                        t = self._addr_idx.resolve(m, target.location)
                        if t is not None:
                            out.append(t)
        elif target.kind == "service-group":
            for sg in self.snapshot.service_groups:
                if sg.name == target.name and sg.location == target.location:
                    for m in sg.members:
                        t = self._svc_idx.resolve(m, target.location)
                        if t is not None:
                            out.append(t)
        return out

    def reachable_targets(self) -> set[Target]:
        """Every object a rule reaches, directly or transitively via groups."""
        seen = self._rule_seeded_targets()
        stack = list(seen)
        while stack:
            cur = stack.pop()
            for child in self._members_of(cur):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    def unused(self, kind: str) -> list[Target]:
        """Objects of `kind` that no rule reaches (recursively).

        `kind` is one of: address, address-group, service, service-group, tag.
        Tags are special-cased: a tag is "used" if any object or rule carries
        it, or a dynamic address-group filter mentions it.
        """
        if kind == "tag":
            return self._unused_tags()
        reachable = self.reachable_targets()
        defined = self._defined_targets(kind)
        return [t for t in defined if t not in reachable]

    def _defined_targets(self, kind: str) -> list[Target]:
        snap = self.snapshot
        if kind == "address":
            return [Target(kind, o.name, o.location) for o in snap.addresses]
        if kind == "address-group":
            return [Target(kind, o.name, o.location) for o in snap.address_groups]
        if kind == "service":
            return [Target(kind, o.name, o.location) for o in snap.services]
        if kind == "service-group":
            return [Target(kind, o.name, o.location) for o in snap.service_groups]
        return []

    def _unused_tags(self) -> list[Target]:
        used: set[tuple[str, str]] = set()  # (location_name, tag_name) of the *referrer* context
        for r in self.references:
            if r.namespace == "tag" and r.resolved is not None:
                used.add((r.resolved.location.name, r.resolved.name))
        # Dynamic address-group filters reference tags by bare name.
        for ag in self.snapshot.address_groups:
            if ag.dynamic_filter:
                for t in self.snapshot.tags:
                    if t.name in ag.dynamic_filter:
                        used.add((t.location.name, t.name))
        return [
            Target("tag", t.name, t.location)
            for t in self.snapshot.tags
            if (t.location.name, t.name) not in used
        ]
