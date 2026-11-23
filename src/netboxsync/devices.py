"""Slice 1: the device, with its site, location, rack, role, type and platform.

Building (D17): one Diode Device entity. Reference objects are sent by name
only (plus what places them: a rack's site and location, a site's region),
so an existing one is matched and left as it is; only the device, its type
and its platform are described in full. The device type's physical facts are
sent only when device_types.yaml has them, so NetBox's values stand otherwise.

Checking (D18, D19), before ingest:
- the `chassis_serial` custom field exists, on devices, and is unique;
- which existing device this one is: by name in its site, and by chassis
  serial anywhere. Diode matches the same way, name first: a different name
  with a known serial is a rename, a known name with a new serial is a
  chassis swap, and two different devices is an error;
- the rack units it will occupy are free.

Verifying (D20), after ingest: NetBox is read back until the device matches
what was sent, since Diode only queues requests.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from netboxlabs.diode.sdk.ingester import (
    CustomFieldValue,
    Device,
    DeviceRole,
    DeviceType,
    Entity,
    Location,
    Manufacturer,
    Platform,
    Rack,
    Site,
)

from netboxsync.config import Creatable
from netboxsync.diode import TAG
from netboxsync.inputs import IngestInput
from netboxsync.netbox import (
    CHASSIS_SERIAL_FIELD,
    CheckReport,
    DeviceRecord,
    DeviceTypeRecord,
    NetBoxReader,
)

log = logging.getLogger(__name__)

#: NetBox's defaults for a device type that doesn't say.
DEFAULT_U_HEIGHT = 1.0
DEFAULT_FULL_DEPTH = True


def platform_family(inp: IngestInput) -> str:
    """E.g. "Cisco NX-OS" (D10)."""
    return f"{inp.facts.vendor} {inp.driver.os_family}"


def platform_name(inp: IngestInput) -> str:
    """E.g. "Cisco NX-OS 10.4(5)" (D10)."""
    return f"{platform_family(inp)} {inp.facts.os_version}"


def build(inp: IngestInput) -> list[Any]:
    df, facts = inp.device_file, inp.facts
    manufacturer = Manufacturer(name=facts.vendor)
    override = inp.device_type_override
    location = Location(name=df.location, site=Site(name=df.site)) if df.location else None
    device = Device(
        name=facts.hostname,
        serial=facts.serial_number,
        status=df.status.value,
        site=Site(name=df.site, region=df.region),
        location=location,
        rack=Rack(name=df.rack, site=Site(name=df.site), location=location) if df.rack else None,
        position=df.position,
        face=df.face,
        role=DeviceRole(name=df.role),
        device_type=DeviceType(
            model=facts.model,
            part_number=facts.model,
            manufacturer=manufacturer,
            u_height=override.u_height if override else None,
            is_full_depth=override.is_full_depth if override else None,
            airflow=override.airflow if override else None,
        ),
        platform=Platform(
            name=platform_name(inp),
            manufacturer=manufacturer,
            parent=Platform(name=platform_family(inp), manufacturer=manufacturer),
        ),
        custom_fields={CHASSIS_SERIAL_FIELD: CustomFieldValue(text=facts.serial_number)},
        tags=[TAG],
    )
    return [Entity(device=device)]


# -- checking --------------------------------------------------------------


def check(inp: IngestInput, reader: NetBoxReader, report: CheckReport) -> None:
    """Add the device checks to a report from `check_device_file`."""
    if not _check_custom_field(reader, report):
        return
    site = _existing(reader, Creatable.SITE, inp.device_file.site)
    current = _identify(inp, reader, site, report)
    _check_rack_space(inp, reader, site, current, report)


def _check_custom_field(reader: NetBoxReader, report: CheckReport) -> bool:
    cf = reader.custom_field(CHASSIS_SERIAL_FIELD)
    problems = []
    if cf is None:
        problems.append("doesn't exist")
    else:
        if "dcim.device" not in cf.object_types:
            problems.append("isn't on devices")
        if cf.type != "text":
            problems.append(f"is of type {cf.type!r}, not 'text'")
        if not cf.unique:
            problems.append("isn't unique")
    if problems:
        report.errors.append(
            f"custom field {CHASSIS_SERIAL_FIELD!r} {', '.join(problems)}: create it in NetBox "
            "(Customization > Custom Fields: object type Device, type Text, Unique); "
            "without it a renamed device would be created a second time"
        )
        return False
    return True


def _existing(reader: NetBoxReader, kind: Creatable, name: str, site_id: int | None = None) -> Any:
    found = reader.find(kind, name, site_id=site_id)
    return found[0] if len(found) == 1 else None


def _identify(
    inp: IngestInput, reader: NetBoxReader, site: Any, report: CheckReport
) -> DeviceRecord | None:
    """The existing device this one will update, or None if it will be created."""
    hostname, serial = inp.facts.hostname, inp.facts.serial_number
    by_name = reader.devices(name=hostname, site_id=site.id) if site is not None else []
    by_serial = reader.devices(chassis_serial=serial)

    if len(by_name) > 1 or len(by_serial) > 1:
        report.errors.append(
            f"device {hostname!r} / chassis serial {serial!r} matches several devices: "
            + ", ".join(_describe(d) for d in [*by_name, *by_serial])
        )
        return None
    named = by_name[0] if by_name else None
    serialed = by_serial[0] if by_serial else None

    if named is not None and serialed is not None and named.id != serialed.id:
        report.errors.append(
            f"device {hostname!r} exists as {_describe(named)}, but chassis serial {serial!r} "
            f"belongs to {_describe(serialed)}; resolve it in NetBox (delete or fix one)"
        )
        return None
    if serialed is not None:
        changes = []
        if serialed.name != hostname:
            changes.append(f"rename from {serialed.name!r}")
        if serialed.site != inp.device_file.site:
            changes.append(f"move from site {serialed.site!r}")
        report.updates.append(
            f"device {hostname!r} ({_describe(serialed)})"
            + (f": {', '.join(changes)}" if changes else "")
        )
        if serialed.serial != serial:
            # chassis_serial is right, so `serial` was edited in NetBox (D21).
            report.warnings.append(
                f"device {hostname!r}: serial in NetBox is {serialed.serial!r}, the device "
                f"reports {serial!r}; it will be overwritten"
            )
        return serialed
    if named is not None:
        if named.name != hostname:
            # Found only by a case-insensitive name: Diode wouldn't match it,
            # and NetBox would refuse a second device with that name.
            report.errors.append(
                f"device {hostname!r} not found, but {_describe(named)} differs only by case "
                f"and has another chassis serial ({named.chassis_serial!r}); rename it in NetBox"
            )
            return None
        report.updates.append(
            f"device {hostname!r} ({_describe(named)}): chassis serial changes from "
            f"{named.chassis_serial!r} to {serial!r} (chassis replaced?)"
        )
        return named
    report.creates.append(f"device {hostname!r} (chassis serial {serial!r})")
    return None


def _describe(device: DeviceRecord) -> str:
    return f"#{device.id} {device.name!r} in site {device.site!r}"


@dataclass(frozen=True)
class _Size:
    u_height: float
    is_full_depth: bool


def _our_size(inp: IngestInput, reader: NetBoxReader) -> _Size:
    """device_types.yaml, else the type already in NetBox, else NetBox's defaults."""
    override = inp.device_type_override
    existing = [
        t for t in reader.device_types(model=inp.facts.model) if t.manufacturer == inp.facts.vendor
    ]
    known = existing[0] if len(existing) == 1 else None

    def pick(ours: Any, theirs: Any, default: Any) -> Any:
        if ours is not None:
            return ours
        return theirs if theirs is not None else default

    return _Size(
        u_height=pick(
            override.u_height if override else None,
            known.u_height if known else None,
            DEFAULT_U_HEIGHT,
        ),
        is_full_depth=pick(
            override.is_full_depth if override else None,
            known.is_full_depth if known else None,
            DEFAULT_FULL_DEPTH,
        ),
    )


