"""Global engine settings (configs/haptics_config.json): connection, panic
key, smoothing, auto-reconnect, timing.

Every setting has a matching module-level constant derived below
(MASTER_RANDOM_ENABLED, ENABLE_SMOOTHING, PANIC_KEY, ...) that
HapticsController (see engine.py) reads fresh on every call/tick rather
than capturing once - apply_haptics_config() takes advantage of this to
make Settings-tab changes take effect immediately, without an app restart.

IMPORTANT for anyone reading these from another module: access them as
`haptics_config.NAME` (import this module, not the names out of it).
`from src.haptics_config import NAME` copies today's value into a local
binding at import time - a later apply_haptics_config() call reassigns
the name inside *this* module's namespace via `global`, which a
`from ... import NAME` elsewhere can never see. engine.py relies on the
dotted form specifically so a running HapticsController keeps observing
live Settings changes; tighc.py's facade re-exports these by name too, but
only as one-time snapshots (see its module docstring) since nothing reads
them from there more than once.
"""

import json

from src.paths import CONFIGS_DIR
from src.ranges import VibeRange

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


def apply_haptics_config(new_config: dict):
    """
    Persist `new_config` to configs/haptics_config.json and take effect
    immediately, without needing an app restart - HapticsController's
    methods (roll(), _smooth(), on_key_press()'s panic check,
    background_loop()'s auto-reconnect check, ...) all read the constants
    above as plain module globals fresh on every call/tick rather than
    capturing them once, so reassigning them here is enough for a running
    engine to pick the change up on its very next read. Called by gui.py's
    Settings tab save handler instead of writing the file directly.

    The one setting this can't make live on its own is `intiface_ws`: a
    connection that's already open doesn't get torn down and reopened just
    because the URL changed. gui.py handles that separately by also
    updating its HapticsController's own `ws_url` attribute, so the new
    URL is at least used the next time something (re)connects, rather than
    silently continuing to point at the old one until an app restart.
    """
    global HAPTICS_CONFIG, INTIFACE_WS, MASTER_RANDOM_ENABLED, MASTER_VIBE_RANGE
    global ENABLE_SMOOTHING, SMOOTHING_FACTOR
    global ENABLE_PANIC_KEY, PANIC_KEY, PANIC_HOLD_DURATION
    global ENABLE_AUTO_RECONNECT, RECONNECT_COOLDOWN, FAILURE_RECONNECT_THRESHOLD
    global BACKGROUND_TICK

    # Constructing VibeRange validates the master range the same way
    # startup does - an invalid range raises before anything is written or
    # reassigned, rather than corrupting the on-disk config or leaving the
    # in-memory globals in a partially-updated state.
    master_vibe_range = VibeRange(*new_config["master"]["range"])

    HAPTICS_CONFIG_PATH.write_text(json.dumps(new_config, indent=2), encoding="utf-8")
    HAPTICS_CONFIG = new_config

    INTIFACE_WS = new_config["intiface_ws"]
    MASTER_RANDOM_ENABLED = new_config["master"]["enabled"]
    MASTER_VIBE_RANGE = master_vibe_range
    ENABLE_SMOOTHING = new_config["smoothing"]["enabled"]
    SMOOTHING_FACTOR = new_config["smoothing"]["factor"]
    ENABLE_PANIC_KEY = new_config["panic_key"]["enabled"]
    PANIC_KEY = new_config["panic_key"]["key"].strip().lower()
    PANIC_HOLD_DURATION = new_config["panic_key"]["hold_duration"]
    ENABLE_AUTO_RECONNECT = new_config["auto_reconnect"]["enabled"]
    RECONNECT_COOLDOWN = new_config["auto_reconnect"]["cooldown"]
    FAILURE_RECONNECT_THRESHOLD = new_config["auto_reconnect"]["failure_threshold"]
    BACKGROUND_TICK = new_config["timing"]["background_tick"]


if __name__ == "__main__":
    print(f"{__file__} is TIGHC's global-settings module - it's a library, not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")
