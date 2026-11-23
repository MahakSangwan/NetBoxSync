"""Writing to NetBox through Diode (D1).

Diode is asynchronous: a successful `ingest()` only means the server accepted
the request. Whether NetBox ended up right is checked separately, by reading
NetBox back (pynetbox) in each slice's verification step.

Every request carries a run ID and the device label as request metadata,
and every entity a slice builds carries the `netboxsync` tag (D9), so what a
run wrote can be found again.
"""

import logging
import os
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from netboxlabs.diode.sdk.chunking import create_message_chunks
from netboxlabs.diode.sdk.client import DiodeClient, DiodeDryRunClient

from netboxsync import __version__

log = logging.getLogger(__name__)

APP_NAME = "netboxsync"
#: Tag on every entity this project writes.
TAG = "netboxsync"
#: e.g. grpc://netbox.example.net:8080/diode; DIODE_CLIENT_ID and
#: DIODE_CLIENT_SECRET are read by the Diode SDK itself.
DIODE_TARGET_ENV = "DIODE_TARGET"


class IngestError(Exception):
    """Diode rejected a request, or isn't configured."""


class Ingester(Protocol):
    def ingest(self, entities: Any, *, metadata: dict[str, Any] | None = None) -> Any: ...

    def close(self) -> None: ...


def new_run_id() -> str:
    return str(uuid.uuid4())


class DiodeIngester:
    """The real Diode client. Creating it authenticates (OAuth client credentials)."""

    def __init__(self, target: str) -> None:
        try:
            self._client = DiodeClient(target=target, app_name=APP_NAME, app_version=__version__)
        except Exception as exc:  # the SDK raises several unrelated types here
            raise IngestError(f"Diode connection to {target} failed: {exc}") from exc

    def ingest(self, entities: Any, *, metadata: dict[str, Any] | None = None) -> Any:
        return self._client.ingest(entities=entities, metadata=metadata)

    def close(self) -> None:
        self._client.close()  # type: ignore[no-untyped-call]


def connect() -> DiodeIngester:
    target = os.environ.get(DIODE_TARGET_ENV)
    if not target:
        raise IngestError(f"set {DIODE_TARGET_ENV} (and DIODE_CLIENT_ID, DIODE_CLIENT_SECRET)")
    return DiodeIngester(target)


class DryRunIngester:
    """Writes each request as JSON under `output_dir` instead of sending it."""

    def __init__(self, output_dir: Path) -> None:
        self._client = DiodeDryRunClient(app_name=APP_NAME, output_dir=str(output_dir))
        self.output_dir = output_dir

    def ingest(self, entities: Any, *, metadata: dict[str, Any] | None = None) -> Any:
        return self._client.ingest(entities=entities, metadata=metadata)

    def close(self) -> None:
        pass


def send(
    ingester: Ingester,
    entities: Sequence[Any],
    *,
    run_id: str,
    device_label: str,
    snapshot: Path,
) -> int:
    """Send entities in size-limited chunks; returns the number of requests sent."""
    if not entities:
        log.info("%s: nothing to ingest", device_label)
        return 0
    metadata = {
        "run_id": run_id,
        "device_label": device_label,
        "snapshot": snapshot.name,
        "netboxsync_version": __version__,
    }
    chunks = create_message_chunks(list(entities))
    for number, chunk in enumerate(chunks, start=1):
        response = ingester.ingest(entities=chunk, metadata=metadata)
        errors = list(getattr(response, "errors", []) or [])
        if errors:
            raise IngestError(
                f"{device_label}: Diode rejected request {number}/{len(chunks)} "
                f"(run {run_id}): " + "; ".join(errors)
            )
        log.info(
            "%s: request %d/%d accepted (%d entities, run %s)",
            device_label,
            number,
            len(chunks),
            len(chunk),
            run_id,
        )
    return len(chunks)
