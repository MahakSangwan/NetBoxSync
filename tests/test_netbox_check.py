from typing import Any

import pytest

from netboxsync.config import Creatable
from netboxsync.netbox import (
    CHASSIS_SERIAL_FIELD,
    CheckReport,
    CustomFieldRecord,
    DeviceRecord,
    DeviceTypeRecord,
    Ref,
    check_device_file,
)
from tests.conftest import device_file


class FakeNetBox:
    """In-memory NetBox: objects per kind; matches like NetBox's name filters."""

    def __init__(
        self,
        objects: dict[Creatable, list[Ref]],
        *,
        devices: list[DeviceRecord] | None = None,
        device_types: list[DeviceTypeRecord] | None = None,
        custom_fields: list[CustomFieldRecord] | None = None,
    ) -> None:
        self.objects = objects
        self.device_list = devices or []
        self.type_list = device_types or []
        self.custom_fields = custom_fields if custom_fields is not None else [CHASSIS_SERIAL]

    def find(
        self, kind: Creatable, name: str, *, site_id: int | None = None, ignore_case: bool = False
    ) -> list[Ref]:
        def same(a: str) -> bool:
            return a.lower() == name.lower() if ignore_case else a == name

        return [
            r
            for r in self.objects.get(kind, [])
            if same(r.name) and (site_id is None or r.site_id == site_id)
        ]

    def status(self) -> dict[str, Any]:
        return {}

    def devices(
        self,
        *,
        name: str | None = None,
        site_id: int | None = None,
        rack_id: int | None = None,
        chassis_serial: str | None = None,
    ) -> list[DeviceRecord]:
        return [
            d
            for d in self.device_list
            if (name is None or d.name.lower() == name.lower())
            and (site_id is None or d.site_id == site_id)
            and (rack_id is None or d.rack_id == rack_id)
            and (chassis_serial is None or d.chassis_serial == chassis_serial)
        ]

    def device_types(
        self, *, model: str | None = None, type_id: int | None = None
    ) -> list[DeviceTypeRecord]:
        return [
            t
            for t in self.type_list
            if (model is None or t.model == model) and (type_id is None or t.id == type_id)
        ]

    def custom_field(self, name: str) -> CustomFieldRecord | None:
        return next((cf for cf in self.custom_fields if cf.name == name), None)


CHASSIS_SERIAL = CustomFieldRecord(
    name=CHASSIS_SERIAL_FIELD, object_types=frozenset({"dcim.device"}), type="text", unique=True
)


NETBOX = FakeNetBox(
    {
        Creatable.REGION: [Ref(1, "US-East"), Ref(2, "US-West")],
        Creatable.SITE: [Ref(10, "NYC-DC01", region_id=1), Ref(11, "SFO-DC01", region_id=2)],
        Creatable.LOCATION: [Ref(20, "Hall-A", site_id=10), Ref(21, "Hall-B", site_id=10)],
        Creatable.RACK: [
            Ref(30, "A-12", site_id=10, location_id=20),
            Ref(31, "A-12", site_id=11),
            Ref(32, "B-01", site_id=10, location_id=21),
        ],
        Creatable.ROLE: [Ref(40, "core-switch")],
    }
)


def check(**overrides: object) -> CheckReport:
    return check_device_file(device_file(**overrides), NETBOX)


def test_everything_exists() -> None:
    report = check(
        region="US-East",
        location="Hall-A",
        rack="A-12",
        position=1,
        face="front",
        role="core-switch",
    )
    assert report.ok, report.errors
    assert report.creates == []
    assert report.warnings == []


def test_racks_are_looked_up_in_their_site() -> None:
    # SFO-DC01 also has an A-12; only NYC-DC01's counts.
    assert check(rack="A-12").ok


def test_missing_site_is_an_error_unless_creatable() -> None:
    report = check(site="BOS-DC01")
    assert not report.ok
    assert "add 'site' to create" in report.errors[0]

    report = check(site="BOS-DC01", create=["site"])
    assert report.ok
    assert report.creates == ["site 'BOS-DC01'"]


def test_case_difference_is_always_an_error() -> None:
    report = check(site="nyc-dc01", create=["site"])
    assert not report.ok
    assert "NetBox has 'NYC-DC01'" in report.errors[0]


def test_children_of_a_new_site_must_be_creatable_too() -> None:
    report = check(site="BOS-DC01", location="Hall-A", rack="R1", create=["site"])
    assert len(report.errors) == 2
    assert all("will be created" in e for e in report.errors)

    report = check(
        site="BOS-DC01", location="Hall-A", rack="R1", create=["site", "location", "rack"]
    )
    assert report.ok
    assert len(report.creates) == 3


def test_region_must_match_existing_site() -> None:
    report = check(region="US-West")
    assert report.errors == [
        "site 'NYC-DC01' exists but isn't in region 'US-West'; fix the region, or leave it out"
    ]
    report = check(region="EU-Central", create=["region"])
    assert "can't be the new region" in report.errors[0]


def test_new_site_in_new_region() -> None:
    report = check(site="FRA-DC01", region="EU-Central", create=["site", "region"])
    assert report.ok
    assert report.creates == ["region 'EU-Central'", "site 'FRA-DC01'"]


def test_rack_must_be_in_the_given_location() -> None:
    report = check(location="Hall-A", rack="B-01")
    assert report.errors == ["rack 'B-01' exists but isn't in location 'Hall-A'"]


def test_existing_rack_can_not_be_in_a_new_location() -> None:
    report = check(location="Hall-Z", rack="A-12", create=["location"])
    assert "can't be the new location" in report.errors[0]


def test_needless_create_is_a_warning() -> None:
    report = check(role="core-switch", create=["role"])
    assert report.ok
    assert report.warnings == ["role 'core-switch' exists; 'create' isn't needed for it"]


def test_ambiguous_match() -> None:
    netbox = FakeNetBox({Creatable.SITE: [Ref(10, "NYC-DC01"), Ref(12, "NYC-DC01")]})
    report = check_device_file(device_file(), netbox)
    assert "matches 2 objects" in report.errors[0]


@pytest.mark.parametrize("role", ["Core-Switch", "core-swtich"])
def test_role_typos(role: str) -> None:
    assert not check(role=role).ok
