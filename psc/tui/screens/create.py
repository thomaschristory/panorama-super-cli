"""Create spoke: author a new object (parity with `psc set <kind>`).

The screen collects a KIND (Select), a LOCATION (Select over shared + every
device-group), and every possible field as an Input — only the fields relevant
to the chosen kind are read per-kind. `plan_create` is the framework-free glue:
it mirrors the CLI's field->planner mapping (`psc/cli/set_cmds.py`) by calling
`psc.core.crud` directly, so crud's validation (name/value/port/color rules) and
its `ChangeSet.blockers` (cross-kind namespace collision, type/mode/protocol
change) are reused verbatim. A blocked plan is never staged (the hard gate).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Input, Select, Static

from psc.core import crud
from psc.core.changeset import ChangeSet
from psc.core.models import AddressType, Location
from psc.output.errors import ErrorType, PscError
from psc.tui.session import WorkbenchSession
from psc.tui.widgets.review import can_apply

if TYPE_CHECKING:
    from psc.tui.app import WorkbenchApp

# The kinds `psc set` can author, in the order they appear in the picker.
CREATE_KINDS: tuple[str, ...] = (
    "address",
    "address-group",
    "service",
    "service-group",
    "tag",
)

# Predefined value sets get a dropdown instead of a free-text Input — you can't
# type an invalid one. `crud` remains the validator; these only drive the UI.
SERVICE_PROTOCOLS: tuple[str, ...] = ("tcp", "udp")
TAG_COLORS: tuple[str, ...] = tuple(f"color{i}" for i in range(1, 43))  # color1..color42

# Every optional (kind-specific) field widget, by its id suffix. `name` /
# `location` / `kind` apply to every kind and are always shown.
_OPTIONAL_FIELDS: tuple[str, ...] = (
    "type",
    "value",
    "protocol",
    "dest-port",
    "source-port",
    "members",
    "filter",
    "color",
    "comments",
    "description",
    "tags",
)

# Which optional fields each kind actually uses (mirrors the per-kind reads in
# `plan_create`). The create form shows only these for the selected kind, so it
# is guided rather than a wall of mostly-irrelevant inputs.
_FIELDS_BY_KIND: dict[str, tuple[str, ...]] = {
    "address": ("type", "value", "description", "tags"),
    "address-group": ("members", "filter", "description", "tags"),
    "service": ("protocol", "dest-port", "source-port", "description", "tags"),
    "service-group": ("members", "tags"),
    "tag": ("color", "comments"),
}


def location_options(session: WorkbenchSession) -> list[str]:
    """Locations an object may be created in: 'shared' plus every device-group.

    Shared first, then device-groups alphabetically, so the drop-down order is
    stable across renders (mirrors `move.move_destinations`)."""
    return ["shared", *sorted(session.working_snapshot.device_groups)]


def _location(name: str) -> Location:
    return Location.shared() if name == "shared" else Location.dg(name)


def _split(raw: str) -> list[str]:
    """Comma-separated field -> trimmed, non-empty tokens (members, tags)."""
    return [tok.strip() for tok in raw.split(",") if tok.strip()]


def plan_create(
    session: WorkbenchSession,
    kind: str,
    fields: dict[str, str],
    location: str,
) -> ChangeSet:
    """Build a create `ChangeSet` for `kind` from `fields`, via `crud.plan_*`.

    `fields` carries the raw string inputs keyed like the CLI options (name,
    type, value, protocol, dest-port, source-port, members, filter, description,
    comments, color, tags). Empty strings mean "unset" (None). Validation and
    blockers come straight from crud — this only maps fields to the planner.
    """
    loc = _location(location)
    name = fields.get("name", "").strip()
    tags = _split(fields.get("tags", ""))
    desc = fields.get("description", "").strip() or None

    if kind == "address":
        type_ = fields.get("type", "").strip()
        addr_type = AddressType(type_) if type_ in AddressType._value2member_map_ else None
        if addr_type is None:
            raise PscError(
                f"type '{type_}' is invalid (ip-netmask | ip-range | ip-wildcard | fqdn)",
                ErrorType.VALIDATION,
            )
        return crud.plan_address(
            session.working_snapshot,
            name,
            addr_type,
            fields.get("value", "").strip(),
            description=desc,
            tags=tags,
            location=loc,
        )
    if kind == "address-group":
        members = _split(fields.get("members", ""))
        filter_ = fields.get("filter", "").strip() or None
        return crud.plan_address_group(
            session.working_snapshot,
            name,
            static_members=members or None,
            dynamic_filter=filter_,
            description=desc,
            tags=tags,
            location=loc,
        )
    if kind == "service":
        return crud.plan_service(
            session.working_snapshot,
            name,
            fields.get("protocol", "").strip(),
            destination_port=fields.get("dest-port", "").strip() or None,
            source_port=fields.get("source-port", "").strip() or None,
            description=desc,
            tags=tags,
            location=loc,
        )
    if kind == "service-group":
        return crud.plan_service_group(
            session.working_snapshot,
            name,
            _split(fields.get("members", "")),
            tags=tags,
            location=loc,
        )
    if kind == "tag":
        return crud.plan_tag(
            session.working_snapshot,
            name,
            color=fields.get("color", "").strip() or None,
            comments=fields.get("comments", "").strip() or None,
            location=loc,
        )
    raise PscError(f"unknown object kind '{kind}'", ErrorType.VALIDATION)


class CreateScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+y", "stage", "create"),
        ("escape", "app.pop_screen", "cancel"),
    ]

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._locations = location_options(session)

    def compose(self) -> ComposeResult:
        # One screen, all fields present. The kind picker names which fields
        # apply; irrelevant ones are simply ignored per-kind at plan time. This
        # is deliberately static (no dynamic re-composition) so it stays testable.
        yield Select(
            [(k, k) for k in CREATE_KINDS],
            value="address",
            allow_blank=False,
            id="create-kind",
        )
        yield Select(
            [(loc, loc) for loc in self._locations],
            value="shared",
            allow_blank=False,
            id="create-location",
        )
        yield Input(placeholder="name", id="create-name")
        # type (address) — predefined AddressType values, so a dropdown.
        yield Select(
            [(t.value, t.value) for t in AddressType],
            value=AddressType.IP_NETMASK.value,
            allow_blank=False,
            id="create-type",
        )
        yield Input(placeholder="value (address)", id="create-value")
        # protocol (service) — predefined tcp/udp, so a dropdown.
        yield Select(
            [(p, p) for p in SERVICE_PROTOCOLS],
            value="tcp",
            allow_blank=False,
            id="create-protocol",
        )
        yield Input(placeholder="dest-port (service)", id="create-dest-port")
        yield Input(placeholder="source-port (service, optional)", id="create-source-port")
        yield Input(
            placeholder="members (address-group/service-group, comma-separated)",
            id="create-members",
        )
        yield Input(placeholder="filter (address-group, dynamic)", id="create-filter")
        # color (tag) — predefined color1..color42 and optional, so a dropdown
        # that can be left blank.
        yield Select(
            [(c, c) for c in TAG_COLORS],
            prompt="color (tag, optional)",
            allow_blank=True,
            id="create-color",
        )
        yield Input(placeholder="comments (tag)", id="create-comments")
        yield Input(placeholder="description (optional)", id="create-description")
        yield Input(placeholder="tags (comma-separated, optional)", id="create-tags")
        yield Static("[ctrl+y] create  [esc] cancel", id="create-plan")
        yield Footer()

    def on_mount(self) -> None:
        self._sync_field_visibility()
        self.query_one("#create-name", Input).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        # Re-show only the fields the newly-chosen kind uses. Other Select
        # widgets (location/type/protocol/color) don't drive visibility.
        if event.select.id == "create-kind":
            self._sync_field_visibility()

    def _sync_field_visibility(self) -> None:
        visible = set(_FIELDS_BY_KIND.get(self._kind(), ()))
        for key in _OPTIONAL_FIELDS:
            self.query_one(f"#create-{key}").display = key in visible

    def _kind(self) -> str:
        value = self.query_one("#create-kind", Select).value
        return str(value) if value is not Select.BLANK else "address"

    def _location_name(self) -> str:
        value = self.query_one("#create-location", Select).value
        return str(value) if value is not Select.BLANK else "shared"

    def _fields(self) -> dict[str, str]:
        def val(widget_id: str) -> str:
            return self.query_one(widget_id, Input).value

        def sel(widget_id: str) -> str:
            # A dropdown left blank (optional color) reads as "" like an empty Input.
            widget = self.query_one(widget_id, Select)
            return "" if widget.is_blank() else str(widget.value)

        return {
            "name": val("#create-name"),
            "type": sel("#create-type"),
            "value": val("#create-value"),
            "protocol": sel("#create-protocol"),
            "dest-port": val("#create-dest-port"),
            "source-port": val("#create-source-port"),
            "members": val("#create-members"),
            "filter": val("#create-filter"),
            "color": sel("#create-color"),
            "comments": val("#create-comments"),
            "description": val("#create-description"),
            "tags": val("#create-tags"),
        }

    def _show_blockers(self, cs: ChangeSet) -> None:
        blockers = "; ".join(cs.blockers) or "invalid"
        self.query_one("#create-plan", Static).update(f"[red]BLOCKED: {blockers}[/red]")

    def action_stage(self) -> None:
        hub = cast("WorkbenchApp", self.app)
        kind = self._kind()
        try:
            cs = plan_create(self.session, kind, self._fields(), self._location_name())
        except PscError as exc:
            # crud raised on a hard validation error (bad name/port/color, bad
            # XOR on group members): surface it on-screen, do not stage.
            self.query_one("#create-plan", Static).update(f"[red]{exc}[/red]")
            self.app.bell()
            return
        if not can_apply(cs):
            # A blocked plan (cross-kind collision, type/mode/protocol change):
            # show the blocker(s), bell, stay on-screen — never stage.
            self._show_blockers(cs)
            self.app.bell()
            return
        try:
            self.session.stage(f"create {kind} {self._fields()['name'].strip()}", cs)
        except PscError as exc:
            # stage() compounds via apply_changeset/parse_config, which can raise
            # on an XML-level failure — surface it rather than crash the app.
            self.query_one("#create-plan", Static).update(f"[red]{exc}[/red]")
            self.app.bell()
            return
        hub._refresh_selection_view()
        self.app.pop_screen()
