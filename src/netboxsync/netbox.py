"""Read-only NetBox access (pynetbox) and the device-file check (D4).

Diode creates any object it is given a name for, so a typo in a device file
would silently become a new site, rack or role. `check_device_file` looks up
every reference object first: one that's missing is an error unless the
device file lists its kind under `create:`. A name that differs from an
existing object only by case is always an error.

This module never writes to NetBox: all writes go through Diode (D1).
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import pynetbox

from netboxsync.config import Creatable, DeviceFile
from netboxsync.tls import ca_file_from_env, warn_if_overridden

NETBOX_URL_ENV = "NETBOX_URL"
NETBOX_TOKEN_ENV = "NETBOX_TOKEN"
#: CA (or self-signed certificate) for NetBox's HTTPS, when not publicly trusted.
NETBOX_CA_FILE_ENV = "NETBOX_CA_FILE"
#: Unique custom field on devices holding the chassis serial: how Diode finds
#: a device again after its hostname changes (D18). Created in NetBox by hand.
CHASSIS_SERIAL_FIELD = "chassis_serial"


class NetBoxError(Exception):
    """NetBox can't be reached, or isn't configured."""


@dataclass(frozen=True)
class Ref:
    """The parts of a NetBox object the check needs."""

    id: int
    name: str
    site_id: int | None = None
    location_id: int | None = None
    region_id: int | None = None


@dataclass(frozen=True)
class DeviceRecord:
    """A NetBox device, with its references by name (what the slices compare)."""

    id: int
    name: str
    serial: str
    chassis_serial: str | None
    status: str | None
    site_id: int | None
    site: str | None
    location: str | None
    rack_id: int | None
    rack: str | None
    position: float | None
    face: str | None
    role: str | None
    device_type_id: int | None
    device_type: str | None
    platform: str | None


@dataclass(frozen=True)
class DeviceTypeRecord:
    id: int
    model: str
    manufacturer: str | None
    u_height: float
    is_full_depth: bool


@dataclass(frozen=True)
class CustomFieldRecord:
    name: str
    object_types: frozenset[str]
    type: str | None
    unique: bool


class NetBoxReader(Protocol):
    def find(
        self, kind: Creatable, name: str, *, site_id: int | None = None, ignore_case: bool = False
    ) -> list[Ref]:
        """Objects of `kind` named `name` (in site `site_id`, if given)."""
        ...

    def status(self) -> dict[str, Any]:
        """NetBox's /api/status/ document."""
        ...

    def devices(
        self,
        *,
        name: str | None = None,
        site_id: int | None = None,
        rack_id: int | None = None,
        chassis_serial: str | None = None,
    ) -> list[DeviceRecord]:
        """Devices matching every filter given; `name` ignores case."""
        ...

    def device_types(
        self, *, model: str | None = None, type_id: int | None = None
    ) -> list[DeviceTypeRecord]: ...

    def custom_field(self, name: str) -> CustomFieldRecord | None: ...


