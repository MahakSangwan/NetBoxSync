"""Everything one ingest works from: the device file, a snapshot, device types."""

from dataclasses import dataclass, field
from typing import Any

from netboxsync.config import DeviceFile, DeviceTypeOverride, DeviceTypesFile
from netboxsync.drivers import DRIVERS, Driver
from netboxsync.snapshot import Snapshot


@dataclass(frozen=True)
class IngestInput:
    device_file: DeviceFile
    snapshot: Snapshot
    device_types: DeviceTypesFile = field(default_factory=DeviceTypesFile)

    @property
    def driver(self) -> Driver:
        return DRIVERS[self.snapshot.driver]

    @property
    def facts(self) -> Any:
        """The snapshot's facts (an IOSFacts or NXOSFacts); facts are always ok (D8)."""
        return self.snapshot.get("facts")

    @property
    def device_type_override(self) -> DeviceTypeOverride | None:
        """device_types.yaml's entry for this device's model, if it has one (D7)."""
        return self.device_types.device_types.get(self.facts.model)
