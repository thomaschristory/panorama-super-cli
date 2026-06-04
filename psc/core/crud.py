"""Create-or-update single objects, with client-side PAN-OS validation.

The `find`/`dedup`/`name` engines *read and rewrite* what already exists; this
one *authors* it. Each planner returns a `ChangeSet` carrying exactly one
`ObjectUpsert` (no new op kind needed — `setcmd`/`apply_xml`/`apply_live`
already render and apply upserts). Validation is raise-based and typed
`ErrorType.VALIDATION` (exit 4): the caller learns precisely why the spec was
rejected before any write is planned.

A *name collision across object kinds* at the same `(location, name)` is a
softer failure: it is appended as a `ChangeSet.blocker` rather than raised, so
the plan stays inspectable (dry-run still prints what was attempted) while the
executor refuses it — the same safety contract `dedup`/`name` use.

The leaf-key strings here are the contract with `setcmd.upsert_lines` and
`apply_xml._apply_upsert`/`apply_live._entry_xml`: address values key on the
`AddressType` value (`ip-netmask`, …), a dynamic group on `dynamic/filter`, a
service on `protocol/<proto>/port` (+ `source-port`), a tag on `color` /
`comments`. Static group / service-group members ride `ObjectUpsert.members`.
"""

from __future__ import annotations

import re

from psc.core.changeset import ChangeSet, ObjectKind, ObjectUpsert
from psc.core.models import Address, AddressGroup, AddressType, Location, Service, Snapshot
from psc.core.naming import INVALID_NAME_CHARS, NAME_MAX
from psc.output.errors import ErrorType, PscError

DESC_MAX = 255
TAG_NAME_MAX = 127

_PROTOCOLS = ("tcp", "udp")
_COLOR_RE = re.compile(r"color([1-9]|[1-3][0-9]|4[0-2])")
_PORT_MAX = 65535
_RANGE_PARTS = 2  # a `lo-hi` port range splits into exactly two integers


def _validate_name_charset(name: str, *, max_len: int, what: str) -> None:
    if not name:
        raise PscError(f"{what} must be non-empty", ErrorType.VALIDATION)
    if not name[0].isalnum():
        raise PscError(
            f"{what} '{name}' must start with an alphanumeric character", ErrorType.VALIDATION
        )
    if INVALID_NAME_CHARS.search(name):
        raise PscError(
            f"{what} '{name}' contains characters not allowed by PAN-OS "
            "(use letters, digits, '.', '_', '-', space)",
            ErrorType.VALIDATION,
        )
    if len(name) > max_len:
        raise PscError(
            f"{what} '{name}' exceeds the {max_len}-character limit", ErrorType.VALIDATION
        )


def validate_name(name: str) -> None:
    """Enforce the PAN-OS object-name rule (<=63, leading alphanumeric, charset)."""
    _validate_name_charset(name, max_len=NAME_MAX, what="object name")


def validate_tag_name(name: str) -> None:
    """Tag names follow the same charset rule but allow up to 127 characters."""
    _validate_name_charset(name, max_len=TAG_NAME_MAX, what="tag name")


def validate_description(desc: str | None) -> None:
    if desc is not None and len(desc) > DESC_MAX:
        raise PscError(f"description exceeds the {DESC_MAX}-character limit", ErrorType.VALIDATION)


def _validate_tags(tags: list[str]) -> None:
    for t in tags:
        validate_tag_name(t)


def _exists(snapshot: Snapshot, kind: ObjectKind, name: str, location: Location) -> bool:
    """True if an object of `kind` is defined directly at `(location, name)`."""
    key = (location.name, name)
    buckets = {
        ObjectKind.ADDRESS: (a.key for a in snapshot.addresses),
        ObjectKind.ADDRESS_GROUP: (g.key for g in snapshot.address_groups),
        ObjectKind.SERVICE: (s.key for s in snapshot.services),
        ObjectKind.SERVICE_GROUP: (g.key for g in snapshot.service_groups),
        ObjectKind.TAG: (t.key for t in snapshot.tags),
    }
    return key in set(buckets[kind])


def _existing_address(snapshot: Snapshot, name: str, location: Location) -> Address | None:
    return snapshot.address_index().get((location.name, name))


