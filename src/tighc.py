"""The Intiface Game Haptics Controller (TIGHC) - engine + cover-art library facade.

This module is a re-export surface, not where the implementation lives.
cli.py and gui.py are both built against `from src import tighc` /
`from src.tighc import NAME`; the actual code lives in focused modules,
re-exported here so both entry points have one place to import everything
from:

  src/paths.py           - filesystem layout (configs/, profiles/, artwork_cache/)
  src/metadata.py        - project name/repo URL
  src/version.py         - version number + get_version()/get_version_tuple()
  src/ranges.py          - VibeRange/DurationRange/PulseSpec
  src/haptics.py         - configs/haptics.json load/apply + derived settings
  src/devices.py         - configs/devices.json registry + DeviceChannel
  src/profiles.py        - profiles/<id>/profile.json loading + Profile
  src/input.py           - keyboard/mouse normalization, focused-window lookup
  src/engine.py          - HapticsController, the engine itself
  src/steamgriddb.py     - SteamGridDB cover-art fetching/caching

This module isn't meant to be run directly - it's a library, imported by
cli.py (headless) and gui.py (interactive).

Caveat for anyone adding new code: the settings re-exported below that
apply_haptics_config() can change at runtime (INTIFACE_WS,
MASTER_RANDOM_ENABLED, ENABLE_SMOOTHING, PANIC_KEY, ...) are snapshots
taken once, at import time - fine for every current caller (none of them
re-read these particular names from `tighc` after Settings are saved; the
engine itself reads them from src.haptics directly, which is what
actually makes live-reload work - see that module's docstring), but new
code that needs the *live* value should do the same rather than relying on
this facade.
"""

from src.metadata import AUTHOR_NAME, AUTHOR_URL, PROJECT_NAME, PROJECT_SHORT_NAME, REPO_URL, WEBSITE_URL
from src.version import __version__, get_version, get_version_tuple
from src.paths import ARTWORK_CACHE_DIR, BUNDLED_PROFILES_DIR, CONFIGS_DIR, PROFILES_DIR, REPO_ROOT, USER_DATA_DIR
from src.ranges import DurationRange, FloatRange, VibeRange
from src.haptics import (
    BACKGROUND_TICK,
    DEFAULT_HAPTICS_CONFIG,
    ENABLE_AUTO_RECONNECT,
    ENABLE_PANIC_KEY,
    ENABLE_SMOOTHING,
    FAILURE_RECONNECT_THRESHOLD,
    HAPTICS_CONFIG,
    HAPTICS_CONFIG_PATH,
    INTIFACE_WS,
    MASTER_RANDOM_ENABLED,
    MASTER_VIBE_RANGE,
    PANIC_HOLD_DURATION,
    PANIC_KEY,
    RECONNECT_COOLDOWN,
    SMOOTHING_FACTOR,
    apply_haptics_config,
    load_haptics_config,
)
from src.devices import (
    DEVICES_PATH,
    SUPPORTED_OUTPUT_TYPES,
    DeviceChannel,
    _slugify,
    load_device_registry,
    resolve_channel_nicknames,
    save_device_registry,
)
from src.profiles import (
    DEFAULT_MINECRAFT_PROFILE,
    PROFILES,
    Binding,
    Profile,
    _load_profile,
    _parse_devices_field,
    has_bundled_version,
    load_profiles,
    restore_profile_from_bundled,
    seed_user_profiles,
)
from src.input import InputState, get_foreground_window_title, normalize_key
from src.engine import HapticsController
from src.steamgriddb import (
    DEFAULT_STEAMGRIDDB_CONFIG,
    STEAMGRIDDB_API_BASE,
    STEAMGRIDDB_CACHE_PATH,
    STEAMGRIDDB_CONFIG_PATH,
    STEAMGRIDDB_USER_AGENT,
    _load_steamgriddb_cache,
    _resolve_game_id,
    _save_steamgriddb_cache,
    _steamgriddb_api_get,
    download_image_bytes,
    get_grids,
    get_profile_artwork,
    load_steamgriddb_config,
    pick_best,
    save_steamgriddb_config,
    search_game,
)

if __name__ == "__main__":
    print(f"{__file__} is the {PROJECT_SHORT_NAME} engine + cover-art library - it's not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")

# Happy Vibes
# KARMA
