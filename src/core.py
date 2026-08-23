"""The Intiface Game Haptics Controller (TIGHC) - engine + cover-art library.

Drives Buttplug/Intiface haptic devices from any game's key/mouse input, and
(optionally) fetches/caches per-profile cover art from SteamGridDB. This
module is a library only - imported by cli.py (headless) and gui.py
(interactive) - it isn't meant to be run directly.

=== Engine ===

Listens globally for keyboard and mouse events (via pynput) and turns them
into vibration levels sent to connected toys (via the buttplug client). It
doesn't read game state directly - just raw input - so every "binding" below
is really "this key/button is currently held" or "this key/button was just
pressed."

Supports multiple game profiles. Each profile lives in its own folder under
profiles/<name>/ and has two files:
  - keybinds.json - which keys/buttons do what, whether each one is a
    sustained ("continuous") vibration while held or a one-shot ("pulse")
    buzz per press, and which device(s) it should drive.
  - ranges.json   - the intensity (and, for pulses, duration) bands for
    each binding.

The script watches the foreground window and automatically switches to
whichever profile's window_titles match it, so haptics silently go idle
when you're not in a matched game.

Devices are addressed per capability, not per physical toy - every output a
device exposes (each motor's vibrate, oscillate, rotate, etc.) shows up as
its own independently controllable channel, so a dual-motor toy is two
channels and a hybrid vibrate+oscillate feature is two more on top of that.
Which physical channel is which is remembered by name in devices.json (see
load_device_registry), so a keybind can target "all" devices or a specific
one/two by nickname.

Global settings (connection, panic key, smoothing, etc.) live in
configs/haptics_config.json. All of the above are created with sensible
defaults on first run - edit them and restart to customize, or use gui.py
for an interactive configurator. No need to touch this source.

=== Cover art (SteamGridDB) ===

Fetches and caches cover-art thumbnails per game profile from
https://www.steamgriddb.com/api/v2 (confirmed against their real OpenAPI
spec at https://www.steamgriddb.com/static/openapi.yml - their docs *page*
blocks automated fetches, but the spec file itself doesn't) using a
user-supplied Bearer API key, which each user generates for free at
https://www.steamgriddb.com/profile/preferences.

Grid images (the classic box-art tile this fetches) have no "official" vs
"fan-made" distinction in the API - only Logos and Icons do (via a
`styles=official|custom` filter). So "prefer the official asset, otherwise
the first one available" simplifies for grids to just "take the top-scored
result", since the official-style filter in pick_best() below naturally
never matches anything and falls through - see pick_best().

Everything here is synchronous/blocking (plain urllib, no extra dependency)
by design; callers (gui.py) are responsible for running it off the Tk main
thread, e.g. via a daemon thread + root.after(0, ...) to marshal the result
back - the same pattern AsyncBridge uses for the engine's coroutines, just
without needing an event loop for something this simple.
"""

import asyncio
import ctypes
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from buttplug import ButtplugClient, DeviceOutputCommand, OutputType
from pynput import keyboard, mouse

# =============================================== PROJECT METADATA ===========================================
# Single source of truth for identity/version - both this module and gui.py
# (its About tab) read these rather than hardcoding them in two places.
# Versioning follows Semantic Versioning (semver.org): MAJOR.MINOR.PATCH,
# where MAJOR bumps mark breaking config-format/behavior changes, MINOR
# marks backward-compatible feature additions, and PATCH marks fixes.
# Bump __version__ here and add a matching entry to CHANGELOG.md together.
PROJECT_NAME = "The Intiface Game Haptics Controller"
PROJECT_SHORT_NAME = "TIGHC"
REPO_URL = "https://github.com/StuxieDev/TIGHC"
__version__ = "3.0.0"

# =============================================== FILESYSTEM LAYOUT ==========================================
# core.py lives at <repo root>/src/core.py, so every path below is resolved
# relative to the repo root (two levels up from this file), not to this
# file's own directory - otherwise config/profile lookups would end up
# looking inside src/ instead of the actual project root.
REPO_ROOT = Path(__file__).resolve().parent.parent

# Per-install runtime config/state - gitignored, never source (see
# .gitignore) - created with sensible defaults the first time it's needed.
CONFIGS_DIR = REPO_ROOT / "configs"
CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

# profiles/ is its own git repository (TIGHC-Profiles) checked out as a
# submodule at the repo root - see the main README's Quick start for how
# it's cloned/updated. Its location isn't affected by where core.py lives.
PROFILES_DIR = REPO_ROOT / "profiles"

# Downloaded cover-art images - generated/cached, not really a "config", so
# it stays at the repo root alongside profiles/ rather than inside configs/.
ARTWORK_CACHE_DIR = REPO_ROOT / "artwork_cache"

# ================================================== RANGES ==================================================

@dataclass(frozen=True)
class FloatRange:
    """A min/max band with a low <= high invariant. Base for both intensity and duration bands."""

    low: float
    high: float

    def __post_init__(self):
        # Catch typos in the config (e.g. swapped low/high) at startup
        # instead of failing obscurely mid-game.
        if self.low > self.high:
            raise ValueError(f"Invalid range ({self.low}, {self.high}): low must be <= high")

    def roll(self) -> float:
        """Pick a uniformly-random value between low and high (inclusive)."""
        return random.uniform(self.low, self.high)


@dataclass(frozen=True)
class VibeRange(FloatRange):
    """A 0.0-1.0 intensity band. Each trigger rolls a random value inside it."""

    def __post_init__(self):
        """Extend FloatRange's low<=high check with the 0.0-1.0 intensity bound."""
        super().__post_init__()
        if not (0.0 <= self.low and self.high <= 1.0):
            raise ValueError(f"Invalid intensity range ({self.low}, {self.high}); must be within 0.0-1.0")

    def __str__(self) -> str:
        """Percent display for banners/GUI, e.g. "40-65%"."""
        return f"{self.low * 100:.0f}-{self.high * 100:.0f}%"


@dataclass(frozen=True)
class DurationRange(FloatRange):
    """A band of seconds. Each pulse rolls a random duration inside it, so pulses don't all feel identical."""

    def __str__(self) -> str:
        """Seconds display for banners/GUI, e.g. "0.30-0.40s"."""
        return f"{self.low:.2f}-{self.high:.2f}s"


@dataclass(frozen=True)
class PulseSpec:
    """A one-shot binding's intensity band plus how long that pulse should last."""

    vibe: VibeRange
    duration: DurationRange

    def roll_duration(self) -> float:
        """Convenience shortcut for `self.duration.roll()` - how long this pulse's next firing should last."""
        return self.duration.roll()


# ========================================== GLOBAL CONFIG FILE I/O ==========================================
HAPTICS_CONFIG_PATH = CONFIGS_DIR / "haptics_config.json"

