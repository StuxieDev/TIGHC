# The Intiface Game Haptics Controller (TIGHC)

> **18+ only.** This software connects to and controls adult haptic/sex toy
> devices based on your keyboard and mouse input while gaming. It is intended
> for use only by adults aged 18 or older. Both `gui.py` and `cli.py`
> require you to confirm this before they'll start.

**Version 3.9.10** — see [CHANGELOG.md](CHANGELOG.md) for release history.

![TIGHC](assets/logo.png)

A haptic controller that links your keyboard/mouse input to a Buttplug/Intiface
toy. Originally Minecraft-only, it now supports multiple game **profiles**,
each with its own keybinds, intensity ranges, and per-motor device targeting,
driven from an interactive GUI. As much as I hate to say it this was made
with grok, chat gpt, and some claude. (I wish I was better at coding)

Website: https://tighc.stuxie.dev  
Repository: https://github.com/StuxieDev/TIGHC

## Author

<img src="assets/author.png" width="80" height="80" alt="StuxieDev" align="left" style="margin-right: 12px;">

**[StuxieDev](https://github.com/StuxieDev)**

<br>
<br>

## Quick start

```
git clone https://github.com/StuxieDev/TIGHC.git
python gui.py
```

This opens an interactive window: connect to Intiface, scan for devices,
assign nicknames to each motor/capability, build or edit game profiles, tune
global settings, and start/stop the haptics engine - all in one place.

Prefer the terminal? `python cli.py` runs the same engine headlessly using
whatever's already on disk (see below) - no GUI, just hand-edit the JSON
files and restart to change things.

If `python` doesn't work, try `py` instead, or call your Python install by
full path (Windows users on OneDrive-synced folders sometimes need this).

## Linux / Steam Deck

Windows is the primary target, but the engine also runs on Linux, including
Steam Deck's Desktop Mode - it needs an X11 session rather than Desktop
Mode's default Wayland one, since focused-window detection and global
input capture both require it. See **[LINUX_GUIDE.md](LINUX_GUIDE.md)** for
the full setup walkthrough (switching sessions, installing dependencies,
getting Intiface Central running, troubleshooting). Everything else in this
README - profiles, the GUI, cover art - applies identically on Linux.

A pre-compiled executable (Linux and Windows) that could enable Steam Deck
**Game Mode** support is in the works - see LINUX_GUIDE.md's note on this;
it's a roadmap item, not something available yet.

## How it's organized

```
src/
  tighc.py                        # re-export facade over the modules below - not meant to be run directly
  engine.py                       # HapticsController - the engine itself
  haptics.py                      # configs/haptics.json load/apply + derived settings
  steamgriddb.py                  # SteamGridDB cover-art fetching/caching
  profiles.py                     # profiles/<id>/{keybinds,ranges}.json loading
  devices.py                      # configs/devices.json registry + per-channel state
  input.py                        # keyboard/mouse normalization, focused-window lookup
  ranges.py                       # VibeRange/DurationRange/PulseSpec
  paths.py                        # filesystem layout (configs/, profiles/, artwork_cache/)
  metadata.py                     # project name/repo URL
  version.py                      # version number + get_version()/get_version_tuple()
cli.py                            # headless CLI entry point (imports src/tighc.py)
gui.py                            # interactive configurator + launcher (also imports src/tighc.py)
assets/                           # icon.png/icon.ico (window icon) and logo.png (About tab banner)
%APPDATA%\TIGHC\  (or ~/.local/share/TIGHC/ on Linux)  # per-user data, never touched by git
  profiles/                       # downloaded from TIGHC-Profiles on GitHub on first launch
  configs/
    haptics.json                  # global settings (connection, panic key, smoothing, ...)
    devices.json                  # remembers a nickname for each connected motor/capability
    steamgriddb_config.json       # your SteamGridDB API key - keep this private
    steamgriddb_cache.json        # resolved game ids / chosen art per profile
  artwork_cache/                  # downloaded cover-art images
  <your-other-game>/
    keybinds.json
    ranges.json
```

`configs/` and its contents, along with `artwork_cache/`, are created
automatically (with sensible defaults) the first time you run `cli.py` or
`gui.py` - you don't need to create them yourself. Editing the JSON files by
hand and using the GUI are fully interchangeable - both just read/write the
same files.

## Profiles: one per game

Your profiles live in `%APPDATA%\TIGHC\profiles\` (or `~/.local/share/TIGHC/profiles/`
on Linux). On first launch, TIGHC downloads all profiles from
[TIGHC-Profiles](https://github.com/StuxieDev/TIGHC-Profiles) on GitHub and
seeds them there. Profiles you edit are never overwritten automatically.

Each profile is a folder containing a single `profile.json` with the game's
window title(s), keybinds, and intensity ranges. The GUI watches whatever
window currently has focus and automatically switches to the matching profile -
so you can alt-tab between games and it just follows along.

The easiest way to add a new profile is the GUI's "New profile..." button.
Use "Update profiles from GitHub" in the Profiles tab to pick up any new
profiles added to the TIGHC-Profiles repo. Want to share a profile you've
made? Open a pull request on [TIGHC-Profiles](https://github.com/StuxieDev/TIGHC-Profiles).

Each binding in `profile.json` has:

- **`keys`** - the key(s)/button(s) that trigger it (`w`, `space`, `ctrl`,
  `mouse_left`, `mouse_right`, `mouse_middle`, `scroll`, digits, etc.)
- **`mode`** - either:
  - `"continuous"` - a sustained vibration for as long as the key/button is
    held (e.g. movement, sneaking, holding down the mouse button).
  - `"pulse"` - a single randomized buzz each time it's pressed, regardless
    of how long it's held (e.g. jump, drop, opening inventory).
- **`devices`** - which channel(s) this binding drives: a list of nicknames
  from `configs/devices.json`, or `["all"]` (the default if omitted).
- **`enabled`** - set to `false` to turn a binding off without deleting it.

Each binding's `vibe` is a `[low, high]` intensity band (0.0–1.0). A random
value is rolled from the band each activation, so nothing feels perfectly
repetitive.

`priority` lists binding ids in "first match wins" order - useful when more
than one binding could apply at once (e.g. attacking should win over just
moving).

A `grounded_2` profile is included alongside `minecraft` as a second working
example (movement/sprint/crouch/attack/aim-block, jump/interact
/inventory/hotbar as pulses). It's built from Grounded's standard default
keybinds rather than anything sequel-specific - if Grounded 2 changes any of
them, just edit the profile in the GUI (or the JSON directly) to match.

## Test mode

The GUI's **Test** tab lets you check things work without needing the actual
game running:

- **Manual channel control** - a slider and "Pulse"/"Hold" controls per
  connected channel, driving it directly regardless of any profile. Good for
  confirming a toy responds and getting a feel for what an intensity % feels
  like.
- **Simulate keybinds** - pick a profile and "Pin" it as the active one
  (overriding the real focused-window detection), then trigger its pulse
  bindings or toggle its continuous bindings' "Hold" to simulate the
  corresponding key/button being pressed - exercising the exact same code
  path real input does. Continuous "Hold" only does something once you've
  hit Start on the Run tab, since that's what's actually computing output
  levels each tick.

"Stop all testing" releases every manual hold/pin at once if you want to bail
out quickly.

## Devices: one channel per motor/capability

Toys aren't addressed as a single on/off unit - every capability a device
exposes (each motor's vibrate, oscillate, rotate, etc.) is its own
independently controllable **channel**. A single-motor toy is one channel; a
dual-motor toy (e.g. Lovense Edge) is two, each targetable separately by a
different keybind; a device that supports both vibrating and oscillating
exposes both as separate channels too.

`configs/devices.json` remembers a friendly nickname for each channel so it
stays the same across reconnects and rescans. The GUI's Devices tab lists
everything found on scan and lets you rename any of them; a keybind then
targets one, several, or `"all"` of these nicknames via its `devices` field.

Position-based outputs (e.g. stroker-style "move to position X") aren't
supported - only continuous intensity-style outputs (vibrate, rotate,
oscillate, constrict, etc.) fit the "roll a random level" model this script
uses.

## Cover art (SteamGridDB)

The Profiles and Test tabs can show each profile's box art, fetched from
[SteamGridDB](https://www.steamgriddb.com/). It's off by default:

1. Get a free API key at
   [steamgriddb.com/profile/preferences](https://www.steamgriddb.com/profile/preferences)
   (there's a link to this right in the Settings tab).
2. In the GUI's **Settings** tab, check "Show profile cover art", paste the
   key in, and click "Save cover art settings" - this takes effect
   immediately, no restart needed.
3. Switching profiles in the Profiles or Test tab then fetches (and caches)
   that game's top-voted cover art automatically.

By default a profile's art is found by searching SteamGridDB for its display
`name`, preferring a `verified` (SteamGridDB-curated) match. If that ever
finds the wrong game - an ambiguous title, a very new release not yet
well-indexed - use **"Change cover art..."** on the Profiles tab to search
and pin an exact game id (stored as `steamgriddb_id` in that profile's
`keybinds.json`; "Use automatic search instead" clears it again).

That still just uses the top-voted image for whichever game is resolved -
use **"Choose image..."** on the Profiles tab to browse every cover image
SteamGridDB has for that game and pin a specific one instead (stored as
`steamgriddb_grid_id`, independent of `steamgriddb_id` above - overriding
the game and overriding the image are separate choices, either or both).
"Use default (top-voted)" in that dialog clears the image override; if a
previously-pinned image is later removed from SteamGridDB, this falls back
to the default automatically and logs why on the Run tab.

Note: SteamGridDB's "official art" concept only really applies to logos and
icons, not the cover-art grids shown here - grids don't have an official/
fan-made distinction in their API, so the default/top-voted pick above just
means whichever grid has the most community votes.

`configs/steamgriddb_config.json` holds your API key in plain text - treat
it like a password (don't commit it or share the file).

## Global settings

`configs/haptics.json` covers everything that isn't game- or
device-specific: the Intiface WebSocket URL, a master randomization
override, level smoothing, the panic key (forces everything off for a
moment), auto-reconnect, and the background tick rate. Edit it by hand or
via the GUI's Settings tab - either way, saving takes effect immediately,
no restart needed. The one exception is the WebSocket URL: an already-open
connection isn't automatically torn down and reopened just because the URL
changed, so click **"Connect + Scan"** (or Stop then Start) after changing
it to actually reconnect using the new one.

The Devices tab shows this URL (read-only there - Settings is the only
place that changes it) alongside **Connect + Scan**, **Rescan**, and
**Disconnect** buttons, and the top bar always shows the current connection
state ("Not connected" / "Connecting..." / "Connected - N channel(s)") no
matter which tab you're on.

## About, versioning, and contact

The GUI's **About** tab shows the current version, a short project summary,
the versioning scheme, the changelog, and a link to the repository. This
project follows [Semantic Versioning](https://semver.org/)
(`MAJOR.MINOR.PATCH`) - see [CHANGELOG.md](CHANGELOG.md) for what changed in
each release. Questions, issues, or contributions:
https://github.com/StuxieDev/TIGHC