class PynetboxReader:
    def __init__(self, api: Any) -> None:
        self._api = api
        self._endpoints: dict[Creatable, Callable[[], Any]] = {
            Creatable.REGION: lambda: api.dcim.regions,
            Creatable.SITE: lambda: api.dcim.sites,
            Creatable.LOCATION: lambda: api.dcim.locations,
            Creatable.RACK: lambda: api.dcim.racks,
            Creatable.ROLE: lambda: api.dcim.device_roles,
        }

    @classmethod
    def from_env(cls) -> PynetboxReader:
        url = os.environ.get(NETBOX_URL_ENV)
        token = os.environ.get(NETBOX_TOKEN_ENV)
        if not url or not token:
            raise NetBoxError(f"set {NETBOX_URL_ENV} and {NETBOX_TOKEN_ENV}")
        api = pynetbox.api(url, token=token)
        try:
            ca_file = ca_file_from_env(NETBOX_CA_FILE_ENV)
        except FileNotFoundError as exc:
            raise NetBoxError(str(exc)) from exc
        if ca_file:
            api.http_session.verify = str(ca_file)
        warn_if_overridden(NETBOX_CA_FILE_ENV)
        return cls(api)

    def find(
        self, kind: Creatable, name: str, *, site_id: int | None = None, ignore_case: bool = False
    ) -> list[Ref]:
        filters: dict[str, Any] = {"name__ie" if ignore_case else "name": name}
        if site_id is not None:
            filters["site_id"] = site_id
        try:
            records = list(self._endpoints[kind]().filter(**filters))
        except pynetbox.RequestError as exc:
            raise NetBoxError(f"NetBox lookup of {kind.value} {name!r} failed: {exc}") from exc
        return [_ref(r.serialize()) for r in records]

    def status(self) -> dict[str, Any]:
        try:
            return dict(self._api.status())
        except pynetbox.RequestError as exc:
            raise NetBoxError(f"NetBox status failed: {exc}") from exc

    def devices(
        self,
        *,
        name: str | None = None,
        site_id: int | None = None,
        rack_id: int | None = None,
        chassis_serial: str | None = None,
    ) -> list[DeviceRecord]:
        filters: dict[str, Any] = {
            key: value
            for key, value in {
                "name__ie": name,
                "site_id": site_id,
                "rack_id": rack_id,
                f"cf_{CHASSIS_SERIAL_FIELD}": chassis_serial,
            }.items()
            if value is not None
        }
        if not filters:
            raise ValueError("devices() needs at least one filter")
        records = self._list("devices", lambda: self._api.dcim.devices.filter(**filters))
        return [_device(dict(r)) for r in records]

    def device_types(
        self, *, model: str | None = None, type_id: int | None = None
    ) -> list[DeviceTypeRecord]:
        filters: dict[str, Any] = {}
        if model is not None:
            filters["model"] = model
        if type_id is not None:
            filters["id"] = type_id
        if not filters:
            raise ValueError("device_types() needs at least one filter")
        records = self._list("device types", lambda: self._api.dcim.device_types.filter(**filters))
        return [_device_type(dict(r)) for r in records]

    def custom_field(self, name: str) -> CustomFieldRecord | None:
        records = self._list(
            "custom fields", lambda: self._api.extras.custom_fields.filter(name=name)
        )
        for record in records:
            data = dict(record)
            if data.get("name") == name:
                return CustomFieldRecord(
                    name=name,
                    object_types=frozenset(data.get("object_types") or ()),
                    type=_value(data.get("type")),
                    unique=bool(data.get("unique")),
                )
        return None

    @staticmethod
    def _list(what: str, query: Callable[[], Any]) -> list[Any]:
        try:
            return list(query())
        except pynetbox.RequestError as exc:
            raise NetBoxError(f"NetBox lookup of {what} failed: {exc}") from exc


def _ref(data: dict[str, Any]) -> Ref:
    # serialize() turns nested objects into their IDs.
    return Ref(
        id=data["id"],
        name=data["name"],
        site_id=data.get("site"),
        location_id=data.get("location"),
        region_id=data.get("region"),
    )


def _value(choice: Any) -> Any:
    """A choice field's value: NetBox returns {"value": ..., "label": ...}."""
    return choice.get("value") if isinstance(choice, dict) else choice


def _nested(obj: Any, key: str = "name") -> Any:
    return obj.get(key) if isinstance(obj, dict) else None


def _device(data: dict[str, Any]) -> DeviceRecord:
    position = data.get("position")
    return DeviceRecord(
        id=data["id"],
        name=data.get("name") or "",
        serial=data.get("serial") or "",
        chassis_serial=(data.get("custom_fields") or {}).get(CHASSIS_SERIAL_FIELD),
        status=_value(data.get("status")),
        site_id=_nested(data.get("site"), "id"),
        site=_nested(data.get("site")),
        location=_nested(data.get("location")),
        rack_id=_nested(data.get("rack"), "id"),
        rack=_nested(data.get("rack")),
        position=float(position) if position is not None else None,
        face=_value(data.get("face")),
        role=_nested(data.get("role")),
        device_type_id=_nested(data.get("device_type"), "id"),
        device_type=_nested(data.get("device_type"), "model"),
        platform=_nested(data.get("platform")),
    )


