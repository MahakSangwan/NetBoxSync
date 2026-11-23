import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from netboxsync.snapshot import (
    GetterResult,
    GetterStatus,
    Snapshot,
    SnapshotError,
    dump_result,
    latest_snapshot_path,
    read_snapshot,
    write_snapshot,
)
from tests.conftest import ip_addresses, nxos_facts, nxos_interfaces

WHEN = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)


def make_snapshot(**getters: GetterResult) -> Snapshot:
    base = {
        "facts": GetterResult(
            status=GetterStatus.OK, data=dump_result("cisco-nxos", "facts", nxos_facts())
        ),
        "interfaces": GetterResult(
            status=GetterStatus.OK, data=dump_result("cisco-nxos", "interfaces", nxos_interfaces())
        ),
        "ip_addresses": GetterResult(
            status=GetterStatus.OK, data=dump_result("cisco-nxos", "ip_addresses", ip_addresses())
        ),
        "vpc_status": GetterResult(
            status=GetterStatus.NOT_SUPPORTED, error_type="NotSupportedError", error="vPC off"
        ),
    }
    base.update(getters)
    return Snapshot(
        device_label="sw1",
        driver="cisco-nxos",
        sdk_version="0.1.0",
        netboxsync_version="0.1.0",
        collected_at=WHEN,
        session_log="session_logs/psm.example.net_20261123T120000Z.log",
        getters=base,
    )


def test_round_trip_gives_back_equal_sdk_models(tmp_path: Path) -> None:
    path = write_snapshot(make_snapshot(), tmp_path)
    assert path == tmp_path / "sw1" / "20261123T120000Z.json"
    back = read_snapshot(path)
    assert back.get("facts") == nxos_facts()
    assert back.get("interfaces") == nxos_interfaces()
    assert back.get("ip_addresses") == ip_addresses()
    assert type(back.get("facts")).__name__ == "NXOSFacts"


def test_file_is_plain_json(tmp_path: Path) -> None:
    data = json.loads(write_snapshot(make_snapshot(), tmp_path).read_text())
    assert data["getters"]["facts"]["data"]["uptime"] == "P3DT5M"
    assert data["getters"]["ip_addresses"]["data"][0]["address"] == "10.0.0.5/24"


def test_never_overwrites(tmp_path: Path) -> None:
    first = write_snapshot(make_snapshot(), tmp_path)
    second = write_snapshot(make_snapshot(), tmp_path)
    third = write_snapshot(make_snapshot(), tmp_path)
    assert first != second != third
    assert second.name == "20261123T120000Z-1.json"
    assert latest_snapshot_path(tmp_path, "sw1") == third


def test_latest_orders_by_time_then_counter(tmp_path: Path) -> None:
    d = tmp_path / "sw1"
    d.mkdir()
    for name in ["20261123T120000Z-9", "20261123T120000Z-10", "20261122T235959Z"]:
        (d / f"{name}.json").write_text("{}")
    assert latest_snapshot_path(tmp_path, "sw1").name == "20261123T120000Z-10.json"


def test_latest_without_snapshots(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError, match="netboxsync collect"):
        latest_snapshot_path(tmp_path, "sw1")


def test_get_refuses_failed_and_missing_getters() -> None:
    snap = make_snapshot()
    assert snap.status("vpc_status") is GetterStatus.NOT_SUPPORTED
    assert snap.status("fexes") is None
    with pytest.raises(SnapshotError, match="not_supported"):
        snap.get("vpc_status")
    with pytest.raises(SnapshotError, match="no 'fexes'"):
        snap.get("fexes")


def test_data_that_no_longer_matches_the_sdk(tmp_path: Path) -> None:
    snap = make_snapshot(facts=GetterResult(status=GetterStatus.OK, data={"hostname": "x"}))
    with pytest.raises(SnapshotError, match="doesn't match the installed SDK"):
        snap.get("facts")


def test_unreadable_snapshot(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": 2}')
    with pytest.raises(SnapshotError, match="not a valid snapshot"):
        read_snapshot(bad)
