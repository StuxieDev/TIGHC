# Changelog

All notable changes to this project are documented here. Versioning follows
[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`): MAJOR bumps
mark breaking config-format/behavior changes, MINOR marks backward-compatible
feature additions, PATCH marks fixes.

## [3.9.15]

### Fixed
- **`commit.sh`/`commit.bat` staleness** — they hardcoded the version and
  commit message per release, so a forgotten update would tag the wrong
  version or skip tagging entirely. Both now read the version from
  `VERSION.md` dynamically, skip committing if nothing's staged, and skip
  tagging if the tag already exists.

## [3.9.14]

### Added
- **`commit.bat`/`commit.sh`** — pre-written commit+tag scripts, rewritten
  with each commit's exact message/tag before being run.

## [3.9.13]

### Added
- **`CONTRIBUTING.md`** — setup, manual verification steps (no automated
  test suite), and the versioning convention for PRs.

## [3.9.12]

### Changed
- **`version.txt` renamed to `VERSION.md`** — same single source of truth,
  read by `src/version.py`; only the filename changed.

## [3.9.11]

### Fixed
- **Priority: held bindings now loop correctly** — instead of one long held pulse
  with cancel-event preemption (which caused ownership races and stale cleanup),
  each held key now fires a short 0.1s pulse in a loop until the key is released.
  When a higher-priority binding is running on those channels, the lower-priority
  loop simply skips that iteration and retries 10ms later — no cancel-event
  cross-talk, no resume logic, no stale state. Sprint pressing while W is held
  now takes the channels within one 0.1s tick; releasing sprint lets W resume on
  the very next loop iteration.

## [3.9.10]

### Fixed
- **Priority preemption now works** — when a higher-priority binding's key is
  pressed while a lower-priority binding is already running (e.g. pressing Shift
  for Sprint while W is held for Movement), the lower-priority pulse is
  immediately cancelled and the higher-priority one takes over on those channels.
  When the higher-priority key is released, the engine automatically resumes the
  best still-held lower-priority binding, so going back to Movement after
  releasing Sprint happens without needing to re-press W. Priority order is
  configured in each profile's `priority` list (lower index = higher priority).

## [3.9.9]

### Added
- **Panic key action setting** — new "Panic key action" option in Settings with
  two modes:
  - **Suppress for duration** (default) — existing behaviour: forces haptics off
    for the configured hold duration, then resumes automatically.
  - **Stop engine completely** — pressing the panic key stops the engine entirely
    (same as clicking Stop), requiring a manual Start to resume. The Run tab
    updates automatically when the engine stops this way.

## [3.9.8]

### Fixed
- **Panic key logged** — pressing the panic key now logs which key was pressed
  and how long haptics will be suppressed (e.g. `PANIC key (F12) pressed -
  haptics forced off for 1.0s.`).
- **Panic timer resets on stop** — stopping the engine now clears the panic
  timer, so a panic pressed just before stopping can't carry over and suppress
  output when the engine is next started.

## [3.9.7]

### Added
- **Run tab profile override** — new "Override profile" dropdown in the Run tab
  lets you force a specific profile active while the engine is running, without
  needing the Test tab. Select "Auto (follow focus)" to go back to normal
  window-matching behaviour.

### Fixed
- **Test tab overrides no longer leak** — the Test tab's "Pin as active profile"
  checkbox and all manual channel holds are now automatically cleared when you
  navigate away from the Test tab.

## [3.9.6]

### Fixed
- **Per-channel independent randomisation** — each connected device/channel now
  gets its own separately rolled level within the binding's range when a
  binding fires. Previously all channels received the same rolled value,
  making every device trigger at identical intensity simultaneously.

## [3.9.5]

### Fixed
- **Config backfill** — if a user's `haptics.json` is missing any keys (e.g.
  after an update adds a new setting), `load_haptics_config()` now writes the
  merged result back to disk so the file stays complete. Previously missing
  keys were only filled in memory.

## [3.9.4]

### Changed
- **Age gate remembered** — once you confirm you're 18+, the confirmation is
  saved to `haptics.json` (`confirmed_age: true`) and the age gate is skipped
  on every subsequent launch. Applies to both the GUI and CLI.

## [3.9.3]

### Changed
- **Color-coded terminal log** — the Run tab log pane now looks like a dark
  terminal regardless of app theme. Each line is color-coded by type:
  profile headers (gold), binding definitions (blue), status lines (gray),
  channel/connection events (cyan), warnings (orange), errors and panic (red),
  success messages (green). Activation events get inline span coloring:
  `[binding_id]` in gold, `activated` in teal, `(key)` in blue, `[range]` in orange.

## [3.9.2]

### Changed
- **Profiles fetched from GitHub** — bundled profiles are no longer stored in
  this repo at all. On first launch (empty user profiles dir), TIGHC downloads
  all profiles from the [TIGHC-Profiles](https://github.com/TIGHC/Profiles)
  GitHub repo. If offline, the built-in Minecraft profile is used as a fallback.
- **"Update profiles from GitHub"** button in the Profiles tab downloads any
  profiles from TIGHC-Profiles not yet in your local profiles dir (existing
  profiles are never overwritten).
- **"Restore from GitHub..."** button in the Profiles tab re-downloads the
  selected profile from GitHub, overwriting your local copy.
- **"Browse profiles on GitHub"** link in Settings opens the TIGHC-Profiles
  repository in the browser.

## [3.9.1]

### Changed
- (Superseded by 3.9.2 — bundled profiles removed from this repo entirely.)

## [3.9.0]

### Changed
- **User data moved to AppData** — profiles, configs, and artwork cache now live
  in the platform-standard per-user app directory (`%APPDATA%\TIGHC` on Windows,
  `~/.local/share/TIGHC` on Linux/Steam Deck) instead of the repo root. This
  means git updates and submodule updates can no longer overwrite your profiles
  or settings.
- **Bundled profiles seed automatically** — on startup, any profile from the
  `profiles/` submodule that isn't yet in your user data dir is copied there,
  so new profiles from a submodule update appear without touching your edits.
- **"Restore to bundled" button** in the Profiles tab overwrites your copy of a
  profile with the bundled (submodule) version. Only available for profiles that
  have a bundled counterpart.
- **"Reset settings to defaults" button** in the Settings tab deletes your
  `haptics.json` and reverts all settings to the built-in defaults.
- **Folder buttons** in the Profiles tab and Settings tab open the user profiles
  dir, configs dir, user data dir, and bundled profiles dir in the system file
  explorer.

### Breaking changes
- On first launch after this update, TIGHC will seed a fresh copy of all
  bundled profiles to `%APPDATA%\TIGHC\profiles\` and read configs from
  `%APPDATA%\TIGHC\configs\`. Any `configs/` or `profiles/` edits you made
  under the repo root will not be migrated automatically - copy them manually
  if you want to keep them.

## [3.8.0]

### Changed
- **No background idle vibe** — the engine no longer applies a steady low-level
  vibe when no binding is active. Channels idle at 0 between activations.
  The `background_vibe` field has been removed from `Profile` and all
  `profile.json` files. Requires Profiles v1.3.0 or later.
- **Config renamed** — the main settings file is now `configs/haptics.json`
  (was `configs/haptics_config.json`). Rename the file on disk to keep your
  settings, or let TIGHC regenerate it with defaults on next launch.

### Breaking changes
- `profile.json` files with a `background_vibe` field will still load (the
  field is now silently ignored), but code that accesses `Profile.background`
  will break — that attribute no longer exists.
- `configs/haptics_config.json` is no longer read; rename it to
  `configs/haptics.json` to preserve your settings.

## [3.7.1]

### Changed
- **`version.txt`** added at the repo root as a single source of truth for the
  version number. `src/version.py` now reads from it instead of hardcoding the
  string, so external tools (the website, CI, etc.) can read the version without
  importing the package.

## [3.7.0]

### Changed
- **Single profile file** - each game profile is now stored as a single
  `profile.json` that combines the old `keybinds.json` + `ranges.json`. The
  `vibe` range is inline on each binding entry rather than in a separate file.
  `background_vibe` replaces the top-level `background.vibe` key from
  `ranges.json`. All existing profiles have been migrated.
- **GUI save/new/steamgriddb** operations now read and write `profile.json`
  instead of the two-file format.
- **`DEFAULT_MINECRAFT_PROFILE`** replaces the removed
  `DEFAULT_MINECRAFT_KEYBINDS` / `DEFAULT_MINECRAFT_RANGES` exports.

### Breaking changes
- Profiles with only `keybinds.json` / `ranges.json` will no longer be loaded.
  Migrate by merging them into a single `profile.json` (see the profiles
  submodule for the canonical format).

## [3.6.0]

### Changed
- **Unified binding model** - the continuous/pulse distinction has been
  removed. All bindings now work the same way: pressing a key fires the
  vibration at a rolled intensity from the binding's range; holding the key
  sustains it; releasing stops it immediately. `background_loop` is now
  responsible only for the idle background level and profile switching — all
  binding-driven vibration is event-driven.
- **Scroll wheel fires a short fixed burst** (0.15 s) since it has no release
  event; all other bindings hold until release.
- **Duration field removed** from the binding editor and saved profile files.
  The `duration` field in existing `ranges.json` files is read and displayed
  in the startup banner for backward compatibility but is no longer used by
  the engine.
- **Mode field removed** from the binding editor. Existing `keybinds.json`
  files with `"mode": "continuous"` or `"mode": "pulse"` continue to load
  correctly (the field is ignored).
- **Bindings tree** no longer has Mode or Duration columns.
- **Test tab** binding panel shows "Trigger (0.5s)" for all bindings
  (previously: "Hold" for continuous, "Trigger" for pulse).

### Breaking changes
- `Profile.continuous` and `Profile.pulse_bindings` removed; replaced by
  `Profile.bindings_by_key` (key token → `Binding`).
- `ContinuousBinding`, `PulseBinding` classes removed from `src/profiles.py`.
- Profiles saved by the GUI no longer include `mode` or `duration` fields.

## [3.5.0]

### Added
- **Pulse stops on key release** - pulse bindings no longer play for their full
  duration if the key/button is released early. Releasing cancels the pulse
  immediately and clears the channel's cooldown so the next press fires without
  delay. Test pulses (from the GUI's Test tab) are unaffected and still run to
  full duration.
- **Run log shows binding events** - pressing a key that activates a pulse or
  continuous binding now writes a line to the Run tab log (e.g.
  `[Pulse] space: triggered`, `[Continuous] movement: activated (w)`). Releasing
  a key mid-pulse logs the cancellation.
- **Priority field covers all bindings** - the "priority order" field on the
  Profiles tab now shows and saves IDs for both pulse and continuous bindings,
  not just continuous. Pulse IDs in the priority list are preserved across saves
  so their position is remembered if the mode is later changed to continuous.

## [3.4.0]

### Added
- **Exact window title matching** - profiles now support a `window_title_exact`
  flag (`keybinds.json`) that switches window matching from substring to full
  equality. Prevents a profile with title `"Grounded"` from activating when
  *Grounded 2* is focused. Exposed as an "Exact window title match" checkbox
  in the Profiles tab.

### Changed
- **Window title matching is now case-sensitive** - previously all window titles
  in profiles and the live window title from the OS were both forced to
  lowercase before comparison, making matching case-insensitive. The forced
  lowercasing has been removed: titles are now compared as-is, so profile
  `window_titles` entries must match the actual case of the game's window title.
  Existing profiles with lowercase entries (e.g. `"minecraft"`) will need to be
  updated if the game's window title uses a different case (e.g. `"Minecraft"`).

### Fixed
- **Profile settings "background vibe" field ignored % label** - the idle
  background vibe low/high entries in the Profile settings form were stored
  and read as raw 0.0–1.0 decimals despite the label saying "vibe % low/high".
  They now use the same 0–100 percentage scale as every other vibe field.
- **Binding dialog pre-populates vibe as decimal instead of percentage** - when
  editing an existing binding, the Vibe % fields showed the raw decimal (e.g.
  `0.30`) instead of the percentage value (`30`). Because the dialog divides by
  100 on save, this silently scaled every edited vibe down to ~1% intensity,
  making pulse bindings functionally invisible against the background. Fields
  now correctly show the percentage (e.g. `30`) when opened for editing, and
  the defaults for new bindings changed from `0.30`/`0.60` to `30`/`60`.
- **Binding dialog duration fields don't reflect mode changes** - the Duration
  fields stayed enabled/editable even when mode was set to "continuous", giving
  no visual indication that they were irrelevant and making it unclear whether
  duration values would be preserved when switching back to "pulse". Duration
  entries are now disabled whenever mode is "continuous" (values are retained
  in the fields, so switching back to "pulse" restores them) and re-enabled
  when mode is "pulse".
- **Save profile silently ignored TypeError from bad duration data** - if a
  pulse binding somehow had `None` duration values, `_compose_profile_files()`
  raised `TypeError` which was not caught by the `except ValueError` guard in
  `_on_save_profile()`, causing the save to fail with no error shown to the
  user. The guard now catches `TypeError` too, and `_compose_profile_files()`
  raises an explicit `ValueError` with a clear message for this case.

## [3.3.2]

### Fixed
- **`assets/icon.png`, `icon.ico`, and `logo.png` had an opaque dark
  (`#1E1E1E`) rounded-rect fill baked in** instead of a transparent
  background, so they showed a visible dark box wherever the app chrome
  wasn't that exact color. Removed the fill via a color-to-alpha un-blend
  (recovers true per-pixel alpha from the anti-aliased blend against the
  fill color, rather than a naive chroma key that would leave a dark
  fringe on the ring edges), and regenerated `icon.ico`'s multi-resolution
  frames from the fixed `icon.png`.

## [3.3.1]

### Fixed
- **Startup crash: `Failed to load profile 'assets'`** - the `profiles`
  submodule now ships its own `assets/` folder (icon/logo images for the
  TIGHC-Profiles repo itself), which `load_profiles()` was treating as a
  profile folder and rejecting for lacking `keybinds.json`/`ranges.json`.
  `load_profiles()` now skips any folder under `profiles/` that has
  *neither* file - not a profile at all - while still raising loudly on a
  folder that has only one of the two, which is a genuinely broken profile.

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
