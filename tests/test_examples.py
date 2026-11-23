"""The example files in examples/ must stay valid."""

from pathlib import Path

from netboxsync.config import load_device_file, load_device_types_file

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_device_example() -> None:
    df = load_device_file(EXAMPLES / "device.example.yaml")
    assert df.driver == "cisco-nxos"


def test_device_types_example() -> None:
    assert load_device_types_file(EXAMPLES / "device_types.example.yaml").device_types
