"""The Intiface Game Haptics Controller (TIGHC) - engine module.

Drives Buttplug/Intiface haptic devices from any game's key/mouse input.

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
haptics_config.json, next to this script. All of the above are created with
sensible defaults on first run - edit them and restart to customize, or use
gui.py for an interactive configurator. No need to touch this source.
"""

import asyncio
import ctypes
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from buttplug import ButtplugClient, DeviceOutputCommand, OutputType
from pynput import keyboard, mouse

# =============================================== PROJECT METADATA ===========================================
# Single source of truth for identity/version - both haptics.py and gui.py
# (its About tab) read these rather than hardcoding them in two places.
# Versioning follows Semantic Versioning (semver.org): MAJOR.MINOR.PATCH,
# where MAJOR bumps mark breaking config-format/behavior changes, MINOR
# marks backward-compatible feature additions, and PATCH marks fixes.
# Bump __version__ here and add a matching entry to CHANGELOG.md together.
PROJECT_NAME = "The Intiface Game Haptics Controller"
PROJECT_SHORT_NAME = "TIGHC"
REPO_URL = "https://github.com/StuxieDev/TIGHC"
__version__ = "2.0.0"

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
        return random.uniform(self.low, self.high)


@dataclass(frozen=True)
class VibeRange(FloatRange):
    """A 0.0-1.0 intensity band. Each trigger rolls a random value inside it."""

    def __post_init__(self):
        super().__post_init__()
        if not (0.0 <= self.low and self.high <= 1.0):
            raise ValueError(f"Invalid intensity range ({self.low}, {self.high}); must be within 0.0-1.0")

    def __str__(self) -> str:
        return f"{self.low * 100:.0f}-{self.high * 100:.0f}%"


@dataclass(frozen=True)
class DurationRange(FloatRange):
    """A band of seconds. Each pulse rolls a random duration inside it, so pulses don't all feel identical."""

    def __str__(self) -> str:
        return f"{self.low:.2f}-{self.high:.2f}s"


@dataclass(frozen=True)
class PulseSpec:
    """A one-shot binding's intensity band plus how long that pulse should last."""

    vibe: VibeRange
    duration: DurationRange

    def roll_duration(self) -> float:
        return self.duration.roll()


# ========================================== GLOBAL CONFIG FILE I/O ==========================================
CONFIG_PATH = Path(__file__).with_name("haptics_config.json")

# Written to disk verbatim the first time the script runs. Every value here
# has a matching constant derived below, so this is the single source of
# truth for defaults - keep it in sync if you add a new setting. Per-game
# keybinds and ranges live in profiles/, not here - see load_profiles().
DEFAULT_CONFIG = {
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


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        try:
            CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
            print(f"Created default config at {CONFIG_PATH.name} - edit it and restart to customize.")
        except OSError as e:
            print(f"Could not write default config ({e}); using built-in defaults.")
        return DEFAULT_CONFIG

    try:
        user_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"Could not read {CONFIG_PATH.name} ({e}); using built-in defaults.")
        return DEFAULT_CONFIG

    return _deep_merge(DEFAULT_CONFIG, user_config)


CONFIG = load_config()

INTIFACE_WS = CONFIG["intiface_ws"]

MASTER_RANDOM_ENABLED = CONFIG["master"]["enabled"]
MASTER_VIBE_RANGE = VibeRange(*CONFIG["master"]["range"])

ENABLE_SMOOTHING = CONFIG["smoothing"]["enabled"]
SMOOTHING_FACTOR = CONFIG["smoothing"]["factor"]

ENABLE_PANIC_KEY = CONFIG["panic_key"]["enabled"]
PANIC_KEY = CONFIG["panic_key"]["key"].strip().lower()
PANIC_HOLD_DURATION = CONFIG["panic_key"]["hold_duration"]

ENABLE_AUTO_RECONNECT = CONFIG["auto_reconnect"]["enabled"]
RECONNECT_COOLDOWN = CONFIG["auto_reconnect"]["cooldown"]
FAILURE_RECONNECT_THRESHOLD = CONFIG["auto_reconnect"]["failure_threshold"]

BACKGROUND_TICK = CONFIG["timing"]["background_tick"]


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
DEVICES_PATH = Path(__file__).with_name("devices.json")


def _slugify(text: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "_" for c in text).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "device"


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
    if not DEVICES_PATH.exists():
        return []
    try:
        data = json.loads(DEVICES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("channels", [])


def save_device_registry(registry: list):
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
PROFILES_DIR = Path(__file__).with_name("profiles")

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

    def matches(self, window_title_lower: str) -> bool:
        return any(t in window_title_lower for t in self.window_titles)

    def range_for(self, channel_nickname: str, pressed_keys: set) -> VibeRange:
        # Walk continuous bindings in priority order; the first one that both
        # targets this channel and has a currently-pressed token wins.
        for binding in self.continuous:
            if binding.devices is not None and channel_nickname not in binding.devices:
                continue
            if pressed_keys & binding.tokens:
                return binding.vibe
        return self.background


def _seed_default_profile():
    profile_dir = PROFILES_DIR / "minecraft"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "keybinds.json").write_text(json.dumps(DEFAULT_MINECRAFT_KEYBINDS, indent=2), encoding="utf-8")
    (profile_dir / "ranges.json").write_text(json.dumps(DEFAULT_MINECRAFT_RANGES, indent=2), encoding="utf-8")
    print(f"Created default profile at {profile_dir} - copy this folder to add more games.")