def _existing_address_group(
    snapshot: Snapshot, name: str, location: Location
) -> AddressGroup | None:
    key = (location.name, name)
    for g in snapshot.address_groups:
        if g.key == key:
            return g
    return None


def _existing_service(snapshot: Snapshot, name: str, location: Location) -> Service | None:
    return snapshot.service_index().get((location.name, name))


def _collision_blocker(
    cs: ChangeSet, snapshot: Snapshot, other: ObjectKind, name: str, location: Location
) -> None:
    """Append a blocker if an object of `other` kind already owns this name.

    Address and address-group share the PAN-OS `address` namespace (likewise
    service/service-group), so creating one over an existing sibling kind is a
    real clash. Recorded as a blocker, not a raise, so the dry-run plan still
    prints the attempted upsert and the executor refuses it.
    """
    if _exists(snapshot, other, name, location):
        cs.blockers.append(f"name '{name}' @{location.name} already used by a {other.value} object")


def _new_changeset(verb_kind: str, name: str, location: Location) -> ChangeSet:
    return ChangeSet(title=f"set {verb_kind} '{name}' @{location.name}")


def plan_address(
    snapshot: Snapshot,
    name: str,
    addr_type: AddressType,
    value: str,
    *,
    description: str | None,
    tags: list[str],
    location: Location,
) -> ChangeSet:
    validate_name(name)
    validate_description(description)
    _validate_tags(tags)
    cs = _new_changeset("address", name, location)
    _collision_blocker(cs, snapshot, ObjectKind.ADDRESS_GROUP, name, location)
    existing = _existing_address(snapshot, name, location)
    if existing is not None and existing.type is not addr_type:
        # An in-place type switch would leave the old value element behind on
        # offline apply, yielding an invalid dual-type object. Refuse it.
        cs.blockers.append(
            f"cannot change address '{name}' @{location.name} type from "
            f"{existing.type.value} to {addr_type.value} in place — delete and recreate"
        )
        return cs
    fields = {addr_type.value: value}  # value stored verbatim, never normalized
    if description:
        fields["description"] = description
    cs.upserts.append(
        ObjectUpsert(
            kind=ObjectKind.ADDRESS,
            name=name,
            location=location.name,
            fields=fields,
            tags=tags,
            exists=existing is not None,
        )
    )
    return cs


def plan_address_group(
    snapshot: Snapshot,
    name: str,
    *,
    static_members: list[str] | None,
    dynamic_filter: str | None,
    description: str | None,
    tags: list[str],
    location: Location,
) -> ChangeSet:
    validate_name(name)
    validate_description(description)
    _validate_tags(tags)
    has_static = bool(static_members)
    has_dynamic = dynamic_filter is not None
    if has_static == has_dynamic:
        raise PscError(
            "address-group requires exactly one of static members or a dynamic filter",
            ErrorType.VALIDATION,
        )
    cs = _new_changeset("address-group", name, location)
    _collision_blocker(cs, snapshot, ObjectKind.ADDRESS, name, location)
    existing = _existing_address_group(snapshot, name, location)
    if existing is not None and existing.is_dynamic != has_dynamic:
        old_mode = "dynamic" if existing.is_dynamic else "static"
        new_mode = "dynamic" if has_dynamic else "static"
        cs.blockers.append(
            f"cannot change address-group '{name}' @{location.name} mode from "
            f"{old_mode} to {new_mode} in place — delete and recreate"
        )
        return cs
    fields: dict[str, str] = {}
    members: list[str] = []
    if has_dynamic:
        assert dynamic_filter is not None
        fields["dynamic/filter"] = dynamic_filter
    else:
        assert static_members is not None
        members = static_members
    if description:
        fields["description"] = description
    cs.upserts.append(
        ObjectUpsert(
            kind=ObjectKind.ADDRESS_GROUP,
            name=name,
            location=location.name,
            fields=fields,
            members=members,
            tags=tags,
            exists=existing is not None,
        )
    )
    return cs