# Written to disk verbatim the first time the script runs. Every value here
# has a matching constant derived below, so this is the single source of
# truth for defaults - keep it in sync if you add a new setting. Per-game
# keybinds and ranges live in profiles/, not here - see load_profiles().
DEFAULT_HAPTICS_CONFIG = {
    "intiface_ws": "ws://127.0.0.1:12345",
    "master": {"enabled": False, "range": [0.20, 1.00]},
    "smoothing": {"enabled": True, "factor": 0.35},
    "panic_key": {"enabled": True, "key": "f12", "hold_duration": 1.0},
    "auto_reconnect": {"enabled": True, "cooldown": 5.0, "failure_threshold": 10},
    "timing": {"background_tick": 0.18},
}


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Layer a user's partial config over the defaults so missing keys fall back cleanly."""
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_haptics_config() -> dict:
    """
    Load configs/haptics_config.json, creating it with DEFAULT_HAPTICS_CONFIG
    if it doesn't exist yet. If it exists but only partially overrides the
    defaults (e.g. the user only changed `intiface_ws`), the rest is filled
    in via _deep_merge() so every key the rest of this module expects is
    always present. Falls back to the in-memory DEFAULT_HAPTICS_CONFIG
    (without touching disk) if the file can't be written or read.
    """
    if not HAPTICS_CONFIG_PATH.exists():
        try:
            HAPTICS_CONFIG_PATH.write_text(json.dumps(DEFAULT_HAPTICS_CONFIG, indent=2), encoding="utf-8")
            print(f"Created default config at {HAPTICS_CONFIG_PATH} - edit it and restart to customize.")
        except OSError as e:
            print(f"Could not write default config ({e}); using built-in defaults.")
        return DEFAULT_HAPTICS_CONFIG

    try:
        user_config = json.loads(HAPTICS_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"Could not read {HAPTICS_CONFIG_PATH} ({e}); using built-in defaults.")
        return DEFAULT_HAPTICS_CONFIG

    return _deep_merge(DEFAULT_HAPTICS_CONFIG, user_config)


HAPTICS_CONFIG = load_haptics_config()

INTIFACE_WS = HAPTICS_CONFIG["intiface_ws"]

MASTER_RANDOM_ENABLED = HAPTICS_CONFIG["master"]["enabled"]
MASTER_VIBE_RANGE = VibeRange(*HAPTICS_CONFIG["master"]["range"])

ENABLE_SMOOTHING = HAPTICS_CONFIG["smoothing"]["enabled"]
SMOOTHING_FACTOR = HAPTICS_CONFIG["smoothing"]["factor"]

ENABLE_PANIC_KEY = HAPTICS_CONFIG["panic_key"]["enabled"]
PANIC_KEY = HAPTICS_CONFIG["panic_key"]["key"].strip().lower()
PANIC_HOLD_DURATION = HAPTICS_CONFIG["panic_key"]["hold_duration"]

ENABLE_AUTO_RECONNECT = HAPTICS_CONFIG["auto_reconnect"]["enabled"]
RECONNECT_COOLDOWN = HAPTICS_CONFIG["auto_reconnect"]["cooldown"]
FAILURE_RECONNECT_THRESHOLD = HAPTICS_CONFIG["auto_reconnect"]["failure_threshold"]

BACKGROUND_TICK = HAPTICS_CONFIG["timing"]["background_tick"]


# ================================================ DEVICE REGISTRY ===========================================
# Devices are addressed per *capability* (one buttplug "feature" x one output
# type it supports), not per physical toy - a dual-motor device (e.g. Lovense
# Edge) exposes two independent vibrate features, and a feature that supports
# more than one output type (e.g. vibrate + oscillate) exposes each as its
# own channel too, so nothing is ever forced to move in lockstep with
# something else unless a keybind's "devices" list explicitly says so.
# devices.json remembers a friendly nickname for each (device name, feature
# index, output type) triple so nicknames survive reconnects/rescans;
# keybinds then target one or more nicknames, or "all".
DEVICES_PATH = CONFIGS_DIR / "devices.json"


def _slugify(text: str) -> str:
    """Turn arbitrary text (a device name, a profile display name, ...) into a lowercase_with_underscores id."""
    # Replace every non-alphanumeric character with "_", then collapse runs
    # of underscores and trim the ends so "Lovense Edge v2!" -> "lovense_edge_v2".
    slug = "".join(c.lower() if c.isalnum() else "_" for c in text).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "device"  # never return an empty string - callers use this as a dict key / filename


# Every output capability a feature exposes becomes its own channel, so a
# feature that can both vibrate and oscillate - or a device with several
# independent vibrating motors ("dual vibrating" toys) - exposes each as a
# separately nicknameable, separately targetable channel rather than forcing
# them to always move together. POSITION/POSITION_WITH_DURATION are excluded:
# those represent "move to an absolute position", not a continuous intensity
# level, so they don't fit the roll-a-random-level-every-tick model the rest
# of this script uses - a stroker-type device would need a different config
# shape entirely.
SUPPORTED_OUTPUT_TYPES = (
    OutputType.VIBRATE,
    OutputType.ROTATE,
    OutputType.OSCILLATE,
    OutputType.CONSTRICT,
    OutputType.SPRAY,
    OutputType.TEMPERATURE,
    OutputType.LED,
)


