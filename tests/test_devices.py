"""Slice 1: building the device entity, the device checks, and the read-back."""

from dataclasses import replace
from typing import Any

import pytest
from netsdk.cisco.ios import IOSFacts

from netboxsync import devices
from netboxsync.config import DeviceTypeOverride, DeviceTypesFile
from netboxsync.inputs import IngestInput
from netboxsync.netbox import CheckReport, DeviceRecord, DeviceTypeRecord
from netboxsync.snapshot import GetterResult, GetterStatus, Snapshot, dump_result
from tests.conftest import device_file
from tests.test_netbox_check import NETBOX, FakeNetBox
from tests.test_snapshot import make_snapshot

SERIAL = "FDO12345678"
HOSTNAME = "sw1.example.net"
MODEL = "N9K-C93180YC-EX"


def make_input(
    types: dict[str, DeviceTypeOverride] | None = None, snapshot: Snapshot | None = None, **df: Any
) -> IngestInput:
    return IngestInput(
        device_file(**df),
        snapshot or make_snapshot(),
        DeviceTypesFile(device_types=types or {}),
    )


def record(**overrides: Any) -> DeviceRecord:
    base: dict[str, Any] = {
        "id": 100,
        "name": HOSTNAME,
        "serial": SERIAL,
        "chassis_serial": SERIAL,
        "status": "active",
        "site_id": 10,
        "site": "NYC-DC01",
        "location": None,
        "rack_id": None,
        "rack": None,
        "position": None,
        "face": None,
        "role": "core-switch",
        "device_type_id": 50,
        "device_type": MODEL,
        "platform": "Cisco NX-OS 9.3(9)",
    }
    base.update(overrides)
    return DeviceRecord(**base)


def netbox(
    devices_: list[DeviceRecord] | None = None,
    types: list[DeviceTypeRecord] | None = None,
    **kw: Any,
) -> FakeNetBox:
    return FakeNetBox(NETBOX.objects, devices=devices_, device_types=types, **kw)


def run_check(inp: IngestInput, reader: FakeNetBox) -> CheckReport:
    report = CheckReport()
    devices.check(inp, reader, report)
    return report


# -- building --------------------------------------------------------------


def test_device_entity() -> None:
    [entity] = devices.build(make_input(rack="A-12", location="Hall-A", position=30, face="front"))
    d = entity.device
    assert d.name == HOSTNAME
    assert d.serial == SERIAL
    assert d.custom_fields["chassis_serial"].text == SERIAL
    assert [t.name for t in d.tags] == ["netboxsync"]
    assert d.status == "active"
    assert d.role.name == "core-switch"
    assert d.site.name == "NYC-DC01"
    assert d.location.name == "Hall-A"
    assert (d.rack.name, d.rack.site.name, d.rack.location.name) == ("A-12", "NYC-DC01", "Hall-A")
    assert (d.position, d.face) == (30, "front")
    assert d.device_type.model == d.device_type.part_number == MODEL
    assert d.device_type.manufacturer.name == "Cisco"
    assert d.platform.name == "Cisco NX-OS 9.3(9)"
    assert d.platform.manufacturer.name == "Cisco"
    assert d.platform.parent.name == "Cisco NX-OS"


def test_unset_placement_and_physical_facts_are_not_sent() -> None:
    # Not sending a field leaves NetBox's value alone.
    [entity] = devices.build(make_input())
    d = entity.device
    for name in ("location", "rack", "position", "face"):
        assert not d.HasField(name), name
    assert not d.site.HasField("region")
    for name in ("u_height", "is_full_depth", "airflow"):
        assert not d.device_type.HasField(name), name


def test_device_types_file_fills_in_the_physical_facts() -> None:
    types = {MODEL: DeviceTypeOverride(u_height=2, is_full_depth=False, airflow="rear-to-front")}
    [entity] = devices.build(make_input(types, region="US-East"))
    t = entity.device.device_type
    assert (t.u_height, t.is_full_depth, t.airflow) == (2, False, "rear-to-front")
    assert entity.device.site.region.name == "US-East"


def ios_snapshot() -> Snapshot:
    facts = IOSFacts(
        hostname="rtr1",
        vendor="Cisco",
        model="ISR4451-X/K9",
        serial_number="FGL1",
        os_version="17.9.4a",
    )
    return Snapshot(
        device_label="sw1",
        driver="cisco-ios",
        sdk_version="0.1.0",
        netboxsync_version="0.1.0",
        collected_at=make_snapshot().collected_at,
        getters={
            "facts": GetterResult(
                status=GetterStatus.OK, data=dump_result("cisco-ios", "facts", facts)
            )
        },
    )