def _check_rack_space(
    inp: IngestInput,
    reader: NetBoxReader,
    site: Any,
    current: DeviceRecord | None,
    report: CheckReport,
) -> None:
    df = inp.device_file
    if df.rack is None or df.position is None or df.face is None or site is None:
        return
    rack = _existing(reader, Creatable.RACK, df.rack, site.id)
    if rack is None:
        return  # a new rack is empty
    ours = _our_size(inp, reader)
    if ours.u_height == 0:
        return
    our_units = (df.position, df.position + ours.u_height)

    types: dict[int, DeviceTypeRecord | None] = {}

    def size_of(device: DeviceRecord) -> _Size:
        if device.device_type_id is None:
            return _Size(DEFAULT_U_HEIGHT, DEFAULT_FULL_DEPTH)
        if device.device_type_id not in types:
            found = reader.device_types(type_id=device.device_type_id)
            types[device.device_type_id] = found[0] if found else None
        t = types[device.device_type_id]
        return _Size(t.u_height, t.is_full_depth) if t else _Size(DEFAULT_U_HEIGHT, True)

    for other in reader.devices(rack_id=rack.id):
        if current is not None and other.id == current.id:
            continue
        if other.position is None:
            continue
        theirs = size_of(other)
        if theirs.u_height == 0:
            continue
        their_units = (other.position, other.position + theirs.u_height)
        overlaps = our_units[0] < their_units[1] and their_units[0] < our_units[1]
        same_side = ours.is_full_depth or theirs.is_full_depth or other.face == df.face
        if overlaps and same_side:
            report.errors.append(
                f"rack {df.rack!r} {_units(df.position, ours.u_height, df.face)} overlaps "
                f"{_describe(other)} at {_units(other.position, theirs.u_height, other.face)}"
            )