def _device_type(data: dict[str, Any]) -> DeviceTypeRecord:
    return DeviceTypeRecord(
        id=data["id"],
        model=data["model"],
        manufacturer=_nested(data.get("manufacturer")),
        u_height=float(data.get("u_height", 1.0)),
        is_full_depth=bool(data.get("is_full_depth", True)),
    )


@dataclass
class CheckReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: What the ingest will create.
    creates: list[str] = field(default_factory=list)
    #: Existing objects the ingest will change, and how.
    updates: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class _Missing:
    """A reference object that doesn't exist and may be created."""


MISSING = _Missing()


def check_device_file(device_file: DeviceFile, reader: NetBoxReader) -> CheckReport:
    report = CheckReport()
    df = device_file

    def lookup(
        kind: Creatable, name: str, site: Ref | _Missing | None = None
    ) -> Ref | _Missing | None:
        """A Ref, MISSING (will be created), or None (an error was reported)."""
        where = ""
        if isinstance(site, _Missing):
            # Its site doesn't exist yet, so neither can it.
            if kind in df.create:
                report.creates.append(f"{kind.value} {name!r} (in new site {df.site!r})")
                return MISSING
            report.errors.append(
                f"{kind.value} {name!r}: its site {df.site!r} will be created, so it can't "
                f"exist yet; add '{kind.value}' to create"
            )
            return None
        site_id = site.id if site is not None else None
        if site is not None:
            where = f" in site {site.name!r}"

        found = reader.find(kind, name, site_id=site_id)
        if len(found) == 1:
            if kind in df.create:
                report.warnings.append(
                    f"{kind.value} {name!r} exists{where}; 'create' isn't needed for it"
                )
            return found[0]
        if len(found) > 1:
            report.errors.append(
                f"{kind.value} {name!r} matches {len(found)} objects{where}; can't tell which"
            )
            return None

        near = sorted({r.name for r in reader.find(kind, name, site_id=site_id, ignore_case=True)})
        if near:
            report.errors.append(
                f"{kind.value} {name!r} not found{where}, but NetBox has "
                f"{', '.join(repr(n) for n in near)}: names must match exactly"
            )
            return None
        if kind in df.create:
            report.creates.append(f"{kind.value} {name!r}{where}")
            return MISSING
        report.errors.append(
            f"{kind.value} {name!r} not found in NetBox{where}; fix the name, or add "
            f"'{kind.value}' to create if it's new"
        )
        return None

    region = lookup(Creatable.REGION, df.region) if df.region else None
    site = lookup(Creatable.SITE, df.site)

    if isinstance(site, Ref) and df.region:
        if isinstance(region, Ref) and site.region_id != region.id:
            report.errors.append(
                f"site {df.site!r} exists but isn't in region {df.region!r}; "
                "fix the region, or leave it out"
            )
        elif region is MISSING:
            report.errors.append(
                f"site {df.site!r} exists, so its region can't be the new region {df.region!r}"
            )

    location = None
    if df.location and site is not None:
        location = lookup(Creatable.LOCATION, df.location, site)

    if df.rack and site is not None:
        rack = lookup(Creatable.RACK, df.rack, site)
        if isinstance(rack, Ref) and df.location:
            if isinstance(location, Ref) and rack.location_id != location.id:
                report.errors.append(
                    f"rack {df.rack!r} exists but isn't in location {df.location!r}"
                )
            elif location is MISSING:
                report.errors.append(
                    f"rack {df.rack!r} exists, so its location can't be the new "
                    f"location {df.location!r}"
                )

    lookup(Creatable.ROLE, df.role)

    return report
