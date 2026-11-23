"""Command line: ping, check, collect, ingest.

Exit status: 0 success, 1 the run failed, 2 bad input (files or arguments).
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from netboxsync import __version__
from netboxsync.collect import CollectError, collect, open_device
from netboxsync.config import ConfigError, DeviceFile, load_device_file
from netboxsync.diode import DryRunIngester, IngestError, connect, new_run_id, send
from netboxsync.drivers import DRIVERS
from netboxsync.ingest import build_entities
from netboxsync.netbox import CheckReport, NetBoxError, PynetboxReader, check_device_file
from netboxsync.snapshot import (
    GetterStatus,
    SnapshotError,
    latest_snapshot_path,
    read_snapshot,
    write_snapshot,
)

log = logging.getLogger("netboxsync")

EXIT_OK, EXIT_FAILED, EXIT_INPUT = 0, 1, 2


def device_label(path: Path) -> str:
    """A device is named after its file: `nyc-core-01.yaml` -> `nyc-core-01`."""
    return path.stem


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="netboxsync", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="check NetBox and Diode are reachable and the credentials work")

    p = sub.add_parser("check", help="check a device file's references against NetBox")
    p.add_argument("device_file", type=Path)

    p = sub.add_parser("collect", help="run every getter on a device and save a snapshot")
    p.add_argument("device_file", type=Path)
    p.add_argument("--snapshot-dir", type=Path, default=Path("snapshots"))
    p.add_argument("--session-log-dir", type=Path, default=Path("session_logs"))

    p = sub.add_parser("ingest", help="send a snapshot to NetBox through Diode")
    p.add_argument("device_file", type=Path)
    p.add_argument(
        "--snapshot", default="latest", help="snapshot file, or 'latest' (default) for the newest"
    )
    p.add_argument("--snapshot-dir", type=Path, default=Path("snapshots"))
    p.add_argument(
        "--dry-run", action="store_true", help="write the Diode requests as JSON, don't send"
    )
    p.add_argument("--dry-run-dir", type=Path, default=Path("dryrun"))
    p.add_argument(
        "--no-check", action="store_true", help="skip the NetBox check (only with --dry-run)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Before any Diode client exists: DiodeClient calls logging.basicConfig()
    # itself, which is a no-op once the root logger has a handler.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if args.command == "ingest" and args.no_check and not args.dry_run:
        log.error("--no-check is only allowed with --dry-run: nothing is sent unchecked")
        return EXIT_INPUT
    try:
        match args.command:
            case "ping":
                return _ping()
            case "check":
                return _check(load_device_file(args.device_file), device_label(args.device_file))
            case "collect":
                return _collect(args)
            case "ingest":
                return _ingest(args)
    except ConfigError as exc:
        log.error("%s", exc)
        return EXIT_INPUT
    except (NetBoxError, IngestError, CollectError, SnapshotError) as exc:
        log.error("%s", exc)
        return EXIT_FAILED
    raise AssertionError(f"unhandled command {args.command}")


def _ping() -> int:
    status = PynetboxReader.from_env().status()
    plugins = status.get("plugins") or {}
    log.info("NetBox %s reachable, token accepted", status.get("netbox-version", "?"))
    diode_plugin = plugins.get("netbox_diode_plugin")
    if diode_plugin:
        log.info("Diode NetBox plugin %s", diode_plugin)
    else:
        log.warning("Diode NetBox plugin not listed in NetBox status: %s", sorted(plugins))
    client = connect()
    log.info("Diode login succeeded")
    try:
        client.check_channel()
    finally:
        client.close()
    log.info("Diode gRPC channel connected")
    return EXIT_OK


def _report(label: str, report: CheckReport) -> None:
    for line in report.creates:
        log.info("%s: will create %s", label, line)
    for line in report.warnings:
        log.warning("%s: %s", label, line)
    for line in report.errors:
        log.error("%s: %s", label, line)


def _check(device_file: DeviceFile, label: str) -> int:
    report = check_device_file(device_file, PynetboxReader.from_env())
    _report(label, report)
    if not report.ok:
        log.error("%s: check failed (%d errors)", label, len(report.errors))
        return EXIT_FAILED
    log.info("%s: check passed", label)
    return EXIT_OK


def _collect(args: argparse.Namespace) -> int:
    device_file = load_device_file(args.device_file)
    label = device_label(args.device_file)
    driver = DRIVERS[device_file.driver]
    device = open_device(device_file, args.session_log_dir)
    snapshot = collect(device, driver, label)
    path = write_snapshot(snapshot, args.snapshot_dir)
    failed = [n for n, r in snapshot.getters.items() if r.status is GetterStatus.ERROR]
    log.info("%s: snapshot written to %s", label, path)
    if failed:
        log.warning("%s: getters that failed: %s", label, ", ".join(failed))
    return EXIT_OK


def _ingest(args: argparse.Namespace) -> int:
    device_file = load_device_file(args.device_file)
    label = device_label(args.device_file)
    path = (
        latest_snapshot_path(args.snapshot_dir, label)
        if args.snapshot == "latest"
        else Path(args.snapshot)
    )
    snapshot = read_snapshot(path)
    if snapshot.device_label != label or snapshot.driver != device_file.driver:
        raise SnapshotError(
            f"{path} is {snapshot.device_label!r} ({snapshot.driver}), "
            f"not {label!r} ({device_file.driver})"
        )
    log.info("%s: using snapshot %s (collected %s)", label, path, snapshot.collected_at)

    if not args.no_check and _check(device_file, label) != EXIT_OK:
        return EXIT_FAILED

    entities = build_entities(device_file, snapshot)
    run_id = new_run_id()
    if args.dry_run:
        dry = DryRunIngester(args.dry_run_dir)
        sent = send(dry, entities, run_id=run_id, device_label=label, snapshot=path)
        if sent:
            log.info("%s: dry run, %d request(s) written to %s", label, sent, args.dry_run_dir)
        return EXIT_OK

    if not entities:
        log.info("%s: no slices produce entities yet; nothing sent", label)
        return EXIT_OK
    client = connect()
    try:
        send(client, entities, run_id=run_id, device_label=label, snapshot=path)
    finally:
        client.close()
    log.info("%s: ingest accepted by Diode (run %s); verify in NetBox", label, run_id)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
