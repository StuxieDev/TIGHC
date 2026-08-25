# Changelog

All notable changes to this project are documented here. Versioning follows
[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`): MAJOR bumps
mark breaking config-format/behavior changes, MINOR marks backward-compatible
feature additions, PATCH marks fixes.

## [3.3.0]

### Added
- **App icon and logo** (`assets/icon.png`/`icon.ico`, `assets/logo.png`) -
  TIGHC previously used Tk's stock feather icon everywhere, since `gui.py`
  never called `iconphoto()`/`iconbitmap()`. `gui.py` now sets the icon on
  the root window (with `default=True`, so the age gate and every other
  window inherit it too), and the About tab shows the logo banner instead
  of a plain-text header. Generated as a simple "pulse rings" mark, since
  that reads clearly as "haptics" even shrunk to a 16px titlebar icon.
- **Brand accent color** (`#7C5CFF`, the icon/logo's violet) threaded
  through the GUI: the two hyperlink-style labels (SteamGridDB key link,
  repository link), a new About tab website link, and the top bar's
  connection status indicator (now switches to the accent color while
  connected, instead of staying the neutral hint gray). The app's primary
  actions (Start, Connect + Scan, New profile..., and the Save buttons) are
  now rendered with a new `_make_accent_button()` helper using a classic
  `tk.Button` rather than `ttk.Button` - sv_ttk's own `Accent.TButton`
  style renders via baked image sprites
  (`ttk::style element create AccentButton.button image ...`), so a normal
  `style.configure(background=...)` on it has no visible effect; a classic
  Button's colors are plain widget options instead.
- **Website URL** (`https://tighc.stuxie.dev`) added as `WEBSITE_URL` in
  `src/metadata.py`, linked from the About tab and the README.
- **Author credit**: `AUTHOR_NAME`/`AUTHOR_URL` added to `src/metadata.py`
  (`StuxieDev`, https://github.com/StuxieDev), re-exported via
  `src/tighc.py`. Shown in `cli.py`'s startup banner ("... v3.3.0 - by
  StuxieDev"), in the GUI's About tab (name + a small avatar, both linking
  to the GitHub profile), and in the README (name + avatar, downloaded from
  `https://github.com/stuxiedev.png` to `assets/author.png`). The same
  avatar and README section were also added to the TIGHC-Profiles and
  TIGHC-Website repos for consistency.

## [3.2.1]

### Fixed
- The Devices tab's "Connect + Scan" button stayed enabled even after a
  successful connection, instead of becoming mutually exclusive with
  "Disconnect" the way the two are meant to be - clicking it again while
  already connected would silently open a *second* connection (`connect()`
  always builds a fresh `ButtplugClient`) without closing the first, rather
  than requiring "Disconnect" first. Also applied the same fix to the Run
  tab's "Start" button, which can establish the connection too if there
  wasn't one yet.

## [3.2.0]

### Changed
- **`src/core.py` (1600+ lines) split into focused modules** - `paths.py`
  (filesystem layout), `metadata.py` (project name/repo URL), `version.py`
  (version number + `get_version()`/`get_version_tuple()`), `ranges.py`
  (`VibeRange`/`DurationRange`/`PulseSpec`), `haptics_config.py`
  (`configs/haptics_config.json` load/apply + derived settings),
  `devices.py` (`configs/devices.json` registry + `DeviceChannel`),
  `profiles.py` (profile loading), `input.py` (keyboard/mouse handling +
  focused-window lookup), `engine.py` (`HapticsController` itself), and
  `steamgriddb.py` (cover-art fetching, unchanged from before the earlier
  merge into `core.py`). The one thing that had to be done carefully:
  `apply_haptics_config()` reassigns its module's globals via `global` for
  live-reload (see 3.1.0) - `engine.py` reads those through
  `haptics_config.NAME` (module-qualified), not a `from ... import NAME`
  copy, since only the former keeps seeing updates across the new module
  boundary. Verified this still works end-to-end after the split.
- **Renamed `src/core.py` to `src/tighc.py`** - once it became a pure
  re-export facade over the modules above rather than where the
  implementation lived, "core" no longer described it well. `cli.py` and
  `gui.py` now import from `src.tighc` instead of `src.core`; nothing else
  about how they use it changed, since the facade re-exports the exact same
  names as before.
- Every module in `src/` now refuses to run directly (`python src/engine.py`,
  etc.), printing a pointer to `cli.py`/`gui.py` instead - previously only
  `core.py` (now `tighc.py`) had this guard.

## [3.1.1]

### Fixed
- The Profiles tab's and Test tab's profile pickers displayed each
  profile's raw folder id (e.g. `cult_of_the_lamb`) instead of its actual
  display name (`"Cult of the Lamb"`, already set correctly in every
  profile's `keybinds.json`) - the dropdowns were populated straight from
  `self.controller.profiles.keys()` rather than each profile's `.name`.
  Added `_profile_display_name()`/`_profile_id_for_display()` to translate
  between the two everywhere a picker's selection is read or set, since
  the rest of the code keys profiles by id, not by their display name.
  `cli.py`'s startup banner and the engine's own profile-switch log
  already used `.name` correctly - only the GUI's pickers had this bug.

## [3.1.0]

### Added
- **Always-visible connection status indicator** in the top bar (not just
  the Run tab) - reflects live connection state ("Not connected" /
  "Connecting to ws://... ..." / "Connected - no devices found" /
  "Connected - N channel(s)") no matter which tab is open, updated
  immediately when you click Connect/Start and every 400ms afterward.
- **"Disconnect" button** on the Devices tab - cleanly disconnects from
  Intiface (stopping the engine first if it's running) without restarting
  the app, so you can reconnect afterward - e.g. after fixing something on
  the Intiface/repeater side, or to point at a different URL.
- **Settings now apply immediately.** `core.apply_haptics_config()`
  persists and applies every global setting (master override, smoothing,
  panic key, auto-reconnect, background tick) without an app restart. The
  Intiface WebSocket URL is the one exception - an already-open connection
  isn't automatically torn down and reopened just because the URL changed,
  so that one still needs a manual "Connect + Scan" (or Stop then Start).

### Changed
- The Devices tab's WebSocket URL field is now read-only, sourced from
  Settings - it was previously a second, independently-editable copy of the
  same value that could silently diverge from what was actually saved to
  `haptics_config.json`.

### Fixed
- Firing "Pulse" on the Test tab's manual channel controls before clicking
  Start on the Run tab left the channel stuck at the pulsed level forever
  instead of turning off after its duration. `_do_pulse()` relied on
  `background_loop()` to reset the channel afterward, which only runs once
  the engine is actually started. It now resets the channel itself when the
  engine isn't running, while real in-game pulses (engine running) still
  get the existing smooth transition instead of an added dip to zero.
- `HapticsController.shutdown()` didn't clear `self.client`/`self.channels`
  after disconnecting - harmless when it only ever ran once at app exit,
  but calling it more than once per run (needed for the new Disconnect
  button) left stale state: a later "Start" would wrongly think it was
  still connected, and the device list could keep showing channels from a
  connection that no longer existed.

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
  plasma-x11-persistent`. See the new [LINUX_GUIDE.md](LINUX_GUIDE.md) for
  the full setup walkthrough (session switching, dependencies, Intiface
  Central on Linux, troubleshooting), linked from the main README.
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