def _parse_devices_field(binding: dict) -> Optional[frozenset]:
    devices_field = binding.get("devices", ["all"])
    if not isinstance(devices_field, list) or not devices_field:
        raise ValueError(f"binding '{binding.get('id')}' devices must be a non-empty list")
    lowered = [d.lower() for d in devices_field]
    if "all" in lowered:
        return None
    return frozenset(lowered)


def _load_profile(profile_dir: Path) -> Profile:
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
    parsed_bindings = []
    continuous_entries = {}  # id -> ContinuousBinding
    pulse_bindings = {}  # key token -> PulseBinding

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
            continue
        if mode == "continuous":
            continuous_entries[bid] = ContinuousBinding(
                tokens=frozenset(keys), vibe=vibe, id=bid, devices=target_devices
            )
        else:
            spec = PulseSpec(vibe, duration)
            pulse_binding = PulseBinding(spec=spec, devices=target_devices)
            for k in keys:
                pulse_bindings[k] = pulse_binding

    background_section = ranges.get("background")
    if background_section is None or "vibe" not in background_section:
        raise ValueError("ranges.json needs a 'background' entry with a 'vibe' range")
    background = VibeRange(*background_section["vibe"])

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
    )


def load_profiles() -> dict:
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


PROFILES = load_profiles()


def normalize_key(key) -> str:
    # pynput reports letter/number keys as "'a'" and special keys as
    # "Key.space" / "Key.shift_l" / "Key.ctrl_r" - strip quotes, drop the
    # "key." prefix, and drop left/right suffixes so "Key.shift_l" and
    # "Key.shift_r" both normalize to the same "shift" used in profile configs.
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
        """(Re)scan for devices on the existing connection and rebuild channels."""
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
        for profile in self.profiles.values():
            if profile.matches(window_title_lower):
                return profile
        return None

    def _update_active_profile(self):
        # A pinned test profile always wins over real window matching - see
        # test_profile_override above.
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
        if target is None:
            return list(self.channels.values())
        return [c for nickname, c in self.channels.items() if nickname in target]

    # -------------------------------------------------------------- output
    def roll(self, vibe_range: VibeRange) -> float:
        """Roll a level from the given range, unless master randomization overrides it."""
        active_range = MASTER_VIBE_RANGE if MASTER_RANDOM_ENABLED else vibe_range
        return active_range.roll()

    def _smooth(self, channel: DeviceChannel, target: float) -> float:
        # Exponential smoothing: closes part of the gap to the target each
        # tick instead of jumping straight there, so idle/continuous levels
        # feel like they drift rather than strobe every 180ms.
        if not ENABLE_SMOOTHING:
            return target
        return channel.last_level + (target - channel.last_level) * SMOOTHING_FACTOR

    async def _set_channel_level(self, channel: DeviceChannel, level: float):
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
        """Shared implementation behind pulse() and test_pulse() - see those for the difference."""
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
        # pynput listeners run on their own thread, not the asyncio loop -
        # this hands the coroutine back over to the loop thread safely.
        if self.running and self.loop:
            asyncio.run_coroutine_threadsafe(coro, self.loop)

    def on_key_press(self, key):
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
        try:
            k = normalize_key(key)
        except Exception:
            return
        self.input_state.pressed_keys.discard(k)

    def on_mouse_click(self, _x, _y, button, pressed):
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
        return "all" if devices is None else ",".join(sorted(devices))

    @classmethod
    def _binding_line(
        cls, label: str, enabled: bool, vibe_range: VibeRange, duration_range: Optional[DurationRange], devices: Optional[frozenset]
    ) -> str:
        if not enabled:
            return f"  - {label:<32} -> disabled"
        target = "" if devices is None else f" [{cls._devices_label(devices)}]"
        if duration_range is not None:
            return f"  - {label:<32} -> {vibe_range} pulse ({duration_range}){target}"
        return f"  - {label:<32} -> {vibe_range}{target}"

    @staticmethod
    def _status_line(label: str, value: str) -> str:
        return f"- {label:<24} -> {value}"

    def print_banner(self):
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
        self.log(f"Global config: {CONFIG_PATH}")
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


def _confirm_age() -> bool:
    """Self-attestation age gate for the headless CLI - the GUI has its own dialog equivalent (see gui.py)."""
    print(f"\n{PROJECT_NAME} ({PROJECT_SHORT_NAME}) v{__version__}")
    print("This software connects to and controls adult haptic/sex toy devices based on")
    print("your keyboard and mouse input while gaming. Intended for use only by adults")
    print("aged 18 or older.")
    try:
        answer = input("Type 'yes' to confirm you are 18 or older and continue: ").strip().lower()
    except EOFError:
        return False
    return answer == "yes"


async def main():
    if not _confirm_age():
        print("Age not confirmed - exiting.")
        return
    controller = HapticsController(INTIFACE_WS, PROFILES)
    await controller.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")

# Run with:
#   python haptics.py           (headless, hand-edited JSON configs)
#   python gui.py                (interactive: connect devices, configure, start/stop)
#
# Or if that doesn't work:
#   py haptics.py
#
# Or full path example (edit to match your Python install + where you saved the file, if you are on windows you may have saved to onedrive in the cloud, I also had that issue):
#   & "C:\Path\To\python.exe" "C:\Path\To\haptics.py"

# Happy Vibes
# KARMA
