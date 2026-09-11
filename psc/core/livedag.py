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
    unreadable_rows: int = 0
    """How many rows psc could not read. Each one is a gap in the coverage."""


class UnreadableDevice(BaseModel):
    """One firewall that Panorama names and that psc cannot read.

    Panorama tells psc that the firewall exists. psc must keep that fact. A
    dropped row would read as "this firewall does not exist", and the scan
    would then claim a coverage that psc does not have (#183).
    """

    label: str
    """The serial number, or another name when the row holds no serial number."""
    reason: str
    """Why psc cannot read the firewall. The text goes on the warning channel."""


class ConnectedDevices(BaseModel):
    """What `show devices connected` says about the managed firewalls."""

    devices: list[ManagedDevice] = Field(default_factory=list)
    """Each firewall that psc can query."""
    unreadable: list[UnreadableDevice] = Field(default_factory=list)
    """Each firewall that Panorama names and that psc cannot query."""


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
    unreadable_rows: int = 0
    """How many registered rows psc could not read."""
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_partial(self) -> bool:
        """True when psc did not read all of the registered data.

        A firewall that did not answer makes the coverage partial. A row that
        psc could not read does the same. The subject of such a row is unknown,
        so psc cannot rule out that the row holds the registration of the
        traced object.

        A value that psc read and could not normalize is a different case. psc
        knows that value, and it names the value on the warning channel. The
        operator can see that the value is not the value of the traced object.
        Such a value therefore does not make the coverage partial (#183).
        """
        return bool(self.failed_devices) or self.unreadable_rows > 0

    def coverage_gap(self) -> str:
        """One phrase that names every gap in the coverage.

        Both commands and the caveat print this phrase, so all three state the
        same fact in the same words. The phrase is empty when the coverage is
        complete.
        """
        parts: list[str] = []
        if self.failed_devices:
            parts.append(f"{', '.join(self.failed_devices)} did not answer")
        if self.unreadable_rows:
            rows = "row" if self.unreadable_rows == 1 else "rows"
            parts.append(f"psc could not read {self.unreadable_rows} registered {rows}")
        return "; ".join(parts)

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

    Every refusal here is a TRANSPORT error, and thus exit `7`. The command of
    the operator is correct. The answer of the device is what psc cannot read,
    so this is a failed query, not bad input (#183).
    """
    try:
        root: ET.Element = _safe_fromstring(xml_text)
    except Exception as exc:
        raise PscError(f"cannot read the answer to `{cmd}`: {exc}", ErrorType.TRANSPORT) from exc
    if root.tag == "result":
        # Some SDK versions hand back the <result> element alone.
        return root
    status = root.get("status")
    if status is not None and status != "success":
        detail = " ".join(root.itertext()).strip() or status
        raise PscError(f"the device refused `{cmd}`: {detail}", ErrorType.TRANSPORT)
    result = root.find("./result")
    if result is None:
        raise PscError(
            f"cannot read the answer to `{cmd}`: the answer holds no `<result>` element",
            ErrorType.TRANSPORT,
        )
    return result


def _text(entry: ET.Element, tag: str) -> str:
    return (entry.findtext(tag) or "").strip()


def parse_connected_devices(xml_text: str) -> ConnectedDevices:
    """Read `show devices connected` into the firewalls that psc can query.

    psc keeps every row. A firewall that `<connected>` reports as `no`, and a
    row with no serial number, are firewalls that psc cannot query. psc puts
    each one in `unreadable` with the reason.

    psc must not drop such a row. Panorama has told psc that the firewall
    exists, and that psc cannot read it. That is a gap in the coverage, not an
    absence. A dropped row would let `refs unused` report a live address as
    unused while the caveat claims full coverage (#183).

    Some PAN-OS versions omit the `<connected>` element. This command reports
    connected firewalls already, so a missing element reads as connected.
    """
    result = _result_element(xml_text, CONNECTED_DEVICES_CMD)
    devices: list[ManagedDevice] = []
    unreadable: list[UnreadableDevice] = []
    for index, entry in enumerate(result.findall("./devices/entry"), start=1):
        serial = _text(entry, "serial")
        hostname = _text(entry, "hostname")
        if not serial:
            label = hostname or f"row {index}"
            unreadable.append(
                UnreadableDevice(
                    label=label,
                    reason=(
                        f"Panorama reports a firewall ({label}) with no serial number. "
                        "psc cannot ask that firewall for its registered IPs."
                    ),
                )
            )
            continue
        if _text(entry, "connected").lower() == "no":
            unreadable.append(
                UnreadableDevice(
                    label=serial,
                    reason=(
                        f"firewall {serial} is not connected to Panorama. psc cannot "
                        "ask that firewall for its registered IPs."
                    ),
                )
            )
            continue
        devices.append(ManagedDevice(serial=serial, hostname=hostname))
    return ConnectedDevices(devices=devices, unreadable=unreadable)


def parse_registered_ip(xml_text: str) -> RegisteredIps:
    """Read `show object registered-ip all` into a value-to-tags map.

    The parser is tolerant of a row it can read in part. A missing `<tag>`
    child, an empty member list, and an unknown child element all give a result
    and no exception. An empty `<result>` is a device that holds no
    registration, which is a valid answer.

    The parser is strict about the shape of the answer. `_check_shape` refuses
    every answer that psc cannot read in full. The caller then treats the
    firewall as a firewall that did not answer. A silent short read would
    report a live host as unused.
    """
    result = _result_element(xml_text, REGISTERED_IP_CMD)
    entries = result.findall("./entry")
    _check_shape(result, len(entries))
    by_value: dict[str, frozenset[str]] = {}
    warnings: list[str] = []
    unreadable = 0
    for entry in entries:
        value = (entry.get("ip") or "").strip()
        if not value:
            unreadable += 1
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
    return RegisteredIps(by_value=by_value, warnings=warnings, unreadable_rows=unreadable)


def _check_shape(result: ET.Element, rows: int) -> None:
    """Refuse an answer whose shape psc does not know.

    psc reads the rows at `result/entry`. Two checks guard that one xpath.

    The first check reads the `<count>` element. psc compares `<count>` to the
    number of rows, and a disagreement means a short read. A `<count>` that is
    not a number means a shape that psc does not know. psc refuses both.

    The second check does not share the depth assumption of the first one. A
    result with no row, and with another element child, holds its data at a
    depth that psc does not read. psc refuses that answer too. An empty result
    is still a valid answer: it holds no element child, or it holds `<count>`
    alone.

    psc must never read an unknown shape as "this firewall holds no
    registration". Such a read turns absent data into full coverage (#183).
    """
    text = (result.findtext("./count") or "").strip()
    if text:
        try:
            count = int(text)
        except ValueError:
            raise PscError(
                f"cannot read the answer to `{REGISTERED_IP_CMD}`: the device counts "
                f"'{text}' registered IPs, which is not a number",
                ErrorType.TRANSPORT,
            ) from None
        if count != rows:
            raise PscError(
                f"cannot read the answer to `{REGISTERED_IP_CMD}`: the device counts "
                f"{count} registered IPs, and psc read {rows} rows",
                ErrorType.TRANSPORT,
            )
    if rows:
        return
    other = [child.tag for child in result if child.tag != "count"]
    if other:
        raise PscError(
            f"cannot read the answer to `{REGISTERED_IP_CMD}`: the answer holds no "
            f"`entry` row, and it holds `<{other[0]}>`, which psc does not know",
            ErrorType.TRANSPORT,
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
    per_device: Mapping[str, RegisteredIps],
    *,
    failed: Sequence[str] = (),
    unreadable: Sequence[UnreadableDevice] = (),
) -> LiveDagMembership:
    """Collect the registered IPs of every firewall into one membership map.

    One value can carry different tags on different firewalls. psc keeps one
    tag set per firewall, and the caller evaluates the filter against each set.
    A joined set is unsafe: a filter such as `'prod' and not 'quarantine'` can
    lose a member that one firewall really holds.

    Each firewall in `failed` also gives a warning. A firewall in `unreadable`
    gives the warning that the parser wrote for it: Panorama named the firewall,
    and psc could not query it at all. Both kinds count as a firewall that did
    not answer, so both make the coverage partial. The warning channel is the
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
    for device in unreadable:
        warnings.append(
            f"{device.reason} The registered IPs of that firewall are not in this scan."
        )
    return LiveDagMembership(
        by_key=by_key,
        devices=list(per_device),
        failed_devices=[*failed, *(d.label for d in unreadable)],
        indexed_values=len(by_key),
        skipped_values=len(skipped),
        unreadable_rows=sum(r.unreadable_rows for r in per_device.values()),
        warnings=warnings,
    )
