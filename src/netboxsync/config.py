"""Input files: one YAML file per device, and an optional device-types file.

Everything a device can't tell us about itself (where it is, what role it
plays, how to reach it) is written down here by hand (D3). Unknown keys are
errors, so a misspelt key never silently drops a value.
"""

import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from netsdk.core import Credentials
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

#: Environment variable holding the SSH key passphrase, if the key has one.
KEY_PASSPHRASE_ENV = "NETBOXSYNC_KEY_PASSPHRASE"

Name = Annotated[str, Field(min_length=1)]


class ConfigError(Exception):
    """An input file is missing, unreadable or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DeviceStatus(StrEnum):
    """NetBox device statuses (dcim DeviceStatusChoices)."""

    OFFLINE = "offline"
    ACTIVE = "active"
    PLANNED = "planned"
    STAGED = "staged"
    FAILED = "failed"
    INVENTORY = "inventory"
    DECOMMISSIONING = "decommissioning"


class Creatable(StrEnum):
    """Reference objects a device file may allow the run to create (D4)."""

    REGION = "region"
    SITE = "site"
    LOCATION = "location"
    RACK = "rack"
    ROLE = "role"


class DeviceFile(_Strict):
    """One device to onboard."""

    #: netsdk driver; chosen by hand, never autodetected.
    driver: Literal["cisco-ios", "cisco-nxos"]
    #: SSH host: the CyberArk PSM jumphost, not the device (D5).
    host: Name
    port: int = Field(default=22, ge=1, le=65535)
    #: PSM username, which carries the target device.
    username: Name
    key_file: Path

    site: Name
    #: Only checked against the site's region, or used when creating the site.
    region: Name | None = None
    location: Name | None = None
    rack: Name | None = None
    #: Lowest rack unit the device occupies.
    position: float | None = Field(default=None, gt=0)
    face: Literal["front", "rear"] | None = None
    #: NetBox requires one; required here so Diode never fills in a placeholder.
    role: Name
    status: DeviceStatus = DeviceStatus.ACTIVE
    #: Interface holding the management (primary) IP, when it isn't mgmt0 (D6).
    management_interface: Name | None = None

    #: Reference objects this run may create when NetBox doesn't have them.
    create: frozenset[Creatable] = frozenset()

    @model_validator(mode="after")
    def _check_placement(self) -> Self:
        if self.position is not None and (self.rack is None or self.face is None):
            raise ValueError("position needs both rack and face")
        if self.face is not None and self.position is None:
            raise ValueError("face needs position")
        if self.position is not None and (self.position * 2) % 1:
            raise ValueError("position must be a multiple of 0.5")
        return self

    def credentials(self) -> Credentials:
        """SSH credentials; the key passphrase comes from the environment only."""
        passphrase = os.environ.get(KEY_PASSPHRASE_ENV)
        return Credentials(
            username=self.username,
            key_file=self.key_file.expanduser(),
            key_passphrase=SecretStr(passphrase) if passphrase else None,
        )


Airflow = Literal[
    "front-to-rear",
    "rear-to-front",
    "left-to-right",
    "right-to-left",
    "side-to-rear",
    "rear-to-side",
    "bottom-to-top",
    "top-to-bottom",
    "passive",
    "mixed",
]


class DeviceTypeOverride(_Strict):
    """Physical facts about a model that no device reports (D7)."""

    u_height: float | None = Field(default=None, ge=0, le=1000)
    is_full_depth: bool | None = None
    airflow: Airflow | None = None

    @model_validator(mode="after")
    def _check_height(self) -> Self:
        if self.u_height is not None and (self.u_height * 2) % 1:
            raise ValueError("u_height must be a multiple of 0.5")
        return self


class DeviceTypesFile(_Strict):
    """Overrides keyed by part number, e.g. `N9K-C93180YC-EX`."""

    device_types: dict[Name, DeviceTypeOverride] = {}


def _read_yaml(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: {exc.strerror or exc}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML: {exc}") from exc


def _validation_message(path: Path, exc: ValidationError) -> str:
    lines = [f"{path}: invalid"]
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"]) or "(file)"
        lines.append(f"  {where}: {err['msg']}")
    return "\n".join(lines)


def load_device_file(path: Path) -> DeviceFile:
    data = _read_yaml(path)
    try:
        return DeviceFile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_validation_message(path, exc)) from exc


def load_device_types_file(path: Path) -> DeviceTypesFile:
    data = _read_yaml(path)
    if data is None:
        return DeviceTypesFile()
    try:
        return DeviceTypesFile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_validation_message(path, exc)) from exc
