"""Parse a Panorama configuration XML document into a `Snapshot`.

Works on anything that contains a standard `<config>` subtree: a full
running/candidate config export, the body of an `<show><config><running>`
API response, or a hand-trimmed fixture. The same parser feeds both the
offline (`--config file.xml`) and live (API `show config`) paths, so there is
exactly one place that understands PAN-OS XML shape.

Parsing goes through `defusedxml`, so pointing `psc` at an untrusted config
(XXE / billion-laughs payloads) is safe; entity expansion and external DTDs
are rejected.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from defusedxml.ElementTree import fromstring as _safe_fromstring

from psc.core.models import (
    Address,
    AddressGroup,
    AddressType,
    Location,
    NatRule,
    PolicyRule,
    Rulebase,
    RuleType,
    SecurityRule,
    Service,
    ServiceGroup,
    Snapshot,
    Tag,
)

_ADDR_TAGS: dict[str, AddressType] = {
    "ip-netmask": AddressType.IP_NETMASK,
    "ip-range": AddressType.IP_RANGE,
    "ip-wildcard": AddressType.IP_WILDCARD,
    "fqdn": AddressType.FQDN,
}


def _members(entry: ET.Element, tag: str) -> list[str]:
    """`<tag><member>x</member><member>y</member></tag>` -> ['x', 'y']."""
    parent = entry.find(tag)
    if parent is None:
        return []
    return [m.text.strip() for m in parent.findall("member") if m.text]


def _text(entry: ET.Element, tag: str) -> str | None:
    el = entry.find(tag)
    if el is None or el.text is None:
        return None
    return el.text.strip()


def _member_list_or_any(entry: ET.Element, tag: str) -> list[str]:
    vals = _members(entry, tag)
    return vals or ["any"]


def _parse_addresses(parent: ET.Element, loc: Location) -> list[Address]:
    out: list[Address] = []
    for entry in parent.findall("./address/entry"):
        name = entry.get("name")
        if not name:
            continue
        atype: AddressType | None = None
        value: str | None = None
        for xml_tag, kind in _ADDR_TAGS.items():
            v = _text(entry, xml_tag)
            if v is not None:
                atype, value = kind, v
                break
        if atype is None or value is None:
            continue
        out.append(
            Address(
                name=name,
                location=loc,
                type=atype,
                value=value,
                description=_text(entry, "description"),
                tags=_members(entry, "tag"),
            )
        )
    return out


def _parse_address_groups(parent: ET.Element, loc: Location) -> list[AddressGroup]:
    out: list[AddressGroup] = []
    for entry in parent.findall("./address-group/entry"):
        name = entry.get("name")
        if not name:
            continue
        dynamic = entry.find("dynamic")
        static = entry.find("static")
        out.append(
            AddressGroup(
                name=name,
                location=loc,
                static_members=(
                    [m.text.strip() for m in static.findall("member") if m.text]
                    if static is not None
                    else None
                ),
                dynamic_filter=(_text(entry, "dynamic/filter") if dynamic is not None else None),
                description=_text(entry, "description"),
                tags=_members(entry, "tag"),
            )
        )
    return out


def _parse_services(parent: ET.Element, loc: Location) -> list[Service]:
    out: list[Service] = []
    for entry in parent.findall("./service/entry"):
        name = entry.get("name")
        if not name:
            continue
        proto = "tcp" if entry.find("protocol/tcp") is not None else "udp"
        out.append(
            Service(
                name=name,
                location=loc,
                protocol=proto,
                destination_port=_text(entry, f"protocol/{proto}/port"),
                source_port=_text(entry, f"protocol/{proto}/source-port"),
                description=_text(entry, "description"),
                tags=_members(entry, "tag"),
            )
        )
    return out


def _parse_service_groups(parent: ET.Element, loc: Location) -> list[ServiceGroup]:
    out: list[ServiceGroup] = []
    for entry in parent.findall("./service-group/entry"):
        name = entry.get("name")
        if not name:
            continue
        out.append(
            ServiceGroup(
                name=name,
                location=loc,
                members=_members(entry, "members"),
                tags=_members(entry, "tag"),
            )
        )
    return out


def _parse_tags(parent: ET.Element, loc: Location) -> list[Tag]:
    out: list[Tag] = []
    for entry in parent.findall("./tag/entry"):
        name = entry.get("name")
        if not name:
            continue
        out.append(
            Tag(
                name=name,
                location=loc,
                color=_text(entry, "color"),
                comments=_text(entry, "comments"),
            )
        )
    return out


def _parse_security_rules(parent: ET.Element, loc: Location, rb: Rulebase) -> list[SecurityRule]:
    out: list[SecurityRule] = []
    for entry in parent.findall("./security/rules/entry"):
        name = entry.get("name")
        if not name:
            continue
        out.append(
            SecurityRule(
                name=name,
                location=loc,
                rulebase=rb,
                source=_member_list_or_any(entry, "source"),
                destination=_member_list_or_any(entry, "destination"),
                service=_member_list_or_any(entry, "service"),
                application=_member_list_or_any(entry, "application"),
                source_user=_member_list_or_any(entry, "source-user"),
                action=_text(entry, "action") or "allow",
                disabled=_text(entry, "disabled") == "yes",
                tags=_members(entry, "tag"),
            )
        )
    return out


def _parse_nat_rules(parent: ET.Element, loc: Location, rb: Rulebase) -> list[NatRule]:
    out: list[NatRule] = []
    for entry in parent.findall("./nat/rules/entry"):
        name = entry.get("name")
        if not name:
            continue
        src_xlate: list[str] = []
        st = entry.find("source-translation")
        if st is not None:
            for ta in st.iter("translated-address"):
                # Either a single text value or a list of <member>s.
                src_xlate.extend(m.text.strip() for m in ta.findall("member") if m.text)
                if ta.text and ta.text.strip():
                    src_xlate.append(ta.text.strip())
        dst_xlate = _text(entry, "destination-translation/translated-address")
        out.append(
            NatRule(
                name=name,
                location=loc,
                rulebase=rb,
                source=_member_list_or_any(entry, "source"),
                destination=_member_list_or_any(entry, "destination"),
                service=_text(entry, "service") or "any",
                source_translation=src_xlate,
                destination_translation=dst_xlate,
                disabled=_text(entry, "disabled") == "yes",
                tags=_members(entry, "tag"),
            )
        )
    return out


def _parse_policy_rules(parent: ET.Element, loc: Location, rb: Rulebase) -> list[PolicyRule]:
    """Parse every "security-shaped" rulebase under one `<*-rulebase>` element.

    Drives off `RuleType` (whose values are the container tags), so a new
    rulebase needs only a new enum member. Reads the shared reference surface
    (source/destination/service/tag); `service` stays empty when the rulebase
    omits it (application-override). PBF additionally captures an object-named
    forwarding next-hop (`action/forward/nexthop/fqdn`); a literal `ip-address`
    next-hop names no object and is skipped.
    """
    out: list[PolicyRule] = []
    for rt in RuleType:
        for entry in parent.findall(f"./{rt.value}/rules/entry"):
            name = entry.get("name")
            if not name:
                continue
            nexthop = _text(entry, "action/forward/nexthop/fqdn") if rt is RuleType.PBF else None
            out.append(
                PolicyRule(
                    name=name,
                    location=loc,
                    rulebase=rb,
                    rule_type=rt,
                    source=_member_list_or_any(entry, "source"),
                    destination=_member_list_or_any(entry, "destination"),
                    service=_members(entry, "service"),
                    nexthop=nexthop,
                    disabled=_text(entry, "disabled") == "yes",
                    tags=_members(entry, "tag"),
                )
            )
    return out


def _collect(snap: Snapshot, parent: ET.Element, loc: Location) -> None:
    snap.addresses.extend(_parse_addresses(parent, loc))
    snap.address_groups.extend(_parse_address_groups(parent, loc))
    snap.services.extend(_parse_services(parent, loc))
    snap.service_groups.extend(_parse_service_groups(parent, loc))
    snap.tags.extend(_parse_tags(parent, loc))
    for rb_tag, rb in (("pre-rulebase", Rulebase.PRE), ("post-rulebase", Rulebase.POST)):
        rb_el = parent.find(rb_tag)
        if rb_el is not None:
            snap.security_rules.extend(_parse_security_rules(rb_el, loc, rb))
            snap.nat_rules.extend(_parse_nat_rules(rb_el, loc, rb))
            snap.policy_rules.extend(_parse_policy_rules(rb_el, loc, rb))


def _find_config_root(root: ET.Element) -> ET.Element:
    """Locate the `<config>` element whether the doc is a bare config or an
    API `<response><result><config>` envelope.
    """
    if root.tag == "config":
        return root
    found = root.find(".//config")
    return found if found is not None else root


def _editable_device_groups(root: ET.Element) -> list[ET.Element]:
    """The user-editable device-group entries (those carrying objects/rules).

    Scoped to `/config/devices/.../device-group` so the read-only hierarchy
    mirror under `/config/readonly` is never parsed as a second set of objects.
    Falls back to a broad search for hand-trimmed fixtures that omit the
    `<devices>` wrapper but also have no `<readonly>` block.
    """
    entries = root.findall("./devices/entry/device-group/entry")
    if entries:
        return entries
    if root.find("readonly") is None:
        return [e for e in root.findall(".//device-group/entry") if e.get("name")]
    return entries


def _parse_dg_hierarchy(root: ET.Element) -> dict[str, str]:
    """Read child→parent links from the `<readonly>` device-group mirror.

    Panorama records nested device-group parentage as
    `/config/readonly/devices/entry/device-group/entry/parent-dg`. A device-group
    without a `parent-dg` (or absent here entirely) is a direct child of
    `shared`.
    """
    parents: dict[str, str] = {}
    for entry in root.findall("./readonly/devices/entry/device-group/entry"):
        name = entry.get("name")
        parent = entry.findtext("parent-dg")
        if name and parent and parent.strip():
            parents[name] = parent.strip()
    return parents


def parse_config(xml_text: str) -> Snapshot:
    """Parse a Panorama config XML string into a `Snapshot`."""
    root = _find_config_root(_safe_fromstring(xml_text))
    snap = Snapshot()

    shared = root.find("shared")
    if shared is not None:
        _collect(snap, shared, Location.shared())

    for dg in _editable_device_groups(root):
        name = dg.get("name")
        if not name:
            continue
        snap.device_groups.append(name)
        _collect(snap, dg, Location.dg(name))

    # Only keep parent links for device-groups that actually carry config, so a
    # stale readonly entry can't invent a phantom DG.
    known = set(snap.device_groups)
    snap.device_group_parents = {
        child: parent
        for child, parent in _parse_dg_hierarchy(root).items()
        if child in known and parent in known
    }

    return snap


def parse_config_file(path: str | Path) -> Snapshot:
    return parse_config(Path(path).read_text(encoding="utf-8"))