def load_device_registry() -> list:
    """
    Read devices.json's "channels" list: dicts with device_name,
    feature_index, output_type, description, and nickname. Returns an empty
    list (never raises) if the file is missing, unreadable, or not valid
    JSON - a fresh install or a corrupted file just means every channel gets
    treated as brand-new and re-registered with an auto-generated nickname.
    """
    if not DEVICES_PATH.exists():
        return []
    try:
        data = json.loads(DEVICES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("channels", [])


def save_device_registry(registry: list):
    """Write the full channel registry back to devices.json, overwriting whatever was there."""
    DEVICES_PATH.write_text(json.dumps({"channels": registry}, indent=2), encoding="utf-8")


def resolve_channel_nicknames(registry: list, entries: list) -> dict:
    """Map each (device_name, feature_index, output_type) in entries to a nickname.

    Reuses a saved nickname when the triple is already in the registry;
    otherwise auto-generates one (device name, plus the feature's
    description or a motorN suffix if the device has more than one feature,
    plus the output type itself if the device exposes more than one kind of
    output) and appends it to registry, saving to disk if anything changed.
    entries is a list of (device_name, feature_index, output_type, description).
    """
    by_key = {(e["device_name"], e["feature_index"], e["output_type"]): e["nickname"] for e in registry}
    existing_nicknames = set(by_key.values())

    device_output_types = {}
    device_feature_indices = {}
    for device_name, feature_index, output_type, _description in entries:
        device_output_types.setdefault(device_name, set()).add(output_type)
        device_feature_indices.setdefault(device_name, set()).add(feature_index)

    result = {}
    motor_numbers = {}  # (device_name, feature_index) -> assigned motorN number
    motor_counters = {}  # device_name -> next motor number to assign
    changed = False
    for device_name, feature_index, output_type, description in entries:
        key = (device_name, feature_index, output_type)
        if key in by_key:
            result[key] = by_key[key]
            continue

        parts = [_slugify(device_name)]
        if description:
            parts.append(_slugify(description))
        elif len(device_feature_indices[device_name]) > 1:
            motor_key = (device_name, feature_index)
            if motor_key not in motor_numbers:
                motor_counters[device_name] = motor_counters.get(device_name, 0) + 1
                motor_numbers[motor_key] = motor_counters[device_name]
            parts.append(f"motor{motor_numbers[motor_key]}")
        if len(device_output_types[device_name]) > 1:
            parts.append(_slugify(output_type))
        nickname = "_".join(parts)

        final = nickname
        n = 2
        while final in existing_nicknames:
            final = f"{nickname}_{n}"
            n += 1

        existing_nicknames.add(final)
        by_key[key] = final
        result[key] = final
        registry.append(
            {
                "device_name": device_name,
                "feature_index": feature_index,
                "output_type": output_type,
                "description": description,
                "nickname": final,
            }
        )
        changed = True

    if changed:
        save_device_registry(registry)
    return result


# ============================================== GAME PROFILES ===============================================
# Each profile is a folder under profiles/<id>/ with two files:
#   keybinds.json - which keys/buttons do what, their mode (continuous/pulse),
#                   and which device nickname(s) (or "all") they drive
#   ranges.json   - the vibe/duration bands for each binding, by id
#
# Profiles are matched against the foreground window title (see
# HapticsController._match_profile) so haptics automatically follow whatever
# game currently has focus, and go idle when nothing matches.

# Seeded to profiles/minecraft/ the first time the script runs (i.e. when
# profiles/ doesn't exist yet), reproducing the behavior this script used to
# have built in. Copy this folder to add another game.
DEFAULT_MINECRAFT_KEYBINDS = {
    "name": "Minecraft",
    "window_titles": ["minecraft"],
    "priority": ["attack", "use", "sneak", "sprint", "movement"],
    "bindings": [
        {"id": "movement", "keys": ["w", "a", "s", "d"], "mode": "continuous", "enabled": True, "devices": ["all"]},
        {"id": "sprint", "keys": ["ctrl"], "mode": "continuous", "enabled": True, "devices": ["all"]},
        {"id": "sneak", "keys": ["shift"], "mode": "continuous", "enabled": True, "devices": ["all"]},
        {"id": "attack", "keys": ["mouse_left"], "mode": "continuous", "enabled": True, "devices": ["all"]},
        {"id": "use", "keys": ["mouse_right"], "mode": "continuous", "enabled": True, "devices": ["all"]},
        {"id": "jump", "keys": ["space"], "mode": "pulse", "enabled": True, "devices": ["all"]},
        {"id": "pick_block", "keys": ["mouse_middle"], "mode": "pulse", "enabled": True, "devices": ["all"]},
        {"id": "drop", "keys": ["q"], "mode": "pulse", "enabled": True, "devices": ["all"]},
        {"id": "offhand", "keys": ["f"], "mode": "pulse", "enabled": True, "devices": ["all"]},
        {"id": "inventory", "keys": ["e"], "mode": "pulse", "enabled": True, "devices": ["all"]},
        {
            "id": "switch_item",
            "keys": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "scroll"],
            "mode": "pulse",
            "enabled": True,
            "devices": ["all"],
        },
    ],
}
DEFAULT_MINECRAFT_RANGES = {
    "background": {"vibe": [0.20, 0.35]},
    "movement": {"vibe": [0.40, 0.65]},
    "sprint": {"vibe": [0.50, 0.70]},
    "sneak": {"vibe": [0.30, 0.50]},
    "attack": {"vibe": [0.75, 1.00]},
    "use": {"vibe": [0.55, 0.80]},
    "jump": {"vibe": [0.70, 0.95], "duration": [0.30, 0.40]},
    "pick_block": {"vibe": [0.30, 0.45], "duration": [0.15, 0.25]},
    "drop": {"vibe": [0.25, 0.40], "duration": [0.15, 0.25]},
    "offhand": {"vibe": [0.25, 0.40], "duration": [0.15, 0.25]},
    "inventory": {"vibe": [0.15, 0.25], "duration": [0.15, 0.25]},
    "switch_item": {"vibe": [0.15, 0.30], "duration": [0.10, 0.15]},
}


@dataclass(frozen=True)
class ContinuousBinding:
    """A held-input binding: while any of `tokens` is pressed, `vibe` competes for output on `devices`."""

    tokens: frozenset
    vibe: VibeRange
    id: str
    devices: Optional[frozenset]  # None means "all channels"


@dataclass(frozen=True)
class PulseBinding:
    """A one-shot binding fired on press, scoped to `devices`."""

    spec: PulseSpec
    devices: Optional[frozenset]  # None means "all channels"


@dataclass(frozen=True)
class Profile:
    """A loaded game profile: what it's called, which window(s) it matches, and its bindings."""

    id: str
    name: str
    window_titles: list  # lowercased substrings matched against the foreground window title
    continuous: list  # ordered [ContinuousBinding, ...], first pressed+targeted match wins
    pulse_bindings: dict  # key/button token -> PulseBinding
    background: VibeRange
    bindings: list  # raw parsed bindings, in file order, for the startup banner
    # Optional, purely cosmetic: pins this profile to an exact SteamGridDB
    # game id for cover-art lookup (see get_profile_artwork() below),
    # bypassing the by-name search that could otherwise match the wrong game
    # (e.g. a sequel or spin-off with a similar title). None means "search
    # by name".
    steamgriddb_id: Optional[int] = None

    def matches(self, window_title_lower: str) -> bool:
        """True if this profile's window_titles has a substring match in the (already-lowercased) window title."""
        return any(t in window_title_lower for t in self.window_titles)

    def range_for(self, channel_nickname: str, pressed_keys: set) -> VibeRange:
        """
        Resolve the intensity range a specific channel should currently be
        driven at, given which keys/buttons are held.

        Walks `continuous` in priority order (the order _load_profile()
        already sorted them into); the first binding that both (a) targets
        this channel (its `devices` is None/"all", or contains this
        nickname) and (b) has at least one of its keys currently in
        `pressed_keys` wins. If nothing matches - nothing relevant is held -
        the profile's idle `background` range is returned instead.
        """
        for binding in self.continuous:
            if binding.devices is not None and channel_nickname not in binding.devices:
                continue
            if pressed_keys & binding.tokens:
                return binding.vibe
        return self.background


def _seed_default_profile():
    """Write profiles/minecraft/{keybinds,ranges}.json from the DEFAULT_MINECRAFT_* dicts above."""
    # Only ever called when PROFILES_DIR doesn't exist yet (see
    # load_profiles()), so this always creates a brand-new folder - it's not
    # meant to "reset" an existing, possibly user-edited minecraft profile.
    profile_dir = PROFILES_DIR / "minecraft"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "keybinds.json").write_text(json.dumps(DEFAULT_MINECRAFT_KEYBINDS, indent=2), encoding="utf-8")
    (profile_dir / "ranges.json").write_text(json.dumps(DEFAULT_MINECRAFT_RANGES, indent=2), encoding="utf-8")
    print(f"Created default profile at {profile_dir} - copy this folder to add more games.")


def _parse_devices_field(binding: dict) -> Optional[frozenset]:
    """
    Parse a binding's "devices" list into the internal target representation:
    `None` means "all channels" (also the default when the field is omitted
    entirely, for backward compatibility with profiles written before this
    field existed); otherwise a frozenset of lowercased nicknames. Raises
    ValueError if present but not a non-empty list - this is a structural
    config error, not something to silently paper over.
    """
    devices_field = binding.get("devices", ["all"])
    if not isinstance(devices_field, list) or not devices_field:
        raise ValueError(f"binding '{binding.get('id')}' devices must be a non-empty list")
    lowered = [d.lower() for d in devices_field]
    if "all" in lowered:
        return None
    return frozenset(lowered)


