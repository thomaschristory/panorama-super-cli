"""Framework-free domain model for Panorama objects, groups, and rules.

These Pydantic models are the lingua franca between every backend engine and
every frontend. They are deliberately decoupled from `pan-os-python`: the
live client and the XML parser both translate *into* these types, so the
engines never touch SDK internals. A web frontend would serialize these same
models straight to JSON.
"""

from __future__ import annotations

from collections import defaultdict
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_serializer, model_validator


class AddressType(str, Enum):
    """The four address-object value kinds PAN-OS supports."""

    IP_NETMASK = "ip-netmask"
    IP_RANGE = "ip-range"
    IP_WILDCARD = "ip-wildcard"
    FQDN = "fqdn"


class Rulebase(str, Enum):
    """Where a rule sits in the Panorama push order."""

    PRE = "pre"
    POST = "post"


class Location(BaseModel):
    """Where an object lives: Panorama `shared`, or a named device-group.

    A `Location` names a single device-group (or `shared`); the *hierarchy*
    between device-groups lives on the `Snapshot` (`device_group_parents`), so
    inheritance is resolved with `Snapshot.ancestors(...)`. The `Location` is
    frozen so it can key dictionaries and sets (dedup buckets, reference
    graphs).
    """

    model_config = {"frozen": True}

    device_group: str | None = None
    """`None` => the Panorama `shared` location."""

    @property
    def is_shared(self) -> bool:
        return self.device_group is None

    @property
    def name(self) -> str:
        return self.device_group or "shared"

    @classmethod
    def shared(cls) -> Location:
        return cls(device_group=None)

    @classmethod
    def dg(cls, name: str) -> Location:
        return cls(device_group=name)

    def __str__(self) -> str:
        return self.name

    @model_validator(mode="before")
    @classmethod
    def _accept_string(cls, value: Any) -> Any:
        # Accept a bare "shared" / "<dg>" string in addition to the dict form,
        # so JSON that used the serialized string form round-trips.
        if isinstance(value, str):
            return {"device_group": None if value == "shared" else value}
        return value

    @model_serializer
    def _serialize(self) -> str:
        # Emit the readable name ("shared" / "<dg>") everywhere, not the
        # `{"device_group": null}` struct — cleaner for the agent JSON contract.
        return self.name


SHARED = Location.shared()


class _Named(BaseModel):
    """Common identity fields. `name` is unique only within a `location`."""

    name: str
    location: Location = SHARED
    description: str | None = None
    tags: list[str] = Field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        """`(location_name, object_name)` — globally unique per object kind."""
        return (self.location.name, self.name)


class Address(_Named):
    type: AddressType
    value: str


class AddressGroup(_Named):
    static_members: list[str] | None = None
    """`None` for a dynamic group; a list (possibly empty) for a static one."""
    dynamic_filter: str | None = None

    @property
    def is_dynamic(self) -> bool:
        return self.dynamic_filter is not None


class Service(_Named):
    protocol: str  # "tcp" | "udp"
    destination_port: str | None = None
    source_port: str | None = None


class ServiceGroup(_Named):
    members: list[str] = Field(default_factory=list)


class Tag(BaseModel):
    name: str
    location: Location = SHARED
    color: str | None = None
    comments: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.location.name, self.name)


class SecurityRule(BaseModel):
    """Only the fields that carry object references plus identity/state.

    High-fidelity round-tripping of every rule field is out of scope: `psc`
    edits *object references*, not rule semantics, so we model the reference
    surface (source/destination/service/tag) faithfully and leave the rest to
    the live device.
    """

    name: str
    location: Location = SHARED
    rulebase: Rulebase = Rulebase.PRE
    source: list[str] = Field(default_factory=lambda: ["any"])
    destination: list[str] = Field(default_factory=lambda: ["any"])
    service: list[str] = Field(default_factory=lambda: ["any"])
    application: list[str] = Field(default_factory=lambda: ["any"])
    source_user: list[str] = Field(default_factory=lambda: ["any"])
    action: str = "allow"
    disabled: bool = False
    tags: list[str] = Field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.location.name, self.rulebase.value, self.name)


class NatRule(BaseModel):
    """NAT rules reference addresses in match *and* translation fields.

    Merging/renaming an address must rewrite these too, or traffic silently
    breaks — hence NAT is first-class in the reference graph from v0.1.
    """

    name: str
    location: Location = SHARED
    rulebase: Rulebase = Rulebase.PRE
    source: list[str] = Field(default_factory=lambda: ["any"])
    destination: list[str] = Field(default_factory=lambda: ["any"])
    service: str = "any"
    source_translation: list[str] = Field(default_factory=list)
    destination_translation: str | None = None
    disabled: bool = False
    tags: list[str] = Field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.location.name, self.rulebase.value, self.name)


class Snapshot(BaseModel):
    """An immutable point-in-time view of the parts of a Panorama config
    `psc` understands. Built by the XML parser or the live client; consumed
    (never mutated) by every read engine. Writes produce a `ChangeSet`, not a
    mutated `Snapshot`.
    """

    addresses: list[Address] = Field(default_factory=list)
    address_groups: list[AddressGroup] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)
    service_groups: list[ServiceGroup] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    security_rules: list[SecurityRule] = Field(default_factory=list)
    nat_rules: list[NatRule] = Field(default_factory=list)
    device_groups: list[str] = Field(default_factory=list)
    device_group_parents: dict[str, str] = Field(default_factory=dict)
    """Child device-group name → its parent device-group name. A device-group
    absent here (or whose chain reaches the top) is a direct child of `shared`.
    Empty for a flat (single-level) Panorama.
    """

    # --- device-group hierarchy -----------------------------------------

    def ancestors(self, location: Location) -> list[Location]:
        """The locations a reference *in* `location` can resolve against, in
        precedence order (closest first, `shared` last).

        `shared` → `[shared]`. A device-group → itself, then each parent up the
        chain, then `shared`. Self-referential/cyclic parent data is truncated
        rather than looped forever.
        """
        chain: list[Location] = []
        cur = location.device_group
        seen: set[str] = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            chain.append(Location.dg(cur))
            cur = self.device_group_parents.get(cur)
        chain.append(SHARED)
        return chain

    def descendant_dgs(self, dg_name: str) -> set[str]:
        """Every device-group that has `dg_name` somewhere among its ancestors."""
        out: set[str] = set()
        for dg in self.device_groups:
            cur = self.device_group_parents.get(dg)
            seen: set[str] = set()
            while cur is not None and cur not in seen:
                if cur == dg_name:
                    out.add(dg)
                    break
                seen.add(cur)
                cur = self.device_group_parents.get(cur)
        return out

    # --- indexes (built lazily, not serialized) -------------------------

    def address_index(self) -> dict[tuple[str, str], Address]:
        return {a.key: a for a in self.addresses}

    def service_index(self) -> dict[tuple[str, str], Service]:
        return {s.key: s for s in self.services}

    def addresses_by_location(self) -> dict[str, list[Address]]:
        out: dict[str, list[Address]] = defaultdict(list)
        for a in self.addresses:
            out[a.location.name].append(a)
        return dict(out)

    def locations(self) -> list[Location]:
        """All distinct locations, `shared` first then device-groups."""
        seen: list[Location] = [SHARED]
        for dg in sorted(self.device_groups):
            seen.append(Location.dg(dg))
        return seen
