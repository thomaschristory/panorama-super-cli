"""Opt-in naming templates: derive a canonical object name from its value.

Naming is *opt-in* — `psc` never renames anything unless you ask. When you do,
this module computes the name a template implies (`H-10.0.0.10`,
`N-10.0.0.0_24`, `tcp-443`, ...), reports drift (`lint`), and builds a
reference-aware rename plan. Renames go through the same `ChangeSet` machinery
as merges, so every group/rule reference is repointed and the dangerous
shared-vs-device-group shadow case is detected and refused.
"""

from __future__ import annotations

import ipaddress
import re

from pydantic import BaseModel

from psc.core.changeset import (
    ChangeSet,
    ObjectKind,
    ObjectRename,
    ReferenceEdit,
    gate_unmappable_reference_edits,
)
from psc.core.dedup import field_members
from psc.core.models import Address, AddressType, Location, Service, Snapshot
from psc.core.refs import ReferenceGraph

# PAN-OS object names: <=63 chars, start alphanumeric, then [0-9a-zA-Z._-] (+space).
# Public so the CRUD validators consume the *same* rule rather than re-declaring
# the limit and pattern (a divergence a reviewer would rightly flag). The
# underscore-prefixed aliases keep this module's internal call sites unchanged.
NAME_MAX = 63
INVALID_NAME_CHARS = re.compile(r"[^0-9A-Za-z._\- ]")
_NAME_MAX = NAME_MAX
_INVALID = INVALID_NAME_CHARS


def sanitize_name(raw: str) -> str:
    name = _INVALID.sub("_", raw).strip()
    if name and not name[0].isalnum():
        name = "x" + name
    return name[:_NAME_MAX] or "x"


class NamingScheme(BaseModel):
    """Format strings per value-kind. Override any subset via config."""

    host: str = "H-{ip}"
    network: str = "N-{network}_{prefix}"
    range: str = "R-{start}-{end}"
    fqdn: str = "FQDN-{fqdn}"
    wildcard: str = "W-{value}"
    service_tcp: str = "tcp-{port}"
    service_udp: str = "udp-{port}"
    lowercase: bool = False

    def _finish(self, name: str) -> str:
        name = name.lower() if self.lowercase else name
        return sanitize_name(name)

    def address_name(self, addr: Address) -> str | None:  # noqa: PLR0911 — per value-kind
        """The name this scheme implies for `addr`, or None if not derivable."""
        v = addr.value.strip()
        if addr.type is AddressType.IP_NETMASK:
            try:
                net = ipaddress.ip_network(v, strict=False)
            except ValueError:
                return None
            if net.prefixlen in (32, 128):
                return self._finish(self.host.format(ip=str(net.network_address)))
            return self._finish(
                self.network.format(network=str(net.network_address), prefix=net.prefixlen)
            )
        if addr.type is AddressType.IP_RANGE and "-" in v:
            start, _, end = v.partition("-")
            return self._finish(self.range.format(start=start.strip(), end=end.strip()))
        if addr.type is AddressType.FQDN:
            return self._finish(self.fqdn.format(fqdn=v.rstrip(".").lower()))
        if addr.type is AddressType.IP_WILDCARD:
            return self._finish(self.wildcard.format(value=v.replace(" ", "_")))
        return None

    def service_name(self, svc: Service) -> str | None:
        if not svc.destination_port:
            return None
        tmpl = self.service_tcp if svc.protocol.lower() == "tcp" else self.service_udp
        return self._finish(tmpl.format(port=svc.destination_port, proto=svc.protocol.lower()))


class NameFinding(BaseModel):
    kind: str
    location: str
    current: str
    suggested: str
    compliant: bool


def lint(snapshot: Snapshot, scheme: NamingScheme) -> list[NameFinding]:
    """Report every address/service whose name differs from the scheme."""
    findings: list[NameFinding] = []
    for a in snapshot.addresses:
        suggested = scheme.address_name(a)
        if suggested is None:
            continue
        findings.append(
            NameFinding(
                kind="address",
                location=a.location.name,
                current=a.name,
                suggested=suggested,
                compliant=a.name == suggested,
            )
        )
    for s in snapshot.services:
        suggested = scheme.service_name(s)
        if suggested is None:
            continue
        findings.append(
            NameFinding(
                kind="service",
                location=s.location.name,
                current=s.name,
                suggested=suggested,
                compliant=s.name == suggested,
            )
        )
    return findings


def plan_rename(
    snapshot: Snapshot,
    graph: ReferenceGraph,
    *,
    kind: ObjectKind,
    location_name: str,
    old_name: str,
    new_name: str,
) -> ChangeSet:
    """Plan a reference-aware rename of one object within its location."""
    loc = Location.shared() if location_name == "shared" else Location.dg(location_name)
    cs = ChangeSet(title=f"rename {kind.value} '{old_name}' -> '{new_name}' @{location_name}")

    new_clean = sanitize_name(new_name)
    if new_clean != new_name:
        cs.warnings.append(f"new name sanitized to '{new_clean}' (PAN-OS naming rules)")
        new_name = new_clean

    namespace = (
        "address"
        if kind in (ObjectKind.ADDRESS, ObjectKind.ADDRESS_GROUP)
        else ("service" if kind in (ObjectKind.SERVICE, ObjectKind.SERVICE_GROUP) else "tag")
    )

    # Shadow guard: introducing `new_name` at `loc` is unsafe if that name is
    # already defined anywhere in loc's visibility cone — an ancestor (the new
    # name would shadow it for references here and below) or a descendant (it
    # would shadow this one for references in between). Renaming a same-location
    # clash is a plain collision. All silently re-point references, so refuse.
    if graph.defined_at(namespace, new_name, loc):
        cs.blockers.append(f"'{new_name}' already exists in {namespace} namespace @{location_name}")
    cone: set[str] = set()
    if loc.is_shared:
        cone = set(snapshot.device_groups)
    else:
        cone = {a.name for a in snapshot.ancestors(loc) if a != loc}
        cone |= snapshot.descendant_dgs(location_name)
    for other in sorted(cone):
        other_loc = Location.shared() if other == "shared" else Location.dg(other)
        if graph.defined_at(namespace, new_name, other_loc):
            cs.blockers.append(
                f"{other_loc} already defines '{new_name}' in the {namespace} "
                f"namespace; renaming would shadow it across the device-group hierarchy"
            )

    if cs.is_blocked:
        return cs

    for ref in graph.where_used(kind.value, old_name, loc):
        before = field_members(snapshot, ref)
        after = [new_name if m == old_name else m for m in before]
        cs.reference_edits.append(
            ReferenceEdit(
                referrer_kind=ref.referrer_kind,
                referrer_name=ref.referrer_name,
                referrer_location=ref.referrer_location.name,
                field=ref.field,
                rulebase=ref.rulebase.value if ref.rulebase else None,
                before=before,
                after=after,
            )
        )
    cs.renames.append(
        ObjectRename(kind=kind, location=location_name, old_name=old_name, new_name=new_name)
    )
    # Refuse any repoint the appliers would silently skip (e.g. a NAT translation
    # field): skipping it while renaming the object away leaves a dangling
    # reference. Shared gate keeps offline and live identical (#28).
    gate_unmappable_reference_edits(cs)
    if cs.blockers:
        # Invariant: a blocked plan carries zero ops (see `plan_merge`).
        cs.reference_edits.clear()
        cs.renames.clear()
    return cs
