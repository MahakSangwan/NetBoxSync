"""Turning a snapshot into Diode entities, one slice at a time.

A slice is a function from the ingest input (device file, snapshot, device
types) to entities. Slices are added here as they are agreed.
"""

from collections.abc import Callable, Sequence
from typing import Any

from netboxsync import devices
from netboxsync.inputs import IngestInput
from netboxsync.netbox import CheckReport, NetBoxReader

Slice = Callable[[IngestInput], Sequence[Any]]

#: In ingest order: later slices may refer to objects earlier ones created.
SLICES: list[tuple[str, Slice]] = [("devices", devices.build)]


def build_entities(inp: IngestInput) -> list[Any]:
    entities: list[Any] = []
    for _name, build in SLICES:
        entities.extend(build(inp))
    return entities


def check_snapshot(inp: IngestInput, reader: NetBoxReader, report: CheckReport) -> None:
    """The checks that need the device's own data, added to `report`."""
    devices.check(inp, reader, report)
