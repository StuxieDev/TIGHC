# Changelog

All notable changes to this project are documented here. Versioning follows
[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`): MAJOR bumps
mark breaking config-format/behavior changes, MINOR marks backward-compatible
feature additions, PATCH marks fixes.

## [2.0.0]

Renamed the project from "Minecraft-x-Lovense-intiface" / "Game Haptics" to
**The Intiface Game Haptics Controller (TIGHC)**, alongside a rewrite that
takes it from a single hardcoded Minecraft script to a general, GUI-driven,
multi-game haptics controller.

### Added
- Multi-game **profiles** (`profiles/<id>/{keybinds.json,ranges.json}`),
  auto-switched by matching the focused window's title. Ships with two
  example profiles: `minecraft` (seeded automatically) and `grounded2`.
- Per-keybind **mode**: `continuous` (sustained vibration while held) or
  `pulse` (one-shot randomized buzz per press) - configurable per binding
  instead of hardcoded per action.
- Per-motor/per-capability **device channels**: every output a device
  exposes (each motor's vibrate, oscillate, rotate, etc.) is independently
  controllable, so dual-motor toys or hybrid vibrate+oscillate devices can be
  driven separately. Channels get a persistent friendly nickname
  (`devices.json`), and a keybind can target one, several, or `"all"`.
- Interactive **Tkinter GUI** (`gui.py`): Devices (connect/scan/rename),
  Profiles (create/edit keybinds+ranges+device targets), Test (manual
  channel control and simulated keybind triggering, independent of the real
  focused window), Settings (global config), Run (start/stop + live log).
- **Test mode**: a per-channel slider/hold/pulse panel for driving a motor
  directly, plus a "pin this profile active" + per-binding trigger panel for
  exercising a profile's keybinds without needing the actual game focused.
- This changelog and an About tab in the GUI (version, versioning scheme,
  changelog, repository link).
- An 18+ age-verification gate, since this project controls adult haptic
  devices: a confirmation dialog before the GUI opens (`gui.py`), and a typed
  confirmation prompt before the headless engine starts (`haptics.py`).

### Changed
- Renamed `minecraft.py` to `haptics.py` and generalized it into a reusable
  engine module, importable by the GUI instead of being a standalone script.
- Global settings (`intiface_ws`, panic key, smoothing, auto-reconnect,
  timing) moved into `haptics_config.json`; keybinds/ranges moved out of
  hardcoded constants into per-profile JSON. `focus_gate` was removed as a
  standalone setting - profile `window_titles` matching now serves that role.

## Pre-2.0.0 (unversioned)

The original single-file script (`minecraft.py`): hardcoded Minecraft
keybinds, a single non-configurable output level sent identically to every
connected device, one `haptics_config.json` covering both global settings
and Minecraft-specific bindings.