def test_ios_xe_is_under_the_ios_family() -> None:
    [entity] = devices.build(make_input(snapshot=ios_snapshot(), driver="cisco-ios"))
    assert entity.device.platform.name == "Cisco IOS 17.9.4a"
    assert entity.device.platform.parent.name == "Cisco IOS"


# -- custom field ------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ([], "doesn't exist"),
        ([replace(FakeNetBox({}).custom_fields[0], unique=False)], "isn't unique"),
        (
            [replace(FakeNetBox({}).custom_fields[0], object_types=frozenset({"dcim.site"}))],
            "isn't on devices",
        ),
        ([replace(FakeNetBox({}).custom_fields[0], type="integer")], "not 'text'"),
    ],
)
def test_chassis_serial_field_must_be_set_up(fields: list[Any], message: str) -> None:
    report = run_check(make_input(), netbox(custom_fields=fields))
    assert len(report.errors) == 1
    assert message in report.errors[0]
    assert report.creates == []  # the other checks don't run


# -- identity ----------------------------------------------------------------


def test_new_device_is_created() -> None:
    report = run_check(make_input(), netbox())
    assert report.ok
    assert report.creates == [f"device {HOSTNAME!r} (chassis serial {SERIAL!r})"]


def test_same_device_is_updated() -> None:
    report = run_check(make_input(), netbox([record()]))
    assert report.ok
    assert report.creates == []
    assert report.updates == [f"device {HOSTNAME!r} (#100 {HOSTNAME!r} in site 'NYC-DC01')"]


def test_rename_updates_the_device_with_that_serial() -> None:
    report = run_check(make_input(), netbox([record(name="old-sw1")]))
    assert report.ok
    assert report.updates == [
        f"device {HOSTNAME!r} (#100 'old-sw1' in site 'NYC-DC01'): rename from 'old-sw1'"
    ]


def test_move_to_another_site() -> None:
    report = run_check(make_input(), netbox([record(site_id=11, site="SFO-DC01")]))
    assert report.ok
    assert "move from site 'SFO-DC01'" in report.updates[0]


def test_new_chassis_under_a_known_name() -> None:
    report = run_check(make_input(), netbox([record(chassis_serial="OLD1", serial="OLD1")]))
    assert report.ok
    assert "chassis serial changes from 'OLD1' to 'FDO12345678'" in report.updates[0]


def test_name_and_serial_on_different_devices_is_an_error() -> None:
    reader = netbox([record(chassis_serial="OLD1"), record(id=101, name="other")])
    report = run_check(make_input(), reader)
    assert not report.ok
    assert "#100" in report.errors[0]
    assert "#101" in report.errors[0]


def test_name_differing_only_by_case_with_another_serial_is_an_error() -> None:
    report = run_check(make_input(), netbox([record(name=HOSTNAME.upper(), chassis_serial="X")]))
    assert not report.ok
    assert "differs only by case" in report.errors[0]


def test_case_change_of_a_known_serial_is_a_rename() -> None:
    report = run_check(make_input(), netbox([record(name=HOSTNAME.upper())]))
    assert report.ok
    assert "rename from" in report.updates[0]


def test_new_site_has_no_devices_by_name() -> None:
    report = run_check(make_input(site="BOS-DC01", create=["site"]), netbox())
    assert report.creates == [f"device {HOSTNAME!r} (chassis serial {SERIAL!r})"]


# -- rack space --------------------------------------------------------------

RACK: dict[str, Any] = {"rack": "A-12", "location": "Hall-A", "face": "front"}
TYPES = [
    DeviceTypeRecord(50, MODEL, "Cisco", 1.0, True),
    DeviceTypeRecord(51, "BIG", "Cisco", 4.0, True),
    DeviceTypeRecord(52, "HALF", "Cisco", 1.0, False),
    DeviceTypeRecord(53, "PANEL", "Cisco", 0.0, False),
]


def in_rack(position: float, type_id: int = 50, face: str = "front", **kw: Any) -> DeviceRecord:
    defaults: dict[str, Any] = {
        "id": 200 + int(position * 2),
        "name": f"other-{position:g}",
        "chassis_serial": f"S{position:g}",
        "rack_id": 30,
        "rack": "A-12",
        "position": position,
        "face": face,
        "device_type_id": type_id,
    }
    defaults.update(kw)
    return record(**defaults)


def rack_check(position: float, others: list[DeviceRecord], **kw: Any) -> CheckReport:
    return run_check(make_input(position=position, **RACK, **kw), netbox(others, TYPES))


