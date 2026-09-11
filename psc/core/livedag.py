"""Dynamic address-group membership that only a live firewall knows (#183).

A dynamic address-group (DAG) selects addresses by a tag expression. PAN-OS
evaluates that expression against two tag sources. The first source is the tags
in the config. The second source is the tags of an **externally registered IP**
(XML-API, User-ID, VM-info, or a cloud plugin). The second source is runtime
state. An exported config does not hold it, so `refs unused` cannot see it and
can report a live host as unused.

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
    """Registered tags of the whole estate, keyed on a normalized address value.

    The value of `by_key` is one tag set per firewall that registered the
    address value. psc keeps the sets apart, and it never joins them into one
    set. Two firewalls can register the same IP with different tags, and a
    filter can negate a tag. One joined set could therefore lose a member that
    a single firewall really holds (#183).
    """

    by_key: dict[str, list[frozenset[str]]] = Field(default_factory=dict)
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

    def tag_sets_for(self, addr: Address) -> list[frozenset[str]]:
        """The registered tags of `addr` on each firewall that holds it.

        The lookup key holds the value kind, and it keeps the host bits. Thus a
        registered host matches only an address object of the same value. The
        caller evaluates a filter against each set on its own.
        """
        value = normalize_address(addr)
        if value is None:
            return []
        return self.by_key.get(value.exact_key(), [])

    def tags_for(self, addr: Address) -> frozenset[str]:
        """Every registered tag of `addr`, from every firewall, in one set.

        This set is for a human who reads a count or a listing. A filter never
        runs against it: use `tag_sets_for` for that.
        """
        sets = self.tag_sets_for(addr)
        return frozenset().union(*sets) if sets else frozenset()

    def unmatched_values(self, addresses: Iterable[Address]) -> int:
        """How many registered values no address object in `addresses` matches.

        The count tells the operator how much of the live data psc could join to
        the config. A large count is normal on an estate that registers many
        hosts that have no address object.
        """
        known = {value.exact_key() for value in (normalize_address(a) for a in addresses) if value}
        return sum(1 for key in self.by_key if key not in known)


def _result_element(xml_text: str, cmd: str) -> ET.Element:
    """The `<result>` element of an op-command answer.

    Parsing goes through `defusedxml`, like `psc.core.parse`, so a hostile
    answer cannot expand entities. A raw `ParseError` never escapes: the caller
    gets a typed error that names the command.

    A successful op answer always carries a `<result>` element. An answer
    without one is an answer that psc cannot read, and psc refuses it. psc must
    not read an unknown shape as "the device holds nothing": that reads absent
    data as full coverage, which is the failure this module exists to stop.
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
    result = root.find("./result")
    if result is None:
        raise PscError(
            f"cannot read the answer to `{cmd}`: the answer holds no `<result>` element",
            ErrorType.INPUT,
        )
    return result


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

    The parser is tolerant of a row it can read in part. A missing `<tag>`
    child, an empty member list, and an unknown child element all give a result
    and no exception. An empty `<result>` is a device that holds no
    registration, which is a valid answer.

    The parser is strict about the shape of the answer. The device prints a
    `<count>` element, and psc compares it to the number of `<entry>` rows. A
    disagreement means that psc read only a part of the answer, so psc refuses
    the answer. The caller then treats the firewall as a firewall that did not
    answer. A silent short read would report a live host as unused.
    """
    result = _result_element(xml_text, REGISTERED_IP_CMD)
    entries = result.findall("./entry")
    _check_count(result, len(entries))
    by_value: dict[str, frozenset[str]] = {}
    warnings: list[str] = []
    for entry in entries:
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


def _check_count(result: ET.Element, rows: int) -> None:
    """Refuse an answer whose `<count>` disagrees with the number of rows.

    `<count>` is the one field of the answer that can disagree with the rows.
    A disagreement means a shape that psc does not know, or an answer that the
    device cut short. psc refuses both. An absent or non-numeric `<count>` gives
    no check, because psc has nothing to compare.
    """
    text = (result.findtext("./count") or "").strip()
    if not text:
        return
    try:
        count = int(text)
    except ValueError:
        return
    if count != rows:
        raise PscError(
            f"cannot read the answer to `{REGISTERED_IP_CMD}`: the device counts "
            f"{count} registered IPs, and psc read {rows} rows",
            ErrorType.INPUT,
        )


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
    """Collect the registered IPs of every firewall into one membership map.

    One value can carry different tags on different firewalls. psc keeps one
    tag set per firewall, and the caller evaluates the filter against each set.
    A joined set is unsafe: a filter such as `'prod' and not 'quarantine'` can
    lose a member that one firewall really holds.

    Each firewall in `failed` also gives a warning. The warning channel is the
    one channel that `--no-caveat` does not silence, so partial coverage always
    reaches the operator (#183).
    """
    by_key: dict[str, list[frozenset[str]]] = {}
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
            by_key.setdefault(key, []).append(tags)
    for serial in failed:
        warnings.append(
            f"firewall {serial} did not answer `{REGISTERED_IP_CMD}`. The registered "
            "IPs of that firewall are not in this scan."
        )
    return LiveDagMembership(
        by_key=by_key,
        devices=list(per_device),
        failed_devices=list(failed),
        indexed_values=len(by_key),
        skipped_values=len(skipped),
        warnings=warnings,
    )
