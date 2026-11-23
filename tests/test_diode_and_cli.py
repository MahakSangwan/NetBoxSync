import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from netboxlabs.diode.sdk.ingester import Entity, Site, Tag

from netboxsync import cli, ingest, netbox
from netboxsync.diode import TAG, DryRunIngester, IngestError, send, warn_if_tls_verify_skipped
from netboxsync.snapshot import write_snapshot
from tests.test_devices import record
from tests.test_netbox_check import NETBOX, FakeNetBox
from tests.test_snapshot import make_snapshot

DEVICE_YAML = """\
driver: cisco-nxos
host: psm.example.net
username: "netops@10.0.0.5@psm.example.net"
key_file: ~/.ssh/id_ed25519
site: NYC-DC01
role: core-switch
"""


def site_entity() -> Any:
    return Entity(site=Site(name="NYC-DC01", tags=[Tag(name=TAG)]))


class RecordingIngester:
    def __init__(self, errors: list[str] | None = None) -> None:
        self.calls: list[tuple[list[Any], dict[str, Any] | None]] = []
        self.errors = errors or []

    def ingest(self, entities: Any, *, metadata: dict[str, Any] | None = None) -> Any:
        self.calls.append((list(entities), metadata))
        return SimpleNamespace(errors=self.errors)

    def close(self) -> None:
        pass


def test_send_attaches_run_metadata() -> None:
    rec = RecordingIngester()
    sent = send(rec, [site_entity()], run_id="r1", device_label="sw1", snapshot=Path("s/x.json"))
    assert sent == 1
    entities, metadata = rec.calls[0]
    assert len(entities) == 1
    assert metadata == {
        "run_id": "r1",
        "device_label": "sw1",
        "snapshot": "x.json",
        "netboxsync_version": "0.1.0",
    }


def test_send_nothing_makes_no_request() -> None:
    rec = RecordingIngester()
    assert send(rec, [], run_id="r1", device_label="sw1", snapshot=Path("x.json")) == 0
    assert rec.calls == []


def test_diode_errors_raise() -> None:
    rec = RecordingIngester(errors=["bad site"])
    with pytest.raises(IngestError, match=r"run r1.*bad site"):
        send(rec, [site_entity()], run_id="r1", device_label="sw1", snapshot=Path("x.json"))


def test_dry_run_writes_the_request(tmp_path: Path) -> None:
    send(
        DryRunIngester(tmp_path),
        [site_entity()],
        run_id="r1",
        device_label="sw1",
        snapshot=Path("x.json"),
    )
    (out,) = tmp_path.glob("*.json")
    request = json.loads(out.read_text())
    assert request["entities"][0]["site"]["name"] == "NYC-DC01"
    assert request["entities"][0]["site"]["tags"] == [{"name": "netboxsync"}]
    assert request["metadata"]["run_id"] == "r1"


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sw1.yaml").write_text(DEVICE_YAML)
    write_snapshot(make_snapshot(), tmp_path / "snapshots")
    return tmp_path


def test_ingest_dry_run_without_netbox(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest, "SLICES", [("test", lambda inp: [site_entity()])])
    assert cli.main(["ingest", "sw1.yaml", "--dry-run", "--no-check"]) == cli.EXIT_OK
    assert len(list((workdir / "dryrun").glob("*.json"))) == 1


def test_no_check_needs_dry_run(workdir: Path) -> None:
    assert cli.main(["ingest", "sw1.yaml", "--no-check"]) == cli.EXIT_INPUT


