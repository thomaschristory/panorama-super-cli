"""Dynamic address-group membership that only a live firewall knows (#183).

A dynamic address-group (DAG) selects addresses by a tag expression. PAN-OS
evaluates that expression against two tag sources: the tags in the config, and
the tags of an **externally registered IP** (XML-API, User-ID, VM-info, or a
cloud plugin). The second source is runtime state. An exported config does not
hold it, so `refs unused` cannot see it and can report a live host as unused.

This module turns the answer of two op commands into a plain membership map:

- `show devices connected` gives the serial number of each firewall, and
- `show object registered-ip all` gives the registered IPs of one firewall,
  with the tags of each IP.

The module is pure. It does no device I/O, and it imports no SDK. The live
calls live in `psc.core.source.LiveSource`, and the reference graph takes the
result through `ReferenceGraph.build(..., live_dag=...)`.

Matching rule: psc joins a registered value to an address object only when the
two values are identical after normalization. A registered host therefore never
marks a larger network object as used. psc does no DNS, so an FQDN object never
matches a registered IP. A registered host does not match an `ip-range` object
of one address either, because the key holds the value kind.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence

from defusedxml.ElementTree import fromstring as _safe_fromstring
from pydantic import BaseModel, Field

from psc.core.models import Address, AddressType
from psc.core.normalize import normalize_address
from psc.output.errors import ErrorType, PscError

CONNECTED_DEVICES_CMD = "show devices connected"
"""Lists the firewalls that Panorama manages and that are connected to it."""

REGISTERED_IP_CMD = "show object registered-ip all"
"""Lists the registered IPs of one firewall, with the tags of each IP."""


class ManagedDevice(BaseModel):
    """One firewall that Panorama manages."""

    serial: str
    hostname: str = ""


class RegisteredIps(BaseModel):
    """The registered IPs of one firewall, keyed on the value the device prints."""

    by_value: dict[str, frozenset[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    """Rows that psc could not read. A bad row never stops the scan."""


class LiveDagMembership(BaseModel):
    """Registered tags of the whole estate, keyed on a normalized address value."""

    by_key: dict[str, frozenset[str]] = Field(default_factory=dict)
    devices: list[str] = Field(default_factory=list)
    """Serial number of each firewall that answered."""
    failed_devices: list[str] = Field(default_factory=list)
    """Serial number of each firewall that did not answer."""
    indexed_values: int = 0
    """How many distinct address values the map holds."""
    skipped_values: int = 0
    """How many registered values psc could not normalize."""
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_partial(self) -> bool:
        """True when one or more firewalls did not answer."""
        return bool(self.failed_devices)

    def tags_for(self, addr: Address) -> frozenset[str]:
        """The registered tags of `addr`, or an empty set.

        The lookup key holds the value kind, and it keeps the host bits. Thus a
        registered host matches only an address object of the same value.
        """
        value = normalize_address(addr)
        if value is None:
            return frozenset()
        return self.by_key.get(value.exact_key(), frozenset())

    def unmatched_values(self, addresses: Iterable[Address]) -> int:
        """How many registered values no address object in `addresses` matches.

        The count tells the operator how much of the live data psc could join to
        the config. A large count is normal on an estate that registers many
        hosts that have no address object.
        """
        known = {value.exact_key() for value in (normalize_address(a) for a in addresses) if value}
        return sum(1 for key in self.by_key if key not in known)


def _result_element(xml_text: str, cmd: str) -> ET.Element | None:
    """The `<result>` element of an op-command answer, or None when it is absent.

    Parsing goes through `defusedxml`, like `psc.core.parse`, so a hostile
    answer cannot expand entities. A raw `ParseError` never escapes: the caller
    gets a typed error that names the command.
    """
    try:
        root: ET.Element = _safe_fromstring(xml_text)
    except Exception as exc:
        raise PscError(f"cannot read the answer to `{cmd}`: {exc}", ErrorType.INPUT) from exc
    if root.tag == "result":
        # Some SDK versions hand back the <result> element alone.
        return root
    status = root.get("status")
    if status is not None and status != "success":
        detail = " ".join(root.itertext()).strip() or status
        raise PscError(f"the device refused `{cmd}`: {detail}", ErrorType.INPUT)
    return root.find("./result")


def _text(entry: ET.Element, tag: str) -> str:
    return (entry.findtext(tag) or "").strip()


def parse_connected_devices(xml_text: str) -> list[ManagedDevice]:
    """Read `show devices connected` into a list of firewalls.

    A row without a serial number is unusable, so psc drops it. psc drops a row
    only when `<connected>` says `no`. This command reports connected firewalls
    already, and some PAN-OS versions omit the `<connected>` element. A missing
    element must not empty the list.
    """
    result = _result_element(xml_text, CONNECTED_DEVICES_CMD)
    if result is None:
        return []
    devices: list[ManagedDevice] = []
    for entry in result.findall("./devices/entry"):
        serial = _text(entry, "serial")
        if not serial:
            continue
        if _text(entry, "connected").lower() == "no":
            continue
        devices.append(ManagedDevice(serial=serial, hostname=_text(entry, "hostname")))
    return devices


def parse_registered_ip(xml_text: str) -> RegisteredIps:
    """Read `show object registered-ip all` into a value-to-tags map.

    The parser is tolerant. An empty result, a missing `<tag>` child, an empty
    member list, a `<count>` sibling, and an unknown child element all give a
    result and no exception.
    """
    result = _result_element(xml_text, REGISTERED_IP_CMD)
    if result is None:
        return RegisteredIps()
    by_value: dict[str, frozenset[str]] = {}
    warnings: list[str] = []
    for entry in result.findall("./entry"):
        value = (entry.get("ip") or "").strip()
        if not value:
            warnings.append(
                f"`{REGISTERED_IP_CMD}` returned an entry with no `ip` attribute; psc skips it"
            )
            continue
        tags = frozenset(
            text
            for member in entry.findall("./tag/member")
            if (text := (member.text or "").strip())
        )
        by_value[value] = by_value.get(value, frozenset()) | tags
    return RegisteredIps(by_value=by_value, warnings=warnings)


def _registered_key(value: str) -> str | None:
    """The normalized lookup key of a registered value, or None.

    PAN-OS registers a host, and newer versions also accept a subnet or a range.
    psc reads a value with a `-` as a range, and every other value as an
    ip-netmask value. A value that does not parse gives None.
    """
    kind = AddressType.IP_RANGE if "-" in value else AddressType.IP_NETMASK
    probe = normalize_address(Address(name="_probe", type=kind, value=value))
    return probe.exact_key() if probe is not None else None


def build_membership(
    per_device: Mapping[str, RegisteredIps], *, failed: Sequence[str] = ()
) -> LiveDagMembership:
    """Join the registered IPs of every firewall into one membership map.

    One value can carry different tags on different firewalls, so psc adds the
    tags together. The result is the tag set that any firewall of the estate
    sees for that value.
    """
    by_key: dict[str, frozenset[str]] = {}
    warnings: list[str] = []
    skipped: set[str] = set()
    for serial in per_device:
        registered = per_device[serial]
        warnings.extend(registered.warnings)
        for value, tags in registered.by_value.items():
            key = _registered_key(value)
            if key is None:
                if value not in skipped:
                    skipped.add(value)
                    warnings.append(
                        f"firewall {serial} registered '{value}', which is not an address "
                        "value psc can read; psc skips it"
                    )
                continue
            by_key[key] = by_key.get(key, frozenset()) | tags
    return LiveDagMembership(
        by_key=by_key,
        devices=list(per_device),
        failed_devices=list(failed),
        indexed_values=len(by_key),
        skipped_values=len(skipped),
        warnings=warnings,
    )