def _load_profile(profile_dir: Path) -> Profile:
    """
    Load and validate one profile folder (keybinds.json + ranges.json) into
    a Profile. Raises ValueError with a human-readable message for any
    structural problem (missing files, bad mode, missing ranges entry, an
    invalid range, etc.) - callers are expected to let this propagate rather
    than silently skip a broken profile, so mistakes surface immediately
    instead of causing quietly-wrong output later.
    """
    keybinds_path = profile_dir / "keybinds.json"
    ranges_path = profile_dir / "ranges.json"
    if not keybinds_path.exists() or not ranges_path.exists():
        raise ValueError("folder must contain both keybinds.json and ranges.json")

    keybinds = json.loads(keybinds_path.read_text(encoding="utf-8"))
    ranges = json.loads(ranges_path.read_text(encoding="utf-8"))

    name = keybinds.get("name", profile_dir.name)
    window_titles = [t.lower() for t in keybinds.get("window_titles", [])]
    if not window_titles:
        raise ValueError("keybinds.json needs at least one entry in window_titles")

    priority = keybinds.get("priority", [])

    seen_ids = set()
    parsed_bindings = []  # every binding, in file order, disabled ones included - used only for the banner/GUI display
    continuous_entries = {}  # id -> ContinuousBinding, enabled continuous bindings only
    pulse_bindings = {}  # key/button token -> PulseBinding, enabled pulse bindings only (a token can map to only one)

    # Single pass over every declared binding: validate its shape, look up
    # its numbers in ranges.json, and - if it's enabled - file it into
    # whichever runtime structure (continuous_entries or pulse_bindings)
    # HapticsController actually consults during play.
    for binding in keybinds.get("bindings", []):
        bid = binding["id"]
        if bid in seen_ids:
            raise ValueError(f"duplicate binding id '{bid}'")
        seen_ids.add(bid)

        mode = binding.get("mode")
        if mode not in ("continuous", "pulse"):
            raise ValueError(f"binding '{bid}' has invalid mode {mode!r} (must be 'continuous' or 'pulse')")

        keys = [k.lower() for k in binding.get("keys", [])]
        if not keys:
            raise ValueError(f"binding '{bid}' has no keys")
        if mode == "continuous" and "scroll" in keys:
            raise ValueError(f"binding '{bid}' is continuous but includes 'scroll', which has no held state")

        enabled = binding.get("enabled", True)
        target_devices = _parse_devices_field(binding)

        # The binding's own vibe/duration numbers live in ranges.json, keyed
        # by the same id - keeping "what triggers it" (keybinds.json)
        # separate from "how strong/long" (ranges.json).
        range_section = ranges.get(bid)
        if range_section is None:
            raise ValueError(f"binding '{bid}' has no matching entry in ranges.json")
        vibe = VibeRange(*range_section["vibe"])

        duration = None
        if mode == "pulse":
            if "duration" not in range_section:
                raise ValueError(f"pulse binding '{bid}' needs a 'duration' range in ranges.json")
            duration = DurationRange(*range_section["duration"])

        parsed_bindings.append(
            {
                "id": bid,
                "keys": keys,
                "mode": mode,
                "enabled": enabled,
                "vibe": vibe,
                "duration": duration,
                "devices": target_devices,
            }
        )

        if not enabled:
            continue  # still recorded in parsed_bindings above (for display), just excluded from runtime lookups
        if mode == "continuous":
            continuous_entries[bid] = ContinuousBinding(
                tokens=frozenset(keys), vibe=vibe, id=bid, devices=target_devices
            )
        else:
            # Every key/button this pulse binding lists triggers the same
            # PulseSpec+devices - e.g. switch_item's "1".."9" and "scroll"
            # all share one entry, keyed separately per token for fast
            # lookup in on_key_press()/on_mouse_scroll().
            spec = PulseSpec(vibe, duration)
            pulse_binding = PulseBinding(spec=spec, devices=target_devices)
            for k in keys:
                pulse_bindings[k] = pulse_binding

    background_section = ranges.get("background")
    if background_section is None or "vibe" not in background_section:
        raise ValueError("ranges.json needs a 'background' entry with a 'vibe' range")
    background = VibeRange(*background_section["vibe"])

    # Order continuous bindings by `priority` first (only ids that are
    # actually continuous+enabled matter here), then append anything left
    # over in whatever order it was declared - see Profile.range_for() for
    # how this ordering is used (first match wins).
    ordered_ids = [i for i in priority if i in continuous_entries]
    ordered_ids += [i for i in continuous_entries if i not in ordered_ids]
    continuous = [continuous_entries[i] for i in ordered_ids]

    return Profile(
        id=profile_dir.name,
        name=name,
        window_titles=window_titles,
        continuous=continuous,
        pulse_bindings=pulse_bindings,
        background=background,
        bindings=parsed_bindings,
        steamgriddb_id=keybinds.get("steamgriddb_id"),
    )


