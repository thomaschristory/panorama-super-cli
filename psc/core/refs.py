"""The reference graph: who points at whom.

Every safe edit hinges on this. Before merging or renaming an object you must
know *every* place it is referenced — across `shared` and every device-group,
in address-groups, service-groups, and every object-referencing rulebase:
security, NAT (match *and* translation fields), and PBF, decryption,
authentication, QoS, application-override, DoS, SD-WAN, tunnel-inspect, and
network-packet-broker (plus a PBF forwarding next-hop object). This module
builds that graph once and answers:

- `where_used(...)` — every reference that resolves to a given object.
- `unused(...)` — objects no rule reaches, directly or through groups.
- `dangling()` — references to names that don't resolve to any object.

PAN-OS name resolution is modelled faithfully: a reference inside a
device-group binds to its *closest* definition up the hierarchy — that
device-group, then each ancestor device-group, then `shared` (a nearer
definition *shadows* an inherited one); if nothing matches it dangles. This
shadowing is exactly why renames are dangerous, so it lives here, in one place
(`Snapshot.ancestors`), rather than being re-derived per feature.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from psc.core.dagfilter import FilterParseError, filter_tags, parse_filter
from psc.core.models import Location, Rulebase, Snapshot, _Named
from psc.core.rulebases import rule_container

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


def dag_filter_tags(filter_str: str) -> set[str]:
    """The tag names a dynamic address-group filter references.

    Thin alias for :func:`psc.core.dagfilter.filter_tags`, kept here as the
    historical import site for the unused-tag analysis, decommission's
    DAG-selection blocker, and relocate's dependency walk.
    """
    return filter_tags(filter_str)


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
    referrer_disabled: bool = False
    """True only for a rule referrer whose rule is disabled. Group/object
    referrers are always False. Lets `unused(..., ignore_disabled=True)` treat
    disabled rules as non-roots (#9)."""

    @property
    def is_resolved(self) -> bool:
        return self.resolved is not None


class _NamespaceIndex:
    """Per-namespace name resolver honouring the device-group chain.

    A name binds to its *closest* definition along `chain` (the referrer's
    device-group, then each ancestor, then `shared`) — exactly PAN-OS shadowing.
    """

    def __init__(self) -> None:
        self._by_loc: dict[str, dict[str, str]] = defaultdict(dict)  # loc name -> {name: kind}

    def add(self, name: str, kind: str, location: Location) -> None:
        self._by_loc[location.name][name] = kind

    def defined_at(self, name: str, location: Location) -> bool:
        """True if `name` is defined *directly* at `location` (no inheritance)."""
        return name in self._by_loc.get(location.name, {})

    def resolve(self, name: str, chain: list[Location]) -> Target | None:
        for loc in chain:
            here = self._by_loc.get(loc.name)
            if here is not None and name in here:
                return Target(here[name], name, loc)
        return None


@dataclass
class ReferenceGraph:
    snapshot: Snapshot
    references: list[Reference] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    """Non-fatal coverage gaps found while building (e.g. an unparseable DAG
    filter whose membership could not be resolved). The CLI surfaces these on
    stderr so the operator knows which findings are unverified."""
    _addr_idx: _NamespaceIndex = field(default_factory=_NamespaceIndex)
    _svc_idx: _NamespaceIndex = field(default_factory=_NamespaceIndex)
    _tag_idx: _NamespaceIndex = field(default_factory=_NamespaceIndex)
    _by_target: dict[Target, list[Reference]] = field(default_factory=lambda: defaultdict(list))
    _dag_members: dict[Target, list[Target]] = field(default_factory=lambda: defaultdict(list))

    @classmethod
    def build(cls, snapshot: Snapshot) -> ReferenceGraph:
        g = cls(snapshot=snapshot)
        g._index()
        g._walk()
        g._resolve_dags()
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
        referrer_disabled: bool = False,
    ) -> None:
        if target_name in PREDEFINED:
            return
        resolved = self._idx_for(namespace).resolve(
            target_name, self.snapshot.ancestors(referrer_location)
        )
        ref = Reference(
            target_name=target_name,
            namespace=namespace,
            referrer_kind=referrer_kind,
            referrer_name=referrer_name,
            referrer_location=referrer_location,
            field=field_name,
            rulebase=rulebase,
            resolved=resolved,
            referrer_disabled=referrer_disabled,
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
                        referrer_disabled=r.disabled,
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
                    referrer_disabled=r.disabled,
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
                    referrer_disabled=r.disabled,
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
                        referrer_disabled=n.disabled,
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
                    referrer_disabled=n.disabled,
                )
            self._emit(
                target_name=n.service,
                namespace="service",
                referrer_kind="nat-rule",
                referrer_name=n.name,
                referrer_location=n.location,
                field_name="service",
                rulebase=n.rulebase,
                referrer_disabled=n.disabled,
            )
            for t in n.tags:
                self._emit(
                    target_name=t,
                    namespace="tag",
                    referrer_kind="nat-rule",
                    referrer_name=n.name,
                    referrer_location=n.location,
                    field_name="tag",
                    rulebase=n.rulebase,
                    referrer_disabled=n.disabled,
                )
        for p in snap.policy_rules:
            kind = p.referrer_kind
            for fname, members in (("source", p.source), ("destination", p.destination)):
                for m in members:
                    self._emit(
                        target_name=m,
                        namespace="address",
                        referrer_kind=kind,
                        referrer_name=p.name,
                        referrer_location=p.location,
                        field_name=fname,
                        rulebase=p.rulebase,
                        referrer_disabled=p.disabled,
                    )
            for m in p.service:
                self._emit(
                    target_name=m,
                    namespace="service",
                    referrer_kind=kind,
                    referrer_name=p.name,
                    referrer_location=p.location,
                    field_name="service",
                    rulebase=p.rulebase,
                    referrer_disabled=p.disabled,
                )
            for t in p.tags:
                self._emit(
                    target_name=t,
                    namespace="tag",
                    referrer_kind=kind,
                    referrer_name=p.name,
                    referrer_location=p.location,
                    field_name="tag",
                    rulebase=p.rulebase,
                    referrer_disabled=p.disabled,
                )
            if p.nexthop is not None:
                # A PBF forwarding next-hop that names an address object. Nested
                # (no flat member list), so it is review-gated on merge/rename
                # like a NAT translation field — see `reference_edit_is_mappable`.
                self._emit(
                    target_name=p.nexthop,
                    namespace="address",
                    referrer_kind=kind,
                    referrer_name=p.name,
                    referrer_location=p.location,
                    field_name="nexthop",
                    rulebase=p.rulebase,
                    referrer_disabled=p.disabled,
                )

    def _resolve_dags(self) -> None:
        """Resolve each dynamic address-group's membership from static tags.

        A DAG selects addresses by a tag expression, not a static member list, so
        it is invisible to `_members_of` unless we evaluate the filter. Here we
        match each DAG's filter against the *config* tags psc already parses and
        record the matched addresses, so an address used only via a
        rule-referenced DAG counts as reachable (and shows the DAG→address edge
        in where-used) instead of looking unused (#60).

        Scope: a DAG matches only addresses visible from its own location (the
        device-group, its ancestors, and shared) — the same chain `_members_of`
        uses for static members. An unparseable filter is recorded as a warning
        and contributes no members (match-nothing): psc never guesses membership,
        but the operator is told that DAG's coverage is unverified (#60 Q2).

        Caveat: this resolves only *config-tagged* membership. Addresses brought
        into a DAG by externally registered IPs (XML-API / User-ID / VM-info) are
        runtime state absent from the config and are still not covered — that is
        the residual gap a live membership query would close.
        """
        addrs_by_loc = self.snapshot.addresses_by_location()
        for ag in self.snapshot.address_groups:
            if not ag.is_dynamic or ag.dynamic_filter is None:
                continue
            dag = Target("address-group", ag.name, ag.location)
            try:
                flt = parse_filter(ag.dynamic_filter)
            except FilterParseError as exc:
                self.warnings.append(
                    f"dynamic address-group '{ag.name}'@{ag.location.name}: "
                    f"unparseable filter ({exc}); membership not resolved — "
                    "addresses matched only by it may be reported unused"
                )
                continue
            scope = {loc.name for loc in self.snapshot.ancestors(ag.location)}
            for loc_name in scope:
                for a in addrs_by_loc.get(loc_name, []):
                    if not flt.matches(set(a.tags)):
                        continue
                    member = Target("address", a.name, a.location)
                    self._dag_members[dag].append(member)
                    # Surface the DAG as an indirect referrer of the matched
                    # address (resolved straight to the concrete object, not by
                    # name — a shadowed same-name address must not steal it).
                    ref = Reference(
                        target_name=a.name,
                        namespace="address",
                        referrer_kind="address-group",
                        referrer_name=ag.name,
                        referrer_location=ag.location,
                        field="dynamic",
                        resolved=member,
                    )
                    self.references.append(ref)
                    self._by_target[member].append(ref)

    # -- queries ---------------------------------------------------------

    def resolve(self, namespace: str, name: str, ref_location: Location) -> Target | None:
        """Resolve a bare name in a referrer's scope (closest DG up the chain,
        then ancestors, then shared)."""
        return self._idx_for(namespace).resolve(name, self.snapshot.ancestors(ref_location))

    def defined_at(self, namespace: str, name: str, location: Location) -> bool:
        """True if `name` is defined *directly* at `location` (ignoring
        inheritance) — used by shadow guards to find cross-level name clashes."""
        return self._idx_for(namespace).defined_at(name, location)

    def where_used(self, kind: str, name: str, location: Location) -> list[Reference]:
        return list(self._by_target.get(Target(kind, name, location), []))

    def dangling(self) -> list[Reference]:
        """References whose name resolves to no object (and isn't predefined).

        An unresolved PBF `nexthop` is excluded: it is just as often a literal
        IP/FQDN as an address-object name, so flagging it would be noise. A
        nexthop that *does* resolve still appears in `where_used` and is gated
        on merge/rename.
        """
        return [r for r in self.references if not r.is_resolved and r.field != "nexthop"]

    def _rule_seeded_targets(self, *, ignore_disabled: bool = False) -> set[Target]:
        # Seed reachability from *every* rulebase, not just security/nat — an
        # object referenced only by e.g. a QoS or PBF rule is still in use, and
        # reporting it unused would invite an unsafe delete. With
        # `ignore_disabled`, a disabled rule is not a root, so an object reached
        # only through disabled rules (directly or via a group) surfaces as
        # unused — the "only disabled references" cleanup case (#9).
        seeds: set[Target] = set()
        for r in self.references:
            if rule_container(r.referrer_kind) is None or r.resolved is None:
                continue
            if ignore_disabled and r.referrer_disabled:
                continue
            seeds.add(r.resolved)
        return seeds

    def _members_of(self, target: Target) -> list[Target]:
        """Resolved members of a group target (empty for leaf objects).

        For a *dynamic* address-group the members are the tag-matched addresses
        computed in `_resolve_dags`; for a static one they are the resolved
        named members.
        """
        out: list[Target] = []
        chain = self.snapshot.ancestors(target.location)
        if target.kind == "address-group":
            out.extend(self._dag_members.get(target, []))
            for ag in self.snapshot.address_groups:
                if ag.name == target.name and ag.location == target.location:
                    for m in ag.static_members or []:
                        t = self._addr_idx.resolve(m, chain)
                        if t is not None:
                            out.append(t)
        elif target.kind == "service-group":
            for sg in self.snapshot.service_groups:
                if sg.name == target.name and sg.location == target.location:
                    for m in sg.members:
                        t = self._svc_idx.resolve(m, chain)
                        if t is not None:
                            out.append(t)
        return out

    def reachable_targets(self, *, ignore_disabled: bool = False) -> set[Target]:
        """Every object a rule reaches, directly or transitively via groups.

        With `ignore_disabled`, disabled rules are not roots. Transitive
        reachability still flows only from enabled-rule seeds, so a group
        reachable *only* from disabled rules seeds nothing and its members drop
        out too — the transitive half of the #9 cleanup case.
        """
        seen = self._rule_seeded_targets(ignore_disabled=ignore_disabled)
        stack = list(seen)
        while stack:
            cur = stack.pop()
            for child in self._members_of(cur):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    def unused(self, kind: str, *, ignore_disabled: bool = False) -> list[Target]:
        """Objects of `kind` that no rule reaches (recursively).

        `kind` is one of: address, address-group, service, service-group, tag.
        Tags are special-cased: a tag is "used" if any object or rule carries
        it, or a dynamic address-group filter mentions it.

        With `ignore_disabled`, references originating from disabled rules are
        not counted as roots, so an object referenced solely by disabled rules
        (directly or transitively through groups) is reported unused — the
        cleanup candidates of #9. Group membership itself still counts; only
        *rule* roots are gated by the flag. Tags are unaffected: a tag carried
        by a disabled rule is still a real config setting on that rule.
        """
        if kind == "tag":
            return self._unused_tags()
        reachable = self.reachable_targets(ignore_disabled=ignore_disabled)
        defined = self._defined_targets(kind)
        return [t for t in defined if t not in reachable]

    def _defined_targets(self, kind: str) -> list[Target]:
        snap = self.snapshot
        # kind -> the defining objects; tag/unknown have no entry and yield [].
        collections: dict[str, Sequence[_Named]] = {
            "address": snap.addresses,
            "address-group": snap.address_groups,
            "service": snap.services,
            "service-group": snap.service_groups,
        }
        return [Target(kind, o.name, o.location) for o in collections.get(kind, [])]

    def _unused_tags(self) -> list[Target]:
        used: set[tuple[str, str]] = set()  # (location_name, tag_name) of the *referrer* context
        for r in self.references:
            if r.namespace == "tag" and r.resolved is not None:
                used.add((r.resolved.location.name, r.resolved.name))
        # Dynamic address-group filters reference tags by name. A token resolves
        # through the filter's *own* scope (closest definition up its ancestor
        # chain wins), exactly like a real object reference — so it marks only the
        # single tag the filter actually binds to, not every same-named tag. A
        # shadowed or sibling-DG copy the filter can't see stays unused (#26).
        for ag in self.snapshot.address_groups:
            if not ag.dynamic_filter:
                continue
            chain = self.snapshot.ancestors(ag.location)
            for token in dag_filter_tags(ag.dynamic_filter):
                bound = self._tag_idx.resolve(token, chain)
                if bound is not None:
                    used.add((bound.location.name, bound.name))
        return [
            Target("tag", t.name, t.location)
            for t in self.snapshot.tags
            if (t.location.name, t.name) not in used
        ]