def test_free_units() -> None:
    assert rack_check(30, [in_rack(29), in_rack(31), in_rack(32)]).ok


def test_occupied_unit_is_an_error() -> None:
    report = rack_check(30, [in_rack(30)])
    assert not report.ok
    assert report.errors == [
        "rack 'A-12' U30 (1U, front) overlaps #260 'other-30' in site 'NYC-DC01' at U30 (1U, front)"
    ]


def test_tall_device_below_reaches_up() -> None:
    report = rack_check(30, [in_rack(27, type_id=51)])  # U27-30
    assert "at U27 (4U, front)" in report.errors[0]


def test_half_depth_devices_share_a_unit_on_opposite_faces() -> None:
    types = {MODEL: DeviceTypeOverride(is_full_depth=False)}
    assert rack_check(30, [in_rack(30, type_id=52, face="rear")], types=types).ok
    # Ours is full depth (NetBox's type says so): it takes both faces.
    assert not rack_check(30, [in_rack(30, type_id=52, face="rear")]).ok


def test_our_height_from_the_device_types_file() -> None:
    types = {MODEL: DeviceTypeOverride(u_height=2)}
    report = rack_check(30, [in_rack(31)], types=types)
    assert "U30 (2U, front)" in report.errors[0]


def test_our_height_from_netbox_when_the_file_has_none() -> None:
    tall = [*TYPES[1:], DeviceTypeRecord(50, MODEL, "Cisco", 3.0, True)]
    report = run_check(make_input(position=30, **RACK), netbox([in_rack(32)], tall))
    assert "U30 (3U, front)" in report.errors[0]


def test_zero_height_and_unpositioned_devices_take_no_space() -> None:
    assert rack_check(
        30, [in_rack(30, type_id=53), record(id=300, name="loose", chassis_serial="L", rack_id=30)]
    ).ok


def test_the_device_itself_is_not_in_the_way() -> None:
    assert rack_check(30, [in_rack(30, id=100, chassis_serial=SERIAL, name=HOSTNAME)]).ok


def test_new_rack_is_not_checked() -> None:
    report = run_check(
        make_input(rack="C-01", position=1, face="front", create=["rack"]), netbox([in_rack(1)])
    )
    assert report.ok


# -- verifying ---------------------------------------------------------------


def test_expected_leaves_out_what_isnt_sent() -> None:
    want = devices.expected(make_input())
    assert want == {
        "name": HOSTNAME,
        "serial": SERIAL,
        "chassis_serial": SERIAL,
        "status": "active",
        "site": "NYC-DC01",
        "role": "core-switch",
        "device_type": MODEL,
        "platform": "Cisco NX-OS 9.3(9)",
    }


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Appearing(FakeNetBox):
    """A NetBox where the device shows up after `polls` reads."""

    def __init__(self, polls: int, device: DeviceRecord) -> None:
        super().__init__({})
        self.polls, self.device = polls, device

    def devices(self, **kw: Any) -> list[DeviceRecord]:
        self.polls -= 1
        return [self.device] if self.polls < 0 else []


def test_verify_waits_for_diode() -> None:
    clock = Clock()
    reader = Appearing(2, record())
    result = devices.verify(make_input(), reader, sleep=clock.sleep, clock=clock)
    assert result.ok
    assert clock.now == 6.0


def test_verify_reports_differences_after_the_timeout() -> None:
    clock = Clock()
    reader = Appearing(0, record(platform="Cisco NX-OS 9.3(8)"))
    result = devices.verify(make_input(), reader, timeout=10, sleep=clock.sleep, clock=clock)
    assert not result.ok
    assert result.differences == {"platform": ("Cisco NX-OS 9.3(9)", "Cisco NX-OS 9.3(8)")}
    assert clock.now >= 10


def test_verify_device_never_appears() -> None:
    clock = Clock()
    result = devices.verify(
        make_input(), Appearing(1000, record()), timeout=9, sleep=clock.sleep, clock=clock
    )
    assert result.device is None
    assert not result.ok


def test_edited_serial_is_warned_about() -> None:
    report = run_check(make_input(), netbox([record(serial="TYPO1")]))
    assert report.ok
    assert report.warnings == [
        f"device {HOSTNAME!r}: serial in NetBox is 'TYPO1', the device reports {SERIAL!r}; "
        "it will be overwritten"
    ]


def test_chassis_swap_is_not_also_a_serial_warning() -> None:
    report = run_check(make_input(), netbox([record(chassis_serial="OLD1", serial="OLD1")]))
    assert report.warnings == []
