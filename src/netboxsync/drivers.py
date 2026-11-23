"""netsdk drivers this project knows, and the getters a snapshot holds.

Each getter is listed with the type it returns, so a snapshot read back from
disk is validated into the same SDK models a live run produced (D2). A test
checks these lists against the SDK classes, so a getter added to the SDK
shows up as a failing test rather than silently not being collected.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

from netsdk.cisco.ios import (
    IOSVLAN,
    CiscoIOS,
    IOSFacts,
    IOSInterface,
    IOSNeighbor,
    IOSSwitchport,
    IOSVTPStatus,
)
from netsdk.cisco.nxos import (
    NXOSFEX,
    NXOSVLAN,
    CiscoNXOS,
    NXOSFacts,
    NXOSInterface,
    NXOSNeighbor,
    NXOSPortChannel,
    NXOSSwitchport,
    NXOSVPCStatus,
    NXOSVTPStatus,
)
from netsdk.core import Device, InventoryItem, IPAddress


@dataclass(frozen=True)
class Driver:
    name: str
    device_class: type[Device]
    #: OS family; the platform family is "<vendor> <os_family>" (D10).
    os_family: str
    #: Getter name without `get_` (in collection order) -> return type.
    getters: Mapping[str, Any]


DRIVERS: Mapping[str, Driver] = {
    "cisco-ios": Driver(
        name="cisco-ios",
        device_class=CiscoIOS,
        os_family="IOS",  # IOS-XE too: same driver (D17)
        getters={
            "facts": IOSFacts,
            "interfaces": list[IOSInterface],
            "ip_addresses": list[IPAddress],
            "vlans": list[IOSVLAN],
            "switchports": list[IOSSwitchport],
            "inventory": list[InventoryItem],
            "neighbors": list[IOSNeighbor],
            "vtp_status": IOSVTPStatus,
        },
    ),
    "cisco-nxos": Driver(
        name="cisco-nxos",
        device_class=CiscoNXOS,
        os_family="NX-OS",
        getters={
            "facts": NXOSFacts,
            "interfaces": list[NXOSInterface],
            "ip_addresses": list[IPAddress],
            "vlans": list[NXOSVLAN],
            "switchports": list[NXOSSwitchport],
            "inventory": list[InventoryItem],
            "neighbors": list[NXOSNeighbor],
            "vtp_status": NXOSVTPStatus,
            "port_channels": list[NXOSPortChannel],
            "vpc_status": NXOSVPCStatus,
            "fexes": list[NXOSFEX],
        },
    ),
}


def sdk_version(driver: Driver) -> str:
    """Installed version of the netsdk package behind a driver."""
    return version(f"netsdk-{driver.name}")
