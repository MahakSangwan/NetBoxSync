from pathlib import Path
from typing import Any

import pytest
from netsdk.core import (
    AuthenticationError,
    Credentials,
    Device,
    DeviceTimeoutError,
    NotSupportedError,
    ParseError,
)

from netboxsync.collect import CollectError, collect, open_device
from netboxsync.drivers import DRIVERS
from netboxsync.snapshot import GetterStatus
from tests.conftest import device_file, ip_addresses, nxos_facts, nxos_interfaces

NXOS = DRIVERS["cisco-nxos"]


class FakeDevice(Device):
    """Answers each getter from `answers`: a value, or an exception to raise."""

    def __init__(self, answers: dict[str, Any], *, open_error: Exception | None = None) -> None:
        super().__init__("psm.example.net", Credentials(username="u", password="p"))
        self.answers = answers
        self.open_error = open_error
        self.opened = self.closed = False
        self.session_log = Path("session_logs/psm.example.net_x.log")

    @property
    def is_open(self) -> bool:
        return self.opened and not self.closed

    def open(self) -> None:
        if self.open_error:
            raise self.open_error
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def __getattr__(self, attr: str) -> Any:
        getter = attr.removeprefix("get_")
        if not attr.startswith("get_") or getter not in self.answers:
            raise AttributeError(attr)

        def call() -> Any:
            answer = self.answers[getter]
            if isinstance(answer, Exception):
                raise answer
            return answer

        return call


def all_ok() -> dict[str, Any]:
    answers: dict[str, Any] = {name: [] for name in NXOS.getters}
    answers.update(
        facts=nxos_facts(),
        interfaces=nxos_interfaces(),
        ip_addresses=ip_addresses(),
        vtp_status=NotSupportedError("VTP off"),
        vpc_status=NotSupportedError("vPC off"),
        fexes=NotSupportedError("FEX off"),
    )
    return answers


def test_collects_every_getter_and_closes() -> None:
    device = FakeDevice(all_ok())
    snap = collect(device, NXOS, "sw1")
    assert list(snap.getters) == list(NXOS.getters)
    assert snap.get("facts") == nxos_facts()
    assert snap.status("vpc_status") is GetterStatus.NOT_SUPPORTED
    assert snap.getters["vpc_status"].error == "vPC off"
    assert snap.session_log == "session_logs/psm.example.net_x.log"
    assert snap.sdk_version == "0.1.0"
    assert device.closed


def test_a_failing_getter_is_recorded_and_the_rest_still_run() -> None:
    answers = all_ok()
    answers["interfaces"] = ParseError("bad JSON", host="psm.example.net")
    snap = collect(FakeDevice(answers), NXOS, "sw1")
    result = snap.getters["interfaces"]
    assert result.status is GetterStatus.ERROR
    assert result.error_type == "ParseError"
    assert snap.get("ip_addresses") == ip_addresses()


@pytest.mark.parametrize("error", [ParseError("x"), NotSupportedError("x")])
def test_facts_failing_aborts(error: Exception) -> None:
    answers = all_ok()
    answers["facts"] = error
    device = FakeDevice(answers)
    with pytest.raises(CollectError, match=r"required getter 'facts'.*session log"):
        collect(device, NXOS, "sw1")
    assert device.closed


def test_timeout_mid_run_aborts_without_snapshot() -> None:
    answers = all_ok()
    answers["neighbors"] = DeviceTimeoutError("no prompt")
    device = FakeDevice(answers)
    with pytest.raises(CollectError, match="DeviceTimeoutError"):
        collect(device, NXOS, "sw1")
    assert device.closed


def test_login_failure_aborts() -> None:
    device = FakeDevice(all_ok(), open_error=AuthenticationError("denied"))
    with pytest.raises(CollectError, match="AuthenticationError: denied"):
        collect(device, NXOS, "sw1")


def test_open_device_uses_the_sdk_class_and_psm_host(tmp_path: Path) -> None:
    device = open_device(device_file(port=2222), tmp_path)
    assert type(device) is NXOS.device_class
    assert device.host == "psm.example.net"
    assert device.port == 2222
    assert device.credentials.username == "netops@10.0.0.5@psm.example.net"
    assert device.session_log_dir == tmp_path
    assert not device.is_open
