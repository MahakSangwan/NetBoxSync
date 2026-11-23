"""Snapshots: what one collection run got from one device (D2).

Collecting and ingesting are separate steps joined by a JSON file, so a
snapshot can be ingested again, inspected by hand, or (sanitized) turned into
a test fixture, without touching the device.

Layout: `<snapshot_dir>/<device label>/<UTC timestamp>.json`, where the label
is the device file's name without `.yaml`.
"""

from datetime import UTC, datetime
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from netboxsync.drivers import DRIVERS, Driver


class SnapshotError(Exception):
    """A snapshot is missing, unreadable, or lacks what was asked of it."""


class GetterStatus(StrEnum):
    OK = "ok"
    #: The device doesn't have the feature (netsdk NotSupportedError).
    NOT_SUPPORTED = "not_supported"
    #: The getter failed; `error` says why. Other getters were still run.
    ERROR = "error"


class GetterResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: GetterStatus
    #: The getter's result as JSON; only set when status is ok.
    data: Any = None
    error_type: str | None = None
    error: str | None = None


class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal[1] = 1
    device_label: str
    driver: str
    sdk_version: str
    netboxsync_version: str
    collected_at: datetime
    #: The SDK's session recording. Its name carries the PSM host, not the
    #: device, so this is how a log is traced back to its device.
    session_log: str | None = None
    getters: dict[str, GetterResult]

    @property
    def driver_info(self) -> Driver:
        try:
            return DRIVERS[self.driver]
        except KeyError:
            raise SnapshotError(f"snapshot uses unknown driver {self.driver!r}") from None

    def status(self, getter: str) -> GetterStatus | None:
        result = self.getters.get(getter)
        return result.status if result else None

    def get(self, getter: str) -> Any:
        """The getter's result as SDK models, validated like a live result.

        Raises SnapshotError when the getter wasn't collected or didn't succeed;
        callers that can do without it check `status()` first.
        """
        result = self.getters.get(getter)
        if result is None:
            raise SnapshotError(f"{self.device_label}: snapshot has no {getter!r} data")
        if result.status is not GetterStatus.OK:
            raise SnapshotError(
                f"{self.device_label}: {getter!r} was {result.status.value} at collection: "
                f"{result.error}"
            )
        try:
            return _adapter(self.driver, getter).validate_python(result.data)
        except ValidationError as exc:
            raise SnapshotError(
                f"{self.device_label}: {getter!r} data doesn't match the installed SDK "
                f"models (collected with SDK {self.sdk_version}): {exc}"
            ) from exc


@cache
def _adapter(driver: str, getter: str) -> TypeAdapter[Any]:
    try:
        return TypeAdapter(DRIVERS[driver].getters[getter])
    except KeyError:
        raise SnapshotError(f"driver {driver!r} has no getter {getter!r}") from None


def dump_result(driver: str, getter: str, value: Any) -> Any:
    """JSON form of a getter's live result."""
    return _adapter(driver, getter).dump_python(value, mode="json")


def write_snapshot(snapshot: Snapshot, snapshot_dir: Path) -> Path:
    """Write a new snapshot file; never overwrites an existing one."""
    directory = snapshot_dir / snapshot.device_label
    directory.mkdir(parents=True, exist_ok=True)
    stem = snapshot.collected_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{stem}.json"
    counter = 1
    while True:
        try:
            with path.open("x", encoding="utf-8") as fh:
                fh.write(snapshot.model_dump_json(indent=2))
                fh.write("\n")
            return path
        except FileExistsError:
            path = directory / f"{stem}-{counter}.json"
            counter += 1


def read_snapshot(path: Path) -> Snapshot:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SnapshotError(f"{path}: {exc.strerror or exc}") from exc
    try:
        return Snapshot.model_validate_json(text)
    except ValidationError as exc:
        raise SnapshotError(f"{path}: not a valid snapshot: {exc}") from exc


def latest_snapshot_path(snapshot_dir: Path, device_label: str) -> Path:
    directory = snapshot_dir / device_label
    candidates = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not candidates:
        raise SnapshotError(
            f"no snapshots for {device_label!r} in {directory}; run `netboxsync collect` first"
        )
    # Names are UTC timestamps (plus a counter), so they sort by time.
    return max(candidates, key=lambda p: (p.stem.split("-")[0], _counter(p.stem)))


def _counter(stem: str) -> int:
    _, _, suffix = stem.partition("-")
    return int(suffix) if suffix.isdigit() else 0


def now() -> datetime:
    return datetime.now(UTC)
