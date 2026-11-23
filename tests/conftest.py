"""Shared builders: small, valid SDK models and device files."""

from datetime import timedelta
from ipaddress import IPv4Interface
from pathlib import Path

import pytest
from netsdk.cisco.nxos import NXOSFacts, NXOSInterface
from netsdk.core import IPAddress

from netboxsync.config import DeviceFile


def nxos_facts() -> NXOSFacts:
    return NXOSFacts(
        hostname="sw1.example.net",
        vendor="Cisco",
        model="N9K-C93180YC-EX",
        serial_number="FDO12345678",
        os_version="9.3(9)",
        uptime=timedelta(days=3, minutes=5),
    )


def nxos_interfaces() -> list[NXOSInterface]:
    return [
        NXOSInterface(
            name="mgmt0",
            enabled=True,
            oper_up=True,
            mac_address="aa:bb:cc:dd:ee:01",
            mtu=1500,
            speed=1_000_000,
            duplex="full",
        ),
        NXOSInterface(name="Ethernet1/1", description="uplink", enabled=False, oper_up=False),
    ]


def ip_addresses() -> list[IPAddress]:
    return [
        IPAddress(interface="mgmt0", address=IPv4Interface("10.0.0.5/24"), vrf="management"),
        IPAddress(interface="Vlan10", address=IPv4Interface("10.1.0.1/24")),
    ]


def device_file(**overrides: object) -> DeviceFile:
    data: dict[str, object] = {
        "driver": "cisco-nxos",
        "host": "psm.example.net",
        "username": "netops@10.0.0.5@psm.example.net",
        "key_file": "~/.ssh/id_ed25519",
        "site": "NYC-DC01",
        "role": "core-switch",
    }
    data.update(overrides)
    return DeviceFile.model_validate(data)


@pytest.fixture
def write_yaml(tmp_path: Path):  # type: ignore[no-untyped-def]
    def write(text: str, name: str = "sw1.yaml") -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return write
