# The Intiface Game Haptics Controller (TIGHC)

> **18+ only.** This software connects to and controls adult haptic/sex toy
> devices based on your keyboard and mouse input while gaming. It is intended
> for use only by adults aged 18 or older. Both `gui.py` and `haptics.py`
> require you to confirm this before they'll start.

**Version 2.0.0** — see [CHANGELOG.md](CHANGELOG.md) for release history.
(Formerly "Game Haptics" / "Minecraft-x-Lovense-intiface".)

A haptic controller that links your keyboard/mouse input to a Buttplug/Intiface
toy. Originally Minecraft-only, it now supports multiple game **profiles**,
each with its own keybinds, intensity ranges, and per-motor device targeting,
driven from an interactive GUI. As much as I hate to say it this was made
with grok, chat gpt, and some claude. (I wish I was better at coding)

Repository: https://github.com/StuxieDev/TIGHC

## Quick start

```
python gui.py
```

This opens an interactive window: connect to Intiface, scan for devices,
assign nicknames to each motor/capability, build or edit game profiles, tune
global settings, and start/stop the haptics engine - all in one place.

Prefer the terminal? `python haptics.py` runs the same engine headlessly
using whatever's already on disk (see below) - no GUI, just hand-edit the
JSON files and restart to change things.

If `python` doesn't work, try `py` instead, or call your Python install by
full path (Windows users on OneDrive-synced folders sometimes need this).

## How it's organized

```
haptics.py                        # the engine (importable, also runs headless)
gui.py                            # interactive configurator + launcher
steamgriddb.py                    # optional cover-art fetching (see "Cover art" below)
haptics_config.json               # global settings (connection, panic key, smoothing, ...)
devices.json                      # remembers a nickname for each connected motor/capability
steamgriddb_config.json           # your SteamGridDB API key - keep this private, don't share/commit it
steamgriddb_cache.json            # resolved game ids / chosen art per profile, so repeat launches don't re-fetch
artwork_cache/                    # downloaded cover-art images
profiles/
  minecraft/                      # seeded automatically on first run
    keybinds.json                 # which keys/buttons do what, and how
    ranges.json                   # intensity/duration bands for each binding
  grounded2/                      # included as a second example profile
    keybinds.json
    ranges.json
  <your-other-game>/
    keybinds.json
    ranges.json
```

All of these are created with sensible defaults the first time you run
`haptics.py` or `gui.py`. Editing them by hand and using the GUI are fully
interchangeable - both just read/write the same files.

## Profiles: one per game

Each folder under `profiles/` is a **profile**: a game, its window title(s),
and its keybinds. The script watches whatever window currently has focus and
automatically switches to whichever profile matches, going idle when nothing
matches - so you can alt-tab between games and it just follows along. The
easiest way to add a new one is the GUI's "New profile..." button (it starts
you off with a copy of the Minecraft profile to edit); to do it by hand, copy
the `profiles/minecraft/` folder, rename it, and edit `window_titles` plus
the bindings.

Each binding in `keybinds.json` has:

- **`keys`** - the key(s)/button(s) that trigger it (`w`, `space`, `ctrl`,
  `mouse_left`, `mouse_right`, `mouse_middle`, `scroll`, digits, etc.)
- **`mode`** - either:
  - `"continuous"` - a sustained vibration for as long as the key/button is
    held (e.g. movement, sneaking, holding down the mouse button).
  - `"pulse"` - a single randomized buzz each time it's pressed, regardless
    of how long it's held (e.g. jump, drop, opening inventory).
- **`devices`** - which channel(s) this binding drives: a list of nicknames
  from `devices.json`, or `["all"]` (the default if omitted).
- **`enabled`** - set to `false` to turn a binding off without deleting it.

The matching entry in `ranges.json` (keyed by the same binding `id`) holds
the actual numbers: `vibe` (a `[low, high]` intensity band, 0.0-1.0) and,
for pulse bindings, `duration` (a `[low, high]` band in seconds). A random
value is rolled from the band each time, so nothing feels perfectly
repetitive. There's always a `background` entry too - the idle level used
whenever nothing more specific is happening.

`priority` in `keybinds.json` lists continuous binding ids in "first match
wins" order - useful when more than one could apply at once (e.g. attacking
should win over just sneaking).

A `grounded2` profile is included alongside `minecraft` as a second working
example (movement/sprint/crouch/attack/aim-block as continuous, jump/interact
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

`devices.json` remembers a friendly nickname for each channel so it stays
the same across reconnects and rescans. The GUI's Devices tab lists
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

Note: SteamGridDB's "official art" concept only really applies to logos and
icons, not the cover-art grids shown here - grids don't have an official/
fan-made distinction in their API, so this just uses whichever grid has the
most community votes.

`steamgriddb_config.json` holds your API key in plain text - treat it like a
password (don't commit it or share the file).

## Global settings

`haptics_config.json` covers everything that isn't game- or device-specific:
the Intiface WebSocket URL, a master randomization override, level
smoothing, the panic key (forces everything off for a moment), auto-reconnect,
and the background tick rate. Edit it (by hand or via the GUI's Settings tab)
and restart the app to pick up changes - these are read once at startup.

## About, versioning, and contact

The GUI's **About** tab shows the current version, a short project summary,
the versioning scheme, the changelog, and a link to the repository. This
project follows [Semantic Versioning](https://semver.org/)
(`MAJOR.MINOR.PATCH`) - see [CHANGELOG.md](CHANGELOG.md) for what changed in
each release. Questions, issues, or contributions:
https://github.com/StuxieDev/TIGHC