def load_profiles() -> dict:
    """
    Discover and load every profile folder under profiles/ into a
    dict keyed by profile id (the folder name), in alphabetical order.

    Seeds profiles/minecraft/ first if the whole profiles/ directory doesn't
    exist yet (a brand-new install). Any folder that fails to load raises
    RuntimeError immediately (wrapping the underlying ValueError/OSError/
    KeyError with the offending folder's name) rather than being silently
    skipped - a typo in one profile shouldn't produce a program that
    silently starts with fewer profiles than the user configured.
    """
    if not PROFILES_DIR.exists():
        try:
            _seed_default_profile()
        except OSError as e:
            print(f"Could not create default profile ({e}).")

    profiles = {}
    if not PROFILES_DIR.exists():
        return profiles

    for entry in sorted(PROFILES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        try:
            profile = _load_profile(entry)
        except (OSError, ValueError, KeyError) as e:
            raise RuntimeError(f"Failed to load profile '{entry.name}': {e}") from e
        profiles[profile.id] = profile

    return profiles


# Loaded once at import time. cli.py uses this module-level dict directly;
# gui.py instead makes its own copy via load_profiles() so it can freely
# mutate its own controller's profile set (add/edit/reload) without
# touching this one.
PROFILES = load_profiles()


def normalize_key(key) -> str:
    """
    Convert a pynput key object into the plain lowercase token used
    throughout this module and in profile JSON (e.g. "w", "space", "shift").

    pynput reports letter/number keys as "'a'" and special keys as
    "Key.space" / "Key.shift_l" / "Key.ctrl_r" - strip quotes, drop the
    "key." prefix, and drop left/right suffixes so "Key.shift_l" and
    "Key.shift_r" both normalize to the same "shift" used in profile configs.
    """
    raw = str(key).strip("'\"").lower()
    if raw.startswith("key."):
        raw = raw[len("key."):]
    for suffix in ("_l", "_r"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


def get_foreground_window_title() -> str:
    """Windows-only; returns "" (so no profile matches) anywhere else or on error."""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value
    except Exception:
        return ""


@dataclass
class InputState:
    """Tracks which keys/buttons are currently held down between events."""

    pressed_keys: set = field(default_factory=set)

    def set_held(self, token: str, held: bool):
        """Add or remove a token from pressed_keys - used for mouse buttons, which have no press/release keystrokes."""
        if held:
            self.pressed_keys.add(token)
        else:
            self.pressed_keys.discard(token)


@dataclass
class DeviceChannel:
    """One independently controllable output - a buttplug DeviceFeature's single capability plus its own state."""

    nickname: str
    feature: object  # buttplug.feature.DeviceFeature
    output_type: OutputType
    device_name: str = ""
    description: Optional[str] = None
    last_level: float = 0.0
    pulse_active: bool = False
    ignore_until: float = 0.0
    # Set by the GUI's manual "Hold" test control (see set_test_level()); while
    # this isn't None, background_loop() leaves the channel alone entirely so
    # a manual test level doesn't get immediately overwritten by real input.
    manual_override: Optional[float] = None


class HapticsController:
    """Owns the buttplug connection, per-channel output state, active profile, and input listeners."""

    def __init__(self, ws_url: str, profiles: dict, log_fn=print):
        """
        `profiles` is a dict of id -> Profile (from load_profiles()) that
        this controller reads from every tick; the GUI mutates its own
        controller's copy in place (add/edit/reload profiles) rather than
        replacing the dict wholesale, so background_loop() always sees
        the latest state without needing to be told about it explicitly.
        `log_fn` defaults to print() for headless use; gui.py overrides it
        with a callback that pushes into a thread-safe queue instead.
        """
        self.ws_url = ws_url
        self.profiles = profiles
        self.log = log_fn  # swap in a GUI-friendly callback instead of print(); see gui.py
        self.client: Optional[ButtplugClient] = None
        self.devices = []
        self.channels: dict = {}  # nickname -> DeviceChannel
        self.running = True
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.input_state = InputState()
        self.active_profile: Optional[Profile] = None

        # When set (by the GUI's Test tab), this profile is always "active"
        # regardless of which window is actually focused - lets you test a
        # profile's continuous bindings without alt-tabbing into the game.
        self.test_profile_override: Optional[Profile] = None

        self._panic_until = 0.0                # Output is forced to 0 until this timestamp
        self._consecutive_send_failures = 0    # Drives auto-reconnect
        self._last_reconnect_attempt = 0.0     # Cooldown gate so reconnects aren't spammed
        self._background_task: Optional[asyncio.Task] = None
        self._kb_listener: Optional[keyboard.Listener] = None
        self._mouse_listener: Optional[mouse.Listener] = None

    # ---------------------------------------------------------------- setup
    async def connect(self) -> bool:
        """
        Open a fresh connection to Intiface and do an initial device scan.
        Returns True if at least one usable channel was found, False on a
        connection failure or an empty scan (both are logged via self.log,
        not raised - callers just check the return value).
        """
        # Both the headless run() path and the GUI reach connect() first, so
        # this is the one place that's guaranteed to run on the loop that
        # should service schedule()'s cross-thread coroutine handoff.
        if self.loop is None:
            self.loop = asyncio.get_running_loop()

        self.client = ButtplugClient(PROJECT_SHORT_NAME)
        try:
            await self.client.connect(self.ws_url)
            self.log("Connected to Intiface!")
        except Exception as e:
            self.log(f"Connection failed: {e}")
            return False

        return await self.scan()

    async def scan(self) -> bool:
        """
        (Re)scan for devices on the existing connection and rebuild
        self.channels from whatever's found. If there's no existing
        connection yet, this just delegates to connect() instead (so GUI
        code can always call scan() without checking connection state
        first). Returns True if at least one channel exists afterward.
        """
        if not self.client:
            return await self.connect()

        # Scan for a fixed window rather than waiting for a signal, since
        # buttplug doesn't tell us when scanning has found everything nearby.
        await self.client.start_scanning()
        await asyncio.sleep(4)
        await self.client.stop_scanning()

        self.devices = list(self.client.devices.values())
        self.channels = self._build_channels(self.devices)
        if self.channels:
            self.log(f"Found {len(self.devices)} device(s), {len(self.channels)} channel(s)")
            return True
        self.log("No devices found")
        return False

    def _build_channels(self, devices: list) -> dict:
        """
        Turn a list of connected buttplug devices into {nickname: DeviceChannel},
        one entry per (device, feature, output type) combination found in
        SUPPORTED_OUTPUT_TYPES. Nicknames are resolved (and any new ones
        persisted to devices.json) via resolve_channel_nicknames(), so a
        channel keeps the same nickname across repeated scans/reconnects.
        """
        entries = []  # (device_name, feature_index, output_type_value, description, feature)
        for device in devices:
            for feature in device.features.values():
                for output_type in SUPPORTED_OUTPUT_TYPES:
                    if feature.has_output(output_type):
                        entries.append((device.name, feature.index, output_type.value, feature.description, feature))

        registry = load_device_registry()
        nickname_map = resolve_channel_nicknames(
            registry, [(dn, fi, ot, desc) for dn, fi, ot, desc, _ in entries]
        )

        channels = {}
        for device_name, feature_index, output_type_value, description, feature in entries:
            nickname = nickname_map[(device_name, feature_index, output_type_value)]
            channels[nickname] = DeviceChannel(
                nickname=nickname,
                feature=feature,
                output_type=OutputType(output_type_value),
                device_name=device_name,
                description=description,
            )
        return channels

    async def _attempt_reconnect(self):
        """
        Called by background_loop() once too many consecutive output sends
        have failed (see FAILURE_RECONNECT_THRESHOLD) - drops the current
        client (if any) and tries connect() again from scratch. Doesn't
        raise on failure; just logs and lets the next threshold trip retry.
        """
        self.log("Lost contact with device(s) - attempting to reconnect...")
        self._consecutive_send_failures = 0
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        if await self.connect():
            self.log("Reconnected!")
        else:
            self.log("Reconnect attempt failed, will retry.")

    # ----------------------------------------------------------- profiles
    def _match_profile(self, window_title_lower: str) -> Optional[Profile]:
        """Return the first loaded profile whose window_titles matches, or None if nothing matches."""
        # Profiles are matched in self.profiles' insertion order (i.e.
        # alphabetical by folder name, per load_profiles()) - if two
        # profiles' window_titles could both match the same window, the
        # alphabetically-first one wins.
        for profile in self.profiles.values():
            if profile.matches(window_title_lower):
                return profile
        return None

    def _update_active_profile(self):
        """
        Re-evaluate which profile should be "active" and update
        self.active_profile if it changed. Called once per background_loop()
        tick. A pinned test_profile_override always wins over real window
        matching (see its definition in __init__) so the GUI's Test tab can
        force a specific profile active regardless of what's focused.
        Switching profiles (including to/from None) always clears
        pressed_keys, since held tokens from the old profile's keybinds
        wouldn't mean anything under a different one anyway.
        """
        if self.test_profile_override is not None:
            matched = self.test_profile_override
        else:
            title = get_foreground_window_title().lower()
            matched = self._match_profile(title)
        if matched is self.active_profile:
            return
        self.active_profile = matched
        self.input_state.pressed_keys.clear()
        if matched:
            self.log(f"[Profile] Switched to: {matched.name}")
        else:
            self.log("[Profile] No matching game focused - haptics idle.")

    # ------------------------------------------------------- channel targets
    def _channels_for(self, target: Optional[frozenset]) -> list:
        """Resolve a binding's `devices` target (None = "all") to the actual list of currently-connected DeviceChannels."""
        if target is None:
            return list(self.channels.values())
        return [c for nickname, c in self.channels.items() if nickname in target]

    # -------------------------------------------------------------- output
    def roll(self, vibe_range: VibeRange) -> float:
        """Roll a level from the given range, unless master randomization overrides it."""
        # MASTER_RANDOM_ENABLED is a global "ignore every per-binding range
        # and just roll from one fixed band instead" override, set in
        # configs/haptics_config.json - useful for testing or for players
        # who want pure randomness regardless of what they're doing in-game.
        active_range = MASTER_VIBE_RANGE if MASTER_RANDOM_ENABLED else vibe_range
        return active_range.roll()

    def _smooth(self, channel: DeviceChannel, target: float) -> float:
        """Exponentially smooth a channel's output level toward `target`, or return `target` unchanged if smoothing is off."""
        # Closes part of the gap to the target each tick instead of jumping
        # straight there, so idle/continuous levels feel like they drift
        # rather than strobe every 180ms. Each channel keeps its own
        # last_level, so smoothing is independent per channel.
        if not ENABLE_SMOOTHING:
            return target
        return channel.last_level + (target - channel.last_level) * SMOOTHING_FACTOR

    async def _set_channel_level(self, channel: DeviceChannel, level: float):
        """
        Send `level` to one channel's underlying feature, update its
        last_level, and track the send's success/failure toward the
        auto-reconnect threshold. This is the single place every code path
        (background_loop, pulses, panic, GUI test controls) funnels through
        to actually talk to a device - so failure counting and the panic
        override only need to live in one spot.
        """
        # Panic always wins, even over a manually-held test level.
        if time.time() < self._panic_until:
            level = 0.0

        sent = False
        try:
            await channel.feature.run_output(DeviceOutputCommand(channel.output_type, level))
            sent = True
        except Exception:
            # A single failed send just counts toward the reconnect
            # threshold below rather than raising - a flaky BLE packet
            # shouldn't kill the whole loop.
            pass

        if self.channels:
            self._consecutive_send_failures = 0 if sent else self._consecutive_send_failures + 1
        channel.last_level = level

    async def _do_pulse(self, vibe_range: VibeRange, duration: float, target: Optional[frozenset]):
        """
        Shared implementation behind pulse() and test_pulse() - see those
        for the difference (whether an active profile is required).

        `target` is a binding's resolved devices field (None = all
        channels, else a frozenset of nicknames). One random level is
        rolled from `vibe_range` and sent to every currently-free channel in
        the target set simultaneously; channels already mid-pulse or still
        in another pulse's cooldown window are skipped for this trigger
        rather than queued or made to wait.
        """
        now = time.time()
        # Only one pulse "slot" per channel at a time - a channel already
        # mid-pulse (or in cooldown) is simply skipped for this trigger.
        targets = [c for c in self._channels_for(target) if now >= c.ignore_until and not c.pulse_active]
        if not targets:
            return

        level = self.roll(vibe_range)
        for channel in targets:
            channel.pulse_active = True
            channel.ignore_until = now + duration

        await asyncio.gather(*(self._set_channel_level(c, level) for c in targets))
        await asyncio.sleep(duration)
        for channel in targets:
            channel.pulse_active = False

    async def pulse(self, vibe_range: VibeRange, duration: float, target: Optional[frozenset]):
        """Short randomized vibration triggered by real input - a no-op while no profile is active."""
        if self.active_profile is None:
            return
        await self._do_pulse(vibe_range, duration, target)

    async def test_pulse(self, vibe_range: VibeRange, duration: float, target: Optional[frozenset]):
        """Same as pulse(), but for the GUI's manual test controls - fires even with no active profile."""
        await self._do_pulse(vibe_range, duration, target)

    async def set_test_level(self, nickname: str, level: float):
        """Manually pin one channel to `level` until clear_test_level() - for GUI testing only."""
        channel = self.channels.get(nickname)
        if channel is None:
            return
        channel.manual_override = level
        await self._set_channel_level(channel, level)

    async def clear_test_level(self, nickname: str):
        """Release a manual test hold, letting background_loop() drive the channel normally again."""
        channel = self.channels.get(nickname)
        if channel is None:
            return
        channel.manual_override = None

    async def panic(self):
        """Immediately force every channel's output off, for a short hold."""
        self._panic_until = time.time() + PANIC_HOLD_DURATION
        await asyncio.gather(*(self._set_channel_level(c, 0.0) for c in self.channels.values()))
        self.log("PANIC - haptics forced off.")

    async def background_loop(self):
        """
        The main output loop: re-evaluates the active profile and every
        channel's target level once every BACKGROUND_TICK seconds, for as
        long as self.running is True. Started by start_engine() and left
        running as an asyncio.Task; stop_engine() cancels it.

        Each tick, per channel: if a pulse or a manual test hold currently
        "owns" that channel, it's left alone; otherwise its level is
        resolved from the active profile's continuous bindings (or the
        idle background range if nothing's held), smoothed, and sent. This
        is also where the auto-reconnect threshold is checked.
        """
        while self.running:
            self._update_active_profile()

            if self.active_profile is None:
                # Nothing matches the focused window (and no test profile is
                # pinned) - zero everything except channels a pulse or a
                # manual test hold is currently in charge of.
                for channel in self.channels.values():
                    if not channel.pulse_active and channel.manual_override is None:
                        await self._set_channel_level(channel, 0.0)
                await asyncio.sleep(BACKGROUND_TICK)
                continue

            now = time.time()
            for channel in self.channels.values():
                if channel.pulse_active or now < channel.ignore_until or channel.manual_override is not None:
                    continue
                target_vibe = self.active_profile.range_for(channel.nickname, self.input_state.pressed_keys)
                level = self._smooth(channel, self.roll(target_vibe))
                await self._set_channel_level(channel, level)

            if (
                ENABLE_AUTO_RECONNECT
                and self._consecutive_send_failures >= FAILURE_RECONNECT_THRESHOLD
                and time.time() - self._last_reconnect_attempt >= RECONNECT_COOLDOWN
            ):
                self._last_reconnect_attempt = time.time()
                await self._attempt_reconnect()

            await asyncio.sleep(BACKGROUND_TICK)

    # --------------------------------------------------------------- input
    def schedule(self, coro):
        """
        Hand a coroutine off to the asyncio loop from any thread.

        pynput's keyboard/mouse listeners each run on their own OS thread,
        not the asyncio event loop, so they can't just `await` something -
        every on_key_press()/on_mouse_click()/on_mouse_scroll() callback
        below calls this instead of awaiting directly. No-ops once
        self.running is False (e.g. mid-shutdown), so a straggling input
        event can't schedule work against a loop that's going away.
        """
        if self.running and self.loop:
            asyncio.run_coroutine_threadsafe(coro, self.loop)

    def on_key_press(self, key):
        """pynput callback: track the key as held, handle the panic key, and fire a pulse if it's a pulse-mode binding."""
        try:
            k = normalize_key(key)
        except Exception:
            return
        self.input_state.pressed_keys.add(k)

        if ENABLE_PANIC_KEY and k == PANIC_KEY:
            self.schedule(self.panic())
            return

        # Pulse-mode bindings fire on press; continuous-mode bindings are
        # picked up by background_loop() via pressed_keys instead.
        if self.active_profile is None:
            return
        pulse_binding = self.active_profile.pulse_bindings.get(k)
        if pulse_binding:
            self.schedule(self.pulse(pulse_binding.spec.vibe, pulse_binding.spec.roll_duration(), pulse_binding.devices))

    def on_key_release(self, key):
        """pynput callback: stop tracking the key as held."""
        try:
            k = normalize_key(key)
        except Exception:
            return
        self.input_state.pressed_keys.discard(k)

    def on_mouse_click(self, _x, _y, button, pressed):
        """
        pynput callback for left/right/middle mouse button press+release.
        Mouse buttons have no natural "held" concept the way keys do, so
        this both tracks the button as held (for continuous bindings, via
        set_held) and fires a pulse on press (for pulse bindings) - whether
        either actually does anything depends on how the active profile
        configured that button. `_x`/`_y` (cursor position) are unused but
        required by pynput's callback signature.
        """
        token = {
            mouse.Button.left: "mouse_left",
            mouse.Button.right: "mouse_right",
            mouse.Button.middle: "mouse_middle",
        }.get(button)
        if token is None:
            return

        self.input_state.set_held(token, pressed)
        if pressed and self.active_profile is not None:
            pulse_binding = self.active_profile.pulse_bindings.get(token)
            if pulse_binding:
                self.schedule(
                    self.pulse(pulse_binding.spec.vibe, pulse_binding.spec.roll_duration(), pulse_binding.devices)
                )

    def on_mouse_scroll(self, _x, _y, _dx, _dy):
        """
        pynput callback for the scroll wheel. Scrolling has no "held" state
        (it's a series of discrete tick events), so it can only ever be
        bound as a pulse - see the "scroll" validation in _load_profile().
        `_x`/`_y`/`_dx`/`_dy` (position and scroll delta) are unused but
        required by pynput's callback signature.
        """
        if self.active_profile is None:
            return
        pulse_binding = self.active_profile.pulse_bindings.get("scroll")
        if pulse_binding:
            self.schedule(
                self.pulse(pulse_binding.spec.vibe, pulse_binding.spec.roll_duration(), pulse_binding.devices)
            )

    # ----------------------------------------------------------------- run
    @staticmethod
    def _devices_label(devices: Optional[frozenset]) -> str:
        """Render a binding's resolved devices target as a display string for the startup banner."""
        return "all" if devices is None else ",".join(sorted(devices))

    @classmethod
    def _binding_line(
        cls, label: str, enabled: bool, vibe_range: VibeRange, duration_range: Optional[DurationRange], devices: Optional[frozenset]
    ) -> str:
        """Format one banner line for a binding (or the idle/background level, passed with duration_range=None)."""
        if not enabled:
            return f"  - {label:<32} -> disabled"
        target = "" if devices is None else f" [{cls._devices_label(devices)}]"
        if duration_range is not None:
            return f"  - {label:<32} -> {vibe_range} pulse ({duration_range}){target}"
        return f"  - {label:<32} -> {vibe_range}{target}"

    @staticmethod
    def _status_line(label: str, value: str) -> str:
        """Format one banner line for a global on/off-style status (panic key, auto-reconnect, smoothing)."""
        return f"- {label:<24} -> {value}"

    def print_banner(self):
        """
        Log a human-readable summary of the current setup right after the
        engine starts: every loaded profile's bindings and their resolved
        ranges/devices, which channels are connected, and the global
        settings in effect. Purely informational - nothing here affects
        behavior, it's just what a headless run prints to the console (or
        what the GUI's Run tab log shows after clicking Start).
        """
        self.log(f"{PROJECT_SHORT_NAME} v{__version__} active - {PROJECT_NAME}")
        if MASTER_RANDOM_ENABLED:
            self.log(f"- MASTER RANDOM ON        -> every binding rolls {MASTER_VIBE_RANGE} (per-binding ranges ignored)")

        if self.channels:
            self.log(f"Channels ({len(self.channels)}): {', '.join(sorted(self.channels))}")
        else:
            self.log("No channels connected - haptics will stay idle.")

        if not self.profiles:
            self.log(f"No profiles found in {PROFILES_DIR} - haptics will stay idle.")
        for profile in self.profiles.values():
            self.log(f"[{profile.name}]  (window match: {'/'.join(profile.window_titles)})")
            self.log(self._binding_line("Idle", True, profile.background, None, None))
            for b in profile.bindings:
                label = f"{b['id']} ({'+'.join(b['keys'])})"
                self.log(self._binding_line(label, b["enabled"], b["vibe"], b["duration"], b["devices"]))

        self.log(self._status_line("Profile switching", "automatic, based on the focused window"))
        if ENABLE_PANIC_KEY:
            self.log(self._status_line(f"Panic key ({PANIC_KEY.upper()})", f"forces output off for {PANIC_HOLD_DURATION:.1f}s"))
        else:
            self.log(self._status_line("Panic key", "disabled"))
        self.log(self._status_line("Auto-reconnect", "enabled" if ENABLE_AUTO_RECONNECT else "disabled"))
        self.log(self._status_line("Level smoothing", f"enabled (factor {SMOOTHING_FACTOR})" if ENABLE_SMOOTHING else "disabled"))
        self.log(f"Global config: {HAPTICS_CONFIG_PATH}")
        self.log(f"Profiles dir:  {PROFILES_DIR}")
        self.log(f"Devices file:  {DEVICES_PATH}")

    def start_engine(self):
        """Start the background output loop and input listeners. Safe to call once per connection."""
        if self._background_task and not self._background_task.done():
            return
        self.running = True
        self._background_task = asyncio.create_task(self.background_loop())

        # pynput listeners run in background threads and call our handlers
        # from there; they stay alive independently of the asyncio loop.
        self._kb_listener = keyboard.Listener(on_press=self.on_key_press, on_release=self.on_key_release)
        self._mouse_listener = mouse.Listener(on_click=self.on_mouse_click, on_scroll=self.on_mouse_scroll)
        self._kb_listener.start()
        self._mouse_listener.start()

        self.print_banner()

    async def stop_engine(self):
        """Stop input listeners and the background loop, forcing every channel off. Connection stays open."""
        self.running = False
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except (asyncio.CancelledError, Exception):
                pass
            self._background_task = None
        if self._kb_listener:
            self._kb_listener.stop()
            self._kb_listener = None
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        await asyncio.gather(*(self._set_channel_level(c, 0.0) for c in self.channels.values()))

    async def shutdown(self):
        """Full teardown: stop the engine and disconnect from Intiface."""
        await self.stop_engine()
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass

    async def run(self):
        """Headless entry point: connect, run until Ctrl+C, then shut down."""
        if not await self.connect():
            return
        self.start_engine()
        try:
            # Nothing to do here but keep the event loop alive; all the real
            # work happens in background_loop() and the input callbacks.
            while self.running:
                await asyncio.sleep(0.5)
        finally:
            await self.shutdown()


# ============================================ COVER ART (SteamGridDB) =======================================
# See the module docstring above for the API/Cloudflare/threading notes.
STEAMGRIDDB_API_BASE = "https://www.steamgriddb.com/api/v2"
# steamgriddb.com sits behind Cloudflare, which blocks requests carrying
# Python's default urllib User-Agent outright (Cloudflare error 1010) before
# they ever reach the actual API - confirmed by testing directly: the same
# request that gets a 403 "error code: 1010" with no/default User-Agent gets
# a normal 401 (real API auth error) once a browser-like one is set. Every
# request this module makes needs this header, not just for looking legit,
# but because omitting it makes every call fail regardless of API key.
STEAMGRIDDB_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# Kept separate from haptics_config.json (unlike every other setting)
# because it holds a personal API credential - easier to keep private/
# gitignored on its own than to mix it into a file that's otherwise safe
# to share.
STEAMGRIDDB_CONFIG_PATH = CONFIGS_DIR / "steamgriddb_config.json"
# Maps profile id -> resolved game id / chosen grid / local file, so repeat
# launches don't re-search or re-download unless something actually changed.
STEAMGRIDDB_CACHE_PATH = CONFIGS_DIR / "steamgriddb_cache.json"

DEFAULT_STEAMGRIDDB_CONFIG = {"enabled": False, "api_key": ""}


def load_steamgriddb_config() -> dict:
    """Load steamgriddb_config.json (API key + enabled flag), or DEFAULT_STEAMGRIDDB_CONFIG if the file is missing/unreadable."""
    if not STEAMGRIDDB_CONFIG_PATH.exists():
        return dict(DEFAULT_STEAMGRIDDB_CONFIG)
    try:
        data = json.loads(STEAMGRIDDB_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_STEAMGRIDDB_CONFIG)
    return {**DEFAULT_STEAMGRIDDB_CONFIG, **data}


def save_steamgriddb_config(config: dict):
    """Persist the API key + enabled flag. Does not validate the key - a bad key just fails later, at fetch time."""
    STEAMGRIDDB_CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def _load_steamgriddb_cache() -> dict:
    """Load steamgriddb_cache.json (profile id -> resolved game/grid/file info), or {} if missing/unreadable."""
    if not STEAMGRIDDB_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(STEAMGRIDDB_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_steamgriddb_cache(cache: dict):
    """Persist the full cache dict back to steamgriddb_cache.json."""
    STEAMGRIDDB_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _steamgriddb_api_get(api_key: str, path: str, params: Optional[dict] = None) -> dict:
    """GET one SteamGridDB API endpoint (JSON only - not for downloading the images themselves) and return its parsed body."""
    url = STEAMGRIDDB_API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {api_key}", "User-Agent": STEAMGRIDDB_USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def search_game(api_key: str, term: str) -> list:
    """Search for games by name. Returns [{id, name, types, verified}, ...], already ranked by the API's own relevance order."""
    data = _steamgriddb_api_get(api_key, f"/search/autocomplete/{urllib.parse.quote(term)}")
    return data.get("data", [])


def get_grids(api_key: str, game_id: int) -> list:
    """
    Fetch grid (cover-art) images for a game id, restricted to PNG so
    Tkinter's built-in PhotoImage can display them directly - no Pillow
    dependency needed. Returns [] (not an error) if the game has no grids.
    """
    try:
        # The API wants the full MIME type ("image/png"), not a bare
        # extension ("png") - the latter gets rejected outright with a 400
        # "Invalid mime type", which get_profile_artwork's blanket
        # exception handler then silently swallows into "no cover art"
        # with zero indication of what actually went wrong.
        data = _steamgriddb_api_get(api_key, f"/grids/game/{game_id}", {"mimes": "image/png"})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    return data.get("data", [])


def pick_best(assets: list) -> Optional[dict]:
    """
    Prefer an asset whose style is "official"; otherwise the first one.

    Grids/Heroes never have an "official" style (only Logos/Icons do), so
    for grids this filter always comes up empty and silently falls through
    to `assets[0]` - which is exactly "first available", since the API
    already returns grids ranked by community score. No grid-specific
    special-casing needed; this same function works correctly if this
    module is ever extended to fetch logos/icons instead.
    """
    if not assets:
        return None
    official = [a for a in assets if a.get("style") == "official"]
    return (official or assets)[0]


def _resolve_game_id(api_key: str, profile_name: str, override_id: Optional[int]) -> Optional[int]:
    """An explicit override always wins; otherwise search by name and prefer a `verified` (SGDB-curated) match."""
    if override_id is not None:
        return override_id
    results = search_game(api_key, profile_name)
    if not results:
        return None
    verified = [g for g in results if g.get("verified")]
    return (verified or results)[0]["id"]


def get_profile_artwork(
    profile_id: str, profile_name: str, override_id: Optional[int] = None, force_refresh: bool = False, log_fn=None
) -> Optional[Path]:
    """
    Return a local file path to this profile's cached cover-art PNG,
    resolving/fetching/caching it from SteamGridDB as needed.

    Returns None if the integration is disabled, no API key is configured,
    nothing could be found, or any network/API error occurs along the way -
    callers should treat None as "no artwork available right now" and just
    not show an image, not as something to surface as an error. If `log_fn`
    is given, it's called with a one-line reason on the network/API-error
    path specifically (not on "disabled"/"nothing found", which are normal,
    expected outcomes) - without this, a real cause like a bad API key or a
    malformed request just looked like "no cover art" with no way to tell
    why (see the get_grids() mimes-parameter bug this caught).

    The on-disk cache entry is only trusted if it still matches what's
    being asked for now (the same override_id, or - with no override - the
    same profile_name); renaming a profile or setting/changing its
    steamgriddb_id invalidates the old entry and triggers a fresh lookup.
    """
    config = load_steamgriddb_config()
    if not config.get("enabled") or not config.get("api_key"):
        return None
    api_key = config["api_key"]

    cache = _load_steamgriddb_cache()
    entry = cache.get(profile_id, {})
    cache_matches_request = (
        (override_id is not None and entry.get("game_id") == override_id)
        or (override_id is None and entry.get("resolved_name") == profile_name)
    )
    if not force_refresh and cache_matches_request and entry.get("cached_file"):
        cached_path = Path(entry["cached_file"])
        if cached_path.exists():
            return cached_path

    try:
        game_id = _resolve_game_id(api_key, profile_name, override_id)
        if game_id is None:
            return None

        best = pick_best(get_grids(api_key, game_id))
        if best is None:
            return None

        ARTWORK_CACHE_DIR.mkdir(exist_ok=True)
        cached_path = ARTWORK_CACHE_DIR / f"{best['id']}.png"
        if force_refresh or not cached_path.exists():
            # The image itself lives on SGDB's CDN, not the API host, and
            # doesn't need the Bearer token (it's a public asset) - but still
            # gets the same User-Agent, since a CDN behind the same kind of
            # bot protection could block a bare urllib request just as the
            # API host does (see the USER_AGENT comment above).
            image_request = urllib.request.Request(best["url"], headers={"User-Agent": STEAMGRIDDB_USER_AGENT})
            with urllib.request.urlopen(image_request, timeout=15) as response:
                cached_path.write_bytes(response.read())

        cache[profile_id] = {
            "game_id": game_id,
            "resolved_name": profile_name,
            "grid_id": best["id"],
            "cached_file": str(cached_path),
        }
        _save_steamgriddb_cache(cache)
        return cached_path
    except Exception as e:
        if log_fn is not None:
            log_fn(f"Cover art fetch failed for '{profile_name}': {e}")
        return None


if __name__ == "__main__":
    print(f"{__file__} is the {PROJECT_SHORT_NAME} engine + cover-art library - it's not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")

# Happy Vibes
# KARMA
