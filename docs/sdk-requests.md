# netsdk requests from NetBoxSync

Requested 2026-11-25, against netsdk v0.1.0 (`cisco-ios`, `cisco-nxos`).

NetBoxSync writes interfaces to NetBox with their physical type, and models
hardware the way NetBox 4.6 recommends: every pluggable cage is a *module
bay*, and every optic, line card, supervisor and FEX is a *module* in a bay.
For that we need three getters that don't exist yet. The FEX getter
(`get_fexes`) already covers FEXes; nothing more is needed for them.

Interfaces are blocked on the first two getters. The third is needed for
modular chassis only, and can come later.

## What applies to all three

Follow the existing SDK conventions:

- One command for the whole device: never one command per interface. Every
  command goes through the CyberArk PSM jumphost, so a 300-port box would
  take minutes.
- `| json` on NX-OS where the command supports it.
- Interface names in full form, exactly as `get_interfaces()` prints them
  ("TenGigabitEthernet1/1/1", never "Te1/1/1"), so the results join on
  `Interface.name`.
- Speeds in kbit/s; `None` when the device doesn't say; values "as printed"
  where the field says so.
- `NotSupportedError` where a platform has no such command (e.g. an ISR
  router with no `show interfaces capabilities`).
- A core model where the data is vendor-neutral; Cisco-only extras go in an
  `IOS…` / `NXOS…` subclass, as elsewhere.

Size is a concern on large NX-OS boxes. `show interface | json` already
needed a 120 s timeout on some devices.

## 1. `get_interface_capabilities()` → `list[InterfaceCapabilities]`

**What it's for:** which ports are pluggable cages (the module bays) and what
they can run. The cage type sets the NetBox interface type of a port with
no optic in it (e.g. an empty SFP28 cage). Copper ports get their type from
here too.

**Commands:** NX-OS `show interface capabilities | json`. IOS
`show interfaces capabilities` (Catalyst switches; `NotSupportedError` on
routers without it).

**One entry per physical port.** No SVIs, loopbacks, tunnels or port-channels.

| Field | Type | Notes |
|---|---|---|
| `interface` | `str` | Full name, joins `Interface.name` |
| `pluggable` | `bool` | The port is a cage for a transceiver. NX-OS: "Type (SFP capable)" vs "Type (Non SFP)" |
| `type` | `str \| None` | As printed, e.g. "10Gbase-SR", "10/100/1000BaseTX", "QSFP-100G-SR4" |
| `speeds` | `tuple[int, ...]` | Supported speeds in kbit/s, ascending; empty when not printed |
| `breakout_capable` | `bool \| None` | If the device says so |

If the device names the cage form factor itself (SFP, SFP+, SFP28, QSFP+,
QSFP28, QSFP-DD, …), please add it as a `form_factor: str | None` (as
printed). If it doesn't, we'll derive the form factor from the speeds.

## 2. `get_transceivers()` → `list[Transceiver]`

**What it's for:** the optic in each cage becomes a NetBox module, and its
part ID is the module type. The optic also sets the interface's NetBox type
(e.g. `10gbase-sr`) when one is present.

**Commands:**
- NX-OS: `show interface transceiver | json`. Not `details`: DOM readings
  (light levels, temperatures, voltages) change every run, NetBox doesn't
  store them, and they make the output much larger.
- IOS: `show interfaces transceiver` has DOM only, and `show idprom` is
  per interface. On Catalyst, `show inventory` already lists every optic
  with its PID and serial. Please build the IOS getter from that, with the
  inventory name turned into the full interface name. Combine it with
  `show interfaces capabilities` to list empty cages as `present=False`.

**One entry per cage, present or not.** An empty cage is still a module bay.

| Field | Type | Notes |
|---|---|---|
| `interface` | `str` | Full name of the cage's interface, joins `Interface.name` |
| `present` | `bool` | An optic is inserted |
| `part_id` | `str \| None` | Cisco product ID, e.g. "SFP-10G-SR", "QSFP-100G-SR4-S" (NX-OS: "cisco product id"; IOS: inventory PID). This becomes the NetBox module type. `None` for third-party optics without one |
| `type` | `str \| None` | As printed, e.g. "10Gbase-SR", "SFP-H10GB-CU3M" |
| `vendor` | `str \| None` | As programmed, e.g. "CISCO-FINISAR" |
| `vendor_part_number` | `str \| None` | As programmed, e.g. "FTLX8571D3BCL-C2" |
| `vendor_revision` | `str \| None` | |
| `serial_number` | `str \| None` | |
| `nominal_bitrate` | `int \| None` | kbit/s, e.g. 10300000 for "10300 MBit/sec" |

All fields except `interface` and `present` are `None` when `present` is false.

**Breakout:** one entry per cage, not per lane, plus `interfaces:
tuple[str, ...]` listing every interface the optic serves (e.g.
"Ethernet1/49/1" through "/4"). Please tell us which name the cage entry
uses in breakout mode.

## 3. `get_modules()` → `list[Module]`

**What it's for:** line cards, supervisors, fabric modules and system
controllers in modular chassis (Nexus 9500/7000, Catalyst 9400/9600) become
NetBox modules in slot module bays. Interfaces on a line card are then
assigned to its module.

**Commands:** NX-OS `show module | json`. IOS `show module` (Catalyst
modular switches; `NotSupportedError` where it doesn't exist).

**One entry per occupied slot.** Also list empty slots as `present=False`
if the command shows them.

| Field | Type | Notes |
|---|---|---|
| `slot` | `str` | Slot as the device numbers it, e.g. "1", "27" |
| `present` | `bool` | |
| `part_id` | `str \| None` | Product ID, e.g. "N9K-X9736C-FX" |
| `description` | `str \| None` | As printed, e.g. "36x40/100G Ethernet Module" |
| `kind` | `str \| None` | As printed: "Supervisor Module", "Fabric Module", "System Controller", … |
| `serial_number` | `str \| None` | |
| `hardware_version` | `str \| None` | |
| `status` | `str \| None` | As printed: "ok", "active *", "ha-standby", "powered-dn", … |
| `ports` | `int \| None` | Port count, if printed |

A fixed switch lists itself as module 1 in `show module`. Keep it; we'll
tell it apart by comparing its part ID with the chassis model.

## Questions for the SDK team

1. **Which interfaces sit on which slot.** We plan to parse names:
   Ethernet<slot>/<port> on NX-OS, Ethernet<fex>/1/<port> on a FEX,
   Gi<switch>/<slot>/<port> on IOS. If there's a more reliable source (a
   command that maps ports to modules), please expose it instead.
2. **Breakout.** How does each platform name a broken-out cage in
   `show interface transceiver` and `capabilities`?
3. **Optics in line cards.** Do any optics in a line-card cage show up
   differently from optics in a fixed switch?

## Test data we can provide

Session logs from the fleet, for at least a fixed Nexus 9300, a FEX, a
fixed Catalyst and an ISR. Modular chassis output depends on what's in the
fleet.