def _units(position: float, u_height: float, face: str | None) -> str:
    return f"U{position:g} ({u_height:g}U, {face or 'no face'})"


# -- verifying -------------------------------------------------------------


def expected(inp: IngestInput) -> dict[str, Any]:
    """What NetBox should show for the device once Diode has applied the ingest.

    Fields the device file leaves out (location, rack, position, face) aren't
    sent, so NetBox keeps whatever it had; they aren't compared either.
    """
    df, facts = inp.device_file, inp.facts
    fields: dict[str, Any] = {
        "name": facts.hostname,
        "serial": facts.serial_number,
        "chassis_serial": facts.serial_number,
        "status": df.status.value,
        "site": df.site,
        "location": df.location,
        "rack": df.rack,
        "position": df.position,
        "face": df.face,
        "role": df.role,
        "device_type": facts.model,
        "platform": platform_name(inp),
    }
    return {k: v for k, v in fields.items() if v is not None}


@dataclass(frozen=True)
class VerifyResult:
    device: DeviceRecord | None
    #: field -> (expected, in NetBox); empty when everything matches.
    differences: dict[str, tuple[Any, Any]]

    @property
    def ok(self) -> bool:
        return self.device is not None and not self.differences


def verify(
    inp: IngestInput,
    reader: NetBoxReader,
    *,
    timeout: float = 60.0,
    interval: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> VerifyResult:
    """Read the device back (by chassis serial) until it matches, or `timeout`."""
    want = expected(inp)
    deadline = clock() + timeout
    while True:
        result = _compare(want, reader.devices(chassis_serial=inp.facts.serial_number))
        if result.ok or clock() >= deadline:
            return result
        log.debug("device not applied yet: %s", result.differences or "not found")
        sleep(interval)


def _compare(want: dict[str, Any], found: list[DeviceRecord]) -> VerifyResult:
    if len(found) != 1:
        return VerifyResult(None, {})
    device = found[0]
    differences = {
        key: (value, getattr(device, key))
        for key, value in want.items()
        if getattr(device, key) != value
    }
    return VerifyResult(device, differences)