def _validate_port(port: str) -> None:
    """Validate a PAN-OS port spec's *structure*, not just its charset.

    Each comma-separated token is either a single integer in 1..65535, or a
    range `lo-hi` with both endpoints in 1..65535 and lo < hi. A charset regex
    alone would wave through `0`, `8080-80` (reversed), `1-2-3` (multi-hyphen)
    and empty pieces, all of which PAN-OS rejects.
    """
    bad = PscError(
        f"port '{port}' is malformed (single ports or lo-hi ranges in 1-{_PORT_MAX})",
        ErrorType.VALIDATION,
    )
    if not port:
        raise bad

    def _in_range(n: int) -> bool:
        return 1 <= n <= _PORT_MAX

    for token in port.split(","):
        parts = token.split("-")
        if not all(p.isdigit() for p in parts):
            raise bad
        nums = [int(p) for p in parts]
        if len(parts) == 1:
            if not _in_range(nums[0]):
                raise bad
        elif len(parts) == _RANGE_PARTS:
            lo, hi = nums
            if not (_in_range(lo) and _in_range(hi) and lo < hi):
                raise bad
        else:
            raise bad


def plan_service(
    snapshot: Snapshot,
    name: str,
    protocol: str,
    *,
    destination_port: str | None,
    source_port: str | None,
    description: str | None,
    tags: list[str],
    location: Location,
) -> ChangeSet:
    validate_name(name)
    validate_description(description)
    _validate_tags(tags)
    if protocol not in _PROTOCOLS:
        raise PscError(
            f"protocol '{protocol}' is not supported (use tcp or udp)", ErrorType.VALIDATION
        )
    if not destination_port:
        # PAN-OS makes the destination <port> element mandatory; a source-port-
        # only service is invalid.
        raise PscError("service requires a destination port (--dest-port)", ErrorType.VALIDATION)
    cs = _new_changeset("service", name, location)
    _collision_blocker(cs, snapshot, ObjectKind.SERVICE_GROUP, name, location)
    existing = _existing_service(snapshot, name, location)
    if existing is not None and existing.protocol != protocol:
        cs.blockers.append(
            f"cannot change service '{name}' @{location.name} protocol from "
            f"{existing.protocol} to {protocol} in place — delete and recreate"
        )
        return cs
    fields: dict[str, str] = {}
    if destination_port:
        _validate_port(destination_port)
        fields[f"protocol/{protocol}/port"] = destination_port
    if source_port:
        _validate_port(source_port)
        fields[f"protocol/{protocol}/source-port"] = source_port
    if description:
        fields["description"] = description
    cs.upserts.append(
        ObjectUpsert(
            kind=ObjectKind.SERVICE,
            name=name,
            location=location.name,
            fields=fields,
            tags=tags,
            exists=existing is not None,
        )
    )
    return cs


def plan_service_group(
    snapshot: Snapshot,
    name: str,
    members: list[str],
    *,
    tags: list[str],
    location: Location,
) -> ChangeSet:
    validate_name(name)
    _validate_tags(tags)
    if not members:
        raise PscError("service-group requires at least one member", ErrorType.VALIDATION)
    cs = _new_changeset("service-group", name, location)
    _collision_blocker(cs, snapshot, ObjectKind.SERVICE, name, location)
    cs.upserts.append(
        ObjectUpsert(
            kind=ObjectKind.SERVICE_GROUP,
            name=name,
            location=location.name,
            members=members,
            tags=tags,
            exists=_exists(snapshot, ObjectKind.SERVICE_GROUP, name, location),
        )
    )
    return cs


def plan_tag(
    snapshot: Snapshot,
    name: str,
    *,
    color: str | None,
    comments: str | None,
    location: Location,
) -> ChangeSet:
    validate_tag_name(name)
    if comments is not None and len(comments) > DESC_MAX:
        raise PscError(f"tag comments exceed the {DESC_MAX}-character limit", ErrorType.VALIDATION)
    if color is not None and not _COLOR_RE.fullmatch(color):
        raise PscError(
            f"color '{color}' is invalid (expected color1..color42)", ErrorType.VALIDATION
        )
    cs = _new_changeset("tag", name, location)
    fields: dict[str, str] = {}
    if color:
        fields["color"] = color
    if comments:
        fields["comments"] = comments
    cs.upserts.append(
        ObjectUpsert(
            kind=ObjectKind.TAG,
            name=name,
            location=location.name,
            fields=fields,
            exists=_exists(snapshot, ObjectKind.TAG, name, location),
        )
    )
    return cs
