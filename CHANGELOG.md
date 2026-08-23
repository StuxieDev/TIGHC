# Changelog

All notable changes to this project are documented here. Versioning follows
[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`): MAJOR bumps
mark breaking config-format/behavior changes, MINOR marks backward-compatible
feature additions, PATCH marks fixes.

## [3.0.0]

Project layout reorganized so it's no longer ambiguous which file to run.

### Changed
- **Breaking:** `haptics.py` and `steamgriddb.py` are merged into a single
  library module, `src/core.py`. Neither top-level file exists anymore.
- **Breaking:** the headless CLI is now its own file, `cli.py` - run
  `python cli.py` instead of `python haptics.py` to start the engine from
  the terminal. `src/core.py` is purely a library (imported by both `cli.py`
  and `gui.py`) and refuses to run directly - doing so now prints a pointer
  to `cli.py`/`gui.py` instead of silently doing nothing or starting
  anything unexpected.
- **Breaking:** per-install runtime config/state (`haptics_config.json`,
  `devices.json`, `steamgriddb_config.json`, `steamgriddb_cache.json`) now
  lives under a `configs/` folder instead of the repo root. The folder is
  created automatically on first run; existing installs should move their
  files into `configs/` (or just let the app regenerate them with defaults).
- `gui.py` is unchanged as the primary way to run TIGHC (`python gui.py`).

### Added
- **Linux / Steam Deck Desktop Mode support**: `get_foreground_window_title()`
  was hardcoded to Win32 APIs, so profile auto-switching (and therefore the
  whole engine) never worked outside Windows. Added an X11 implementation
  (via the new, Linux-only `python-xlib` dependency) alongside it, dispatched
  by platform at runtime. Requires an actual X11 session - Steam Deck
  Desktop Mode defaults to Wayland as of SteamOS 3.8, which doesn't expose
  focused-window info (or support pynput's global input capture) to other
  apps by design; switch once with `steamos-session-select
  plasma-x11-persistent`. See the new README section for details.
- **Cover image picker**: the Profiles tab's "Choose image..." button lets
  you browse every cover-art image SteamGridDB has for a profile's game (not
  just the automatically-chosen top-voted one) and pin a specific one -
  independent of `steamgriddb_id` (which pins the *game*, not the image), so
  you can override either one on its own. Stored as `steamgriddb_grid_id` in
  the profile's `keybinds.json`; the top-voted image stays the default when
  no image override is set, or if a previously-pinned one is later removed
  from SteamGridDB (that case now falls back to the default and logs why,
  instead of just failing).

### Fixed
- The 18+ age-gate dialog in `gui.py` could silently never appear on some
  Windows/Tk setups: it ran without error and its event loop kept working,
  but the window itself was never painted, so `python gui.py` looked like it
  hung with nothing on screen. Cause: the dialog was `transient()` to the
  main window while that window was still `withdraw()`n - a known Tk quirk
  where a transient child of a hidden/unmapped owner can fail to map on some
  window managers. `grab_set()` alone is enough to keep it modal, so the
  `transient()` call was removed.
- The age gate and main window now explicitly force themselves to the
  foreground on startup (`lift()` + a brief topmost toggle + `focus_force()`),
  since Windows doesn't always let a process launched from a terminal/IDE
  steal focus for a freshly-created window on its own.
- The Settings tab had no scrollbar, so on a window at or near the app's
  minimum size, the entire Cover art (SteamGridDB) section at the bottom -
  API key, enable checkbox, save button - was clipped below the visible area
  with no way to reach it. Wrapped the tab's content in a scrollable
  (mouse-wheel included) canvas, reused for any tab that outgrows the
  window's minimum size.
- Cover art fetching was silently broken for every profile: `get_grids()`
  asked SteamGridDB's API for `mimes=png`, which the API rejects outright
  with a 400 "Invalid mime type" (it wants the full MIME string,
  `image/png`) - and since `get_profile_artwork()` swallowed every error
  into a plain "no cover art available," this failure was invisible. Fixed
  the parameter, and gave `get_profile_artwork()` an optional log callback
  (wired to the Run tab log in `gui.py`) so a real fetch failure shows up
  instead of vanishing next time.

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
- A modern GUI look via the `sv_ttk` theme (new dependency), with a light/dark
  toggle.
- Optional per-profile cover art fetched from SteamGridDB (`steamgriddb.py`),
  shown on the Profiles and Test tabs once an API key is set in Settings.
  Profiles can pin an exact SteamGridDB game id (`steamgriddb_id` in
  `keybinds.json`) when the automatic name search picks the wrong game.

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
