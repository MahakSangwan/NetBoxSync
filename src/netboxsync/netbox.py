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

NETBOX_URL_ENV = "NETBOX_URL"
NETBOX_TOKEN_ENV = "NETBOX_TOKEN"


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


class NetBoxReader(Protocol):
    def find(
        self, kind: Creatable, name: str, *, site_id: int | None = None, ignore_case: bool = False
    ) -> list[Ref]:
        """Objects of `kind` named `name` (in site `site_id`, if given)."""
        ...

    def status(self) -> dict[str, Any]:
        """NetBox's /api/status/ document."""
        ...


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
        # TLS: requests honours REQUESTS_CA_BUNDLE for a private CA.
        return cls(pynetbox.api(url, token=token))

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


def _ref(data: dict[str, Any]) -> Ref:
    # serialize() turns nested objects into their IDs.
    return Ref(
        id=data["id"],
        name=data["name"],
        site_id=data.get("site"),
        location_id=data.get("location"),
        region_id=data.get("region"),
    )


@dataclass
class CheckReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: What the ingest will create.
    creates: list[str] = field(default_factory=list)

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

    if df.role:
        lookup(Creatable.ROLE, df.role)

    return report
