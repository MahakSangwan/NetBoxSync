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
| `REQUESTS_CA_BUNDLE` | NetBox | Only for a private CA |
| `DIODE_TARGET` | `ping`, `ingest` | e.g. `grpc://netbox.example.net:8080/diode` |
| `DIODE_CLIENT_ID`, `DIODE_CLIENT_SECRET` | `ping`, `ingest` | Read by the Diode SDK |
| `DIODE_CERT_FILE`, `DIODE_SKIP_TLS_VERIFY` | Diode | Optional, read by the Diode SDK |
| `NETBOXSYNC_KEY_PASSPHRASE` | `collect` | Only if the SSH key has one |

One YAML file per device: see
[examples/device.example.yaml](examples/device.example.yaml).

## Usage

```
uv run netboxsync ping                          # NetBox and Diode reachable, credentials OK
uv run netboxsync check   devices/sw1.yaml      # references exist in NetBox
uv run netboxsync collect devices/sw1.yaml      # -> snapshots/sw1/<timestamp>.json
uv run netboxsync ingest  devices/sw1.yaml --dry-run   # -> dryrun/*.json, nothing sent
uv run netboxsync ingest  devices/sw1.yaml      # check, then send through Diode
```

`ingest` uses the newest snapshot unless `--snapshot FILE` is given.
Snapshots, session logs and dry-run output are written under the current
directory and are gitignored.

## Development

```
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest
```