def test_ingest_runs_the_check_first(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (workdir / "sw1.yaml").write_text(DEVICE_YAML.replace("NYC-DC01", "NYC-DC1"))
    monkeypatch.setattr(netbox.PynetboxReader, "from_env", classmethod(lambda cls: NETBOX))
    monkeypatch.setattr(ingest, "SLICES", [("test", lambda inp: [site_entity()])])
    assert cli.main(["ingest", "sw1.yaml", "--dry-run"]) == cli.EXIT_FAILED
    assert not (workdir / "dryrun").exists()


def test_snapshot_of_another_device_is_refused(workdir: Path) -> None:
    (workdir / "sw2.yaml").write_text(DEVICE_YAML)
    snap = next((workdir / "snapshots" / "sw1").glob("*.json"))
    code = cli.main(["ingest", "sw2.yaml", "--snapshot", str(snap), "--dry-run", "--no-check"])
    assert code == cli.EXIT_FAILED


def test_bad_device_file_is_an_input_error(workdir: Path) -> None:
    (workdir / "bad.yaml").write_text("driver: cisco-ios\n")
    assert cli.main(["check", "bad.yaml"]) == cli.EXIT_INPUT


def test_check_without_netbox_env(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NETBOX_URL", raising=False)
    assert cli.main(["check", "sw1.yaml"]) == cli.EXIT_FAILED


# -- slice 1 through the CLI ---------------------------------------------------


@pytest.fixture
def fake_netbox(monkeypatch: pytest.MonkeyPatch) -> FakeNetBox:
    reader = FakeNetBox(NETBOX.objects)
    monkeypatch.setattr(netbox.PynetboxReader, "from_env", classmethod(lambda cls: reader))
    return reader


def test_dry_run_sends_the_device(workdir: Path, fake_netbox: FakeNetBox) -> None:
    assert cli.main(["ingest", "sw1.yaml", "--dry-run"]) == cli.EXIT_OK
    (out,) = (workdir / "dryrun").glob("*.json")
    device = json.loads(out.read_text())["entities"][0]["device"]
    assert device["name"] == "sw1.example.net"
    assert device["custom_fields"] == {"chassis_serial": {"text": "FDO12345678"}}


def test_device_types_file_next_to_the_device_file_is_used(
    workdir: Path, fake_netbox: FakeNetBox
) -> None:
    (workdir / "device_types.yaml").write_text(
        "device_types:\n  N9K-C93180YC-EX:\n    u_height: 2\n"
    )
    assert cli.main(["ingest", "sw1.yaml", "--dry-run"]) == cli.EXIT_OK
    (out,) = (workdir / "dryrun").glob("*.json")
    assert json.loads(out.read_text())["entities"][0]["device"]["device_type"]["u_height"] == 2


def test_missing_device_types_file_given_explicitly(workdir: Path) -> None:
    code = cli.main(["ingest", "sw1.yaml", "--dry-run", "--no-check", "--device-types", "no.yaml"])
    assert code == cli.EXIT_INPUT


def test_check_runs_device_checks_with_a_snapshot(
    workdir: Path, fake_netbox: FakeNetBox, caplog: pytest.LogCaptureFixture
) -> None:
    fake_netbox.custom_fields = []
    assert cli.main(["check", "sw1.yaml"]) == cli.EXIT_FAILED
    assert "chassis_serial" in caplog.text


def test_check_without_a_snapshot_checks_the_file_only(
    workdir: Path, fake_netbox: FakeNetBox, caplog: pytest.LogCaptureFixture
) -> None:
    fake_netbox.custom_fields = []  # would fail the device checks
    code = cli.main(["check", "sw1.yaml", "--snapshot-dir", str(workdir / "none")])
    assert code == cli.EXIT_OK
    assert "checking the device file only" in caplog.text


def test_ingest_reads_the_device_back(
    workdir: Path, fake_netbox: FakeNetBox, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = RecordingIngester()
    monkeypatch.setattr(cli, "connect", lambda: rec)

    def applied(*args: Any, **kwargs: Any) -> None:
        fake_netbox.device_list = [record()]

    monkeypatch.setattr(rec, "close", applied)  # "Diode" applies it once the client is done
    assert cli.main(["ingest", "sw1.yaml"]) == cli.EXIT_OK
    assert len(rec.calls) == 1


def test_ingest_fails_when_netbox_differs(
    workdir: Path,
    fake_netbox: FakeNetBox,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(cli, "connect", lambda: RecordingIngester())
    fake_netbox.device_list = [record(role="access-switch")]
    code = cli.main(["ingest", "sw1.yaml", "--verify-timeout", "0"])
    assert code == cli.EXIT_FAILED
    assert "role is 'access-switch', expected 'core-switch'" in caplog.text


def test_skip_tls_verify_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DIODE_SKIP_TLS_VERIFY", "True")
    warn_if_tls_verify_skipped()
    assert "don't check certificates" in caplog.text
    caplog.clear()
    monkeypatch.setenv("DIODE_SKIP_TLS_VERIFY", "false")
    warn_if_tls_verify_skipped()
    assert caplog.text == ""
