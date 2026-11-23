"""The driver registry must match the installed SDK exactly."""

import inspect
from typing import get_type_hints

import pytest

from netboxsync.drivers import DRIVERS, sdk_version


@pytest.mark.parametrize("name", sorted(DRIVERS))
def test_registry_lists_every_sdk_getter(name: str) -> None:
    driver = DRIVERS[name]
    sdk_getters = {
        attr.removeprefix("get_")
        for attr, _ in inspect.getmembers(driver.device_class, inspect.isfunction)
        if attr.startswith("get_") and not attr.endswith("_raw")
    }
    assert set(driver.getters) == sdk_getters


@pytest.mark.parametrize("name", sorted(DRIVERS))
def test_registry_types_match_sdk_return_types(name: str) -> None:
    driver = DRIVERS[name]
    for getter, expected in driver.getters.items():
        method = getattr(driver.device_class, f"get_{getter}")
        assert get_type_hints(method)["return"] == expected, getter


@pytest.mark.parametrize("name", sorted(DRIVERS))
def test_facts_is_collected_first(name: str) -> None:
    assert next(iter(DRIVERS[name].getters)) == "facts"


def test_sdk_version_is_pinned_release() -> None:
    assert sdk_version(DRIVERS["cisco-ios"]) == sdk_version(DRIVERS["cisco-nxos"]) == "0.1.0"
