"""Turning a snapshot into Diode entities, one slice at a time.

A slice is a function from (device file, snapshot) to entities. Slices are
added here as they are agreed; slice 0 has none, so an ingest sends nothing.
"""

from collections.abc import Callable, Sequence
from typing import Any

from netboxsync.config import DeviceFile
from netboxsync.snapshot import Snapshot

Slice = Callable[[DeviceFile, Snapshot], Sequence[Any]]

#: In ingest order: later slices may refer to objects earlier ones created.
SLICES: list[tuple[str, Slice]] = []


def build_entities(device_file: DeviceFile, snapshot: Snapshot) -> list[Any]:
    entities: list[Any] = []
    for _name, build in SLICES:
        entities.extend(build(device_file, snapshot))
    return entities
