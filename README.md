# NetBoxSync

Populates NetBox from live network devices: collects with
[netsdk](https://github.com/MahakSangwan/NetworkSDK), writes through Diode.
Design decisions are in [docs/decisions.md](docs/decisions.md).

## Requirements

Linux, Python 3.14, [uv](https://docs.astral.sh/uv/), `git`, `gcc` and `make`
(Cisco's Genie parser, used by the IOS driver, builds a dependency from source
on 3.14).

```
uv sync
```

## Configuration

Environment:

| Variable | Used by | |
|---|---|---|
| `NETBOX_URL`, `NETBOX_TOKEN` | `ping`, `check`, `ingest` | Read-only token is enough |
| `NETBOX_CA_FILE` | NetBox | CA or self-signed cert, if not publicly trusted |
| `DIODE_TARGET` | `ping`, `ingest` | e.g. `grpc://netbox.example.net:8080/diode` |
| `DIODE_CLIENT_ID`, `DIODE_CLIENT_SECRET` | `ping`, `ingest` | Read by the Diode SDK |
| `DIODE_CERT_FILE` | Diode | CA or self-signed cert, for both the login and gRPC; read by the Diode SDK |
| `DIODE_SKIP_TLS_VERIFY` | Diode | Optional, read by the Diode SDK; turns off certificate checks, so netboxsync warns when it's set (D16) |
| `NETBOXSYNC_KEY_PASSPHRASE` | `collect` | Only if the SSH key has one |

Leave `REQUESTS_CA_BUNDLE` and `CURL_CA_BUNDLE` unset: `requests` lets them
override both CA files above (D14).

One YAML file per device: see
[examples/device.example.yaml](examples/device.example.yaml). Physical facts
per model (height, depth, airflow) go in an optional `device_types.yaml` next
to the device files, or the file given with `--device-types`: see
[examples/device_types.example.yaml](examples/device_types.example.yaml).

NetBox needs one custom field, created by hand once (D18): **Customization >
Custom Fields**, name `chassis_serial`, object type Device, type Text,
Unique. Diode uses it to find a device again after its hostname changes;
`check` fails without it.

## Usage

```
uv run netboxsync ping                          # NetBox and Diode reachable, credentials OK
uv run netboxsync collect devices/sw1.yaml      # -> snapshots/sw1/<timestamp>.json
uv run netboxsync check   devices/sw1.yaml      # references, device identity, rack space
uv run netboxsync ingest  devices/sw1.yaml --dry-run   # -> dryrun/*.json, nothing sent
uv run netboxsync ingest  devices/sw1.yaml      # check, send through Diode, read back
```

`check` and `ingest` use the newest snapshot unless `--snapshot FILE` is
given; without any snapshot, `check` checks the device file only. After
sending, `ingest` waits up to `--verify-timeout` seconds (default 60) for
NetBox to show the device as sent, and fails if it doesn't.
Snapshots, session logs and dry-run output are written under the current
directory and are gitignored.

## Development

```
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
uv run pytest
```
