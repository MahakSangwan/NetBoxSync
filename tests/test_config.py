from collections.abc import Callable
from pathlib import Path

import pytest

from netboxsync.config import (
    KEY_PASSPHRASE_ENV,
    ConfigError,
    Creatable,
    DeviceStatus,
    load_device_file,
    load_device_types_file,
)
from tests.conftest import device_file

FULL = """\
driver: cisco-nxos
host: psm.example.net
username: "netops@10.0.0.5@psm.example.net"
key_file: ~/.ssh/id_ed25519
site: NYC-DC01
region: US-East
location: Hall-A
rack: A-12
position: 30
face: front
role: core-switch
status: planned
management_interface: Vlan99
create: [location, rack]
"""


def test_full_device_file(write_yaml: Callable[[str], Path]) -> None:
    df = load_device_file(write_yaml(FULL))
    assert df.driver == "cisco-nxos"
    assert df.port == 22
    assert df.position == 30
    assert df.status is DeviceStatus.PLANNED
    assert df.create == {Creatable.LOCATION, Creatable.RACK}


def test_defaults() -> None:
    df = device_file()
    assert df.status is DeviceStatus.ACTIVE
    assert df.create == frozenset()
    assert df.role is None
    assert df.management_interface is None


def test_unknown_key_is_an_error(write_yaml: Callable[[str], Path]) -> None:
    with pytest.raises(ConfigError, match="rakc"):
        load_device_file(write_yaml(FULL + "rakc: B-1\n"))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"rack": "A-1", "position": 3}, "position needs both rack and face"),
        ({"position": 3, "face": "front"}, "position needs both rack and face"),
        ({"rack": "A-1", "face": "front"}, "face needs position"),
        ({"rack": "A-1", "position": 3.3, "face": "front"}, "multiple of 0.5"),
        ({"driver": "cisco-asa"}, "driver"),
        ({"status": "up"}, "status"),
        ({"create": ["site", "tenant"]}, "create"),
        ({"site": ""}, "site"),
    ],
)
def test_invalid_device_files(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        device_file(**overrides)


def test_half_unit_position_is_valid() -> None:
    assert device_file(rack="A-1", position=3.5, face="rear").position == 3.5


def test_not_yaml(write_yaml: Callable[[str], Path]) -> None:
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_device_file(write_yaml("driver: [unclosed\n"))


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"nope\.yaml"):
        load_device_file(tmp_path / "nope.yaml")


def test_credentials_take_passphrase_from_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY_PASSPHRASE_ENV, raising=False)
    creds = device_file().credentials()
    assert creds.password is None
    assert creds.key_passphrase is None
    assert creds.key_file is not None
    assert "~" not in str(creds.key_file)

    monkeypatch.setenv(KEY_PASSPHRASE_ENV, "s3cret")
    creds = device_file().credentials()
    assert creds.key_passphrase is not None
    assert creds.key_passphrase.get_secret_value() == "s3cret"


def test_passphrase_is_not_a_device_file_key() -> None:
    with pytest.raises(ValueError, match="key_passphrase"):
        device_file(key_passphrase="x")


def test_device_types_file(write_yaml: Callable[..., Path]) -> None:
    path = write_yaml(
        "device_types:\n"
        "  N9K-C93180YC-EX: {u_height: 1, airflow: front-to-rear, is_full_depth: true}\n"
        "  WS-C3850-24T: {u_height: 1.5}\n",
        "device_types.yaml",
    )
    types = load_device_types_file(path).device_types
    assert types["N9K-C93180YC-EX"].airflow == "front-to-rear"
    assert types["WS-C3850-24T"].u_height == 1.5
    assert types["WS-C3850-24T"].airflow is None


def test_empty_device_types_file(write_yaml: Callable[..., Path]) -> None:
    assert load_device_types_file(write_yaml("", "t.yaml")).device_types == {}


@pytest.mark.parametrize("entry", ["{airflow: front-to-back}", "{u_height: 1.2}", "{height: 1}"])
def test_invalid_device_types(write_yaml: Callable[..., Path], entry: str) -> None:
    with pytest.raises(ConfigError):
        load_device_types_file(write_yaml(f"device_types:\n  X: {entry}\n", "t.yaml"))
