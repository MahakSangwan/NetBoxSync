"""Collect: run every getter a driver has against one device (D2).

Failure rules (D8):
- `facts` must succeed: without the hostname nothing can be ingested.
- A connection or timeout error aborts the run with no snapshot: the session
  can't be trusted after it, and a half snapshot is easy to mistake for a
  whole one.
- Any other getter failure is recorded in the snapshot and the next getter
  runs. Whether a slice can do without it is decided at ingest.
"""

import logging
import time
from pathlib import Path

from netsdk.core import (
    Device,
    DeviceConnectionError,
    DeviceTimeoutError,
    NetSDKError,
    NotSupportedError,
)
from pydantic import ValidationError

from netboxsync import __version__
from netboxsync.config import DeviceFile
from netboxsync.drivers import DRIVERS, Driver, sdk_version
from netboxsync.snapshot import GetterResult, GetterStatus, Snapshot, dump_result, now

log = logging.getLogger(__name__)

REQUIRED_GETTERS = frozenset({"facts"})
#: Seconds, for every device (D15). The SDK uses one value for the SSH
#: connect/auth/banner timeouts and for each command's whole output; its 30 s
#: default is too short for `show interface | json` on large NX-OS boxes.
DEVICE_TIMEOUT = 120.0


class CollectError(Exception):
    """Collection stopped; no snapshot was written."""


def open_device(device_file: DeviceFile, session_log_dir: Path) -> Device:
    driver = DRIVERS[device_file.driver]
    # Only the arguments the base Device defines; the rest keep SDK defaults.
    return driver.device_class(
        device_file.host,
        device_file.credentials(),
        port=device_file.port,
        timeout=DEVICE_TIMEOUT,
        session_log_dir=session_log_dir,
    )


def collect(device: Device, driver: Driver, device_label: str) -> Snapshot:
    """Open the device, run every getter, close it, and return the snapshot."""
    collected_at = now()
    results: dict[str, GetterResult] = {}
    try:
        with device:
            for getter in driver.getters:
                results[getter] = _run_getter(device, driver, device_label, getter)
    except (DeviceConnectionError, DeviceTimeoutError) as exc:
        raise CollectError(
            f"{device_label}: {type(exc).__name__}: {exc}{_log_hint(device)}"
        ) from exc
    return Snapshot(
        device_label=device_label,
        driver=driver.name,
        sdk_version=sdk_version(driver),
        netboxsync_version=__version__,
        collected_at=collected_at,
        session_log=_session_log(device),
        getters=results,
    )


def _run_getter(device: Device, driver: Driver, device_label: str, getter: str) -> GetterResult:
    started = time.monotonic()
    try:
        value = getattr(device, f"get_{getter}")()
    except DeviceConnectionError, DeviceTimeoutError:
        raise
    except NotSupportedError as exc:
        result = _failed(GetterStatus.NOT_SUPPORTED, exc)
    except (NetSDKError, ValidationError) as exc:
        # ValidationError: the SDK built an invalid model, an SDK bug worth reporting.
        result = _failed(GetterStatus.ERROR, exc)
    else:
        result = GetterResult(status=GetterStatus.OK, data=dump_result(driver.name, getter, value))

    elapsed = time.monotonic() - started
    if result.status is GetterStatus.OK:
        log.info("%s: %s ok (%.1fs)", device_label, getter, elapsed)
    elif getter in REQUIRED_GETTERS:
        raise CollectError(
            f"{device_label}: required getter {getter!r} failed: {result.error_type}: "
            f"{result.error}{_log_hint(device)}"
        )
    else:
        level = logging.INFO if result.status is GetterStatus.NOT_SUPPORTED else logging.WARNING
        log.log(level, "%s: %s %s: %s", device_label, getter, result.status.value, result.error)
    return result


def _failed(status: GetterStatus, exc: Exception) -> GetterResult:
    return GetterResult(status=status, error_type=type(exc).__name__, error=str(exc))


def _session_log(device: Device) -> str | None:
    path = getattr(device, "session_log", None)
    return str(path) if path is not None else None


def _log_hint(device: Device) -> str:
    path = _session_log(device)
    return f" (session log: {path})" if path else ""
