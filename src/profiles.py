"""Game profiles (profiles/<id>/profile.json): what each game's bindings are
and how to match its window title.

Each profile is a folder under PROFILES_DIR/<id>/ containing a single
profile.json with window matching metadata and a bindings array where each
entry contains its own keys, devices, and vibe range inline.

User profiles live in the platform-standard app data directory (see
src/paths.py - PROFILES_DIR). On first launch (empty user profiles dir),
seed_user_profiles() downloads all profiles from the TIGHC-Profiles GitHub
repo. Existing user profiles are never overwritten by seeding - only profile
ids not yet present in the user dir are fetched.

Profiles are matched against the foreground window title so haptics
automatically follow whatever game currently has focus, and go idle when
nothing matches.
"""

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.paths import PROFILES_DIR
from src.ranges import VibeRange

TIGHC_PROFILES_REPO = "TIGHC/Profiles"
TIGHC_PROFILES_RAW_BASE = f"https://raw.githubusercontent.com/{TIGHC_PROFILES_REPO}/main"
TIGHC_PROFILES_API_BASE = f"https://api.github.com/repos/{TIGHC_PROFILES_REPO}/contents"
TIGHC_PROFILES_URL = f"https://github.com/{TIGHC_PROFILES_REPO}"


@dataclass(frozen=True)
class Binding:
    """A keybinding: pressing any of its keys fires the vibration; holding sustains it; releasing stops it."""

    id: str
    vibe: VibeRange
    devices: Optional[frozenset]  # None means "all channels"


@dataclass(frozen=True)
class Profile:
    """A loaded game profile: what it's called, which window(s) it matches, and its bindings."""

    id: str
    name: str
    window_titles: list  # strings matched case-sensitively against the foreground window title
    bindings_by_key: dict  # key/button token -> Binding, all enabled bindings merged for event dispatch
    bindings: list  # raw parsed bindings, in file order, for the startup banner
    priority: list  # raw id order from profile.json, used by the GUI's priority field
    # When True, window_titles entries must equal the full window title exactly
    # (case-sensitive). When False (default), each entry is a substring - the
    # title just needs to contain it. Use exact=True when two games share a
    # common prefix, e.g. "Grounded" would otherwise also match "Grounded 2".
    window_title_exact: bool = False
    # Optional, purely cosmetic: pins this profile to an exact SteamGridDB
    # game id for cover-art lookup (see steamgriddb.get_profile_artwork()),
    # bypassing the by-name search that could otherwise match the wrong game
    # (e.g. a sequel or spin-off with a similar title). None means "search
    # by name".
    steamgriddb_id: Optional[int] = None
    # Optional: pins this profile to one exact grid (cover-art image) id
    # among that game's available options, instead of the default top-voted
    # one get_profile_artwork() would otherwise pick via pick_best(). None
    # means "use the default". Independent of steamgriddb_id above - you can
    # override the image without overriding the game, or vice versa.
    steamgriddb_grid_id: Optional[int] = None

    def matches(self, window_title: str) -> bool:
        """True if this profile's window_titles matches the window title (case-sensitive).

        Uses exact equality when window_title_exact is True, substring search otherwise.
        """
        if self.window_title_exact:
            return window_title in self.window_titles
        return any(t in window_title for t in self.window_titles)


def _github_get(url: str, timeout: int = 8) -> Optional[bytes]:
    """GET a URL with a User-Agent header. Returns raw bytes or None on any error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TIGHC"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def fetch_profile_ids_from_github() -> list:
    """
    Return the list of profile folder names available in the TIGHC-Profiles
    GitHub repo (i.e. every directory at the repo root that isn't 'assets').
    Returns an empty list if the request fails (offline, rate-limited, etc.).
    """
    data = _github_get(TIGHC_PROFILES_API_BASE)
    if data is None:
        return []
    try:
        items = json.loads(data.decode())
        return [
            item["name"] for item in items
            if item.get("type") == "dir" and item["name"] != "assets"
        ]
    except Exception:
        return []


def fetch_profile_from_github(profile_id: str) -> Optional[dict]:
    """
    Fetch and parse a single profile.json from the TIGHC-Profiles GitHub repo.
    Returns the parsed dict or None if the request fails or the JSON is invalid.
    """
    data = _github_get(f"{TIGHC_PROFILES_RAW_BASE}/{profile_id}/profile.json")
    if data is None:
        return None
    try:
        return json.loads(data.decode())
    except Exception:
        return None


def download_missing_profiles(log=print) -> int:
    """
    Fetch any profile from the TIGHC-Profiles GitHub repo that isn't already
    in the user's profiles dir. Returns the number of newly downloaded profiles.
    Profiles the user already has (even if unmodified) are left untouched.
    """
    existing = {d.name for d in PROFILES_DIR.iterdir() if d.is_dir()} if PROFILES_DIR.exists() else set()
    profile_ids = fetch_profile_ids_from_github()
    if not profile_ids:
        log("Could not reach TIGHC-Profiles on GitHub (offline?).")
        return 0

    new_ids = [pid for pid in profile_ids if pid not in existing]
    count = 0
    for profile_id in new_ids:
        data = fetch_profile_from_github(profile_id)
        if data is None:
            log(f"Failed to download profile '{profile_id}'.")
            continue
        dest = PROFILES_DIR / profile_id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "profile.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        log(f"Downloaded profile '{profile_id}'.")
        count += 1
    return count


def restore_profile_from_github(profile_id: str) -> bool:
    """
    Overwrite the user's copy of profile_id with the version from the
    TIGHC-Profiles GitHub repo. Returns True on success, False if the
    profile doesn't exist in the repo or the download fails.
    """
    data = fetch_profile_from_github(profile_id)
    if data is None:
        return False
    dest = PROFILES_DIR / profile_id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "profile.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return True


# Keep old name as an alias so any code referencing it still works.
restore_profile_from_bundled = restore_profile_from_github


def has_bundled_version(profile_id: str) -> bool:
    """True if this profile exists in the TIGHC-Profiles GitHub repo."""
    data = _github_get(
        f"{TIGHC_PROFILES_RAW_BASE}/{profile_id}/profile.json",
        timeout=4,
    )
    return data is not None


def seed_user_profiles():
    """
    Called on first launch (empty user profiles dir) to populate PROFILES_DIR
    from the TIGHC-Profiles GitHub repo. If the download fails (offline, etc.),
    falls back to the built-in Minecraft profile so the app always starts with
    at least one profile. Does nothing if the user already has profiles.
    """
    existing = [d for d in PROFILES_DIR.iterdir() if d.is_dir()] if PROFILES_DIR.exists() else []
    if existing:
        return  # user already has profiles - never overwrite on startup

    print("First launch: downloading profiles from GitHub...")
    count = download_missing_profiles()
    if count == 0:
        print("Download failed - creating built-in Minecraft profile as fallback.")
        _seed_builtin_minecraft()
    else:
        print(f"Downloaded {count} profile(s).")


_BUILTIN_MINECRAFT = {
    "name": "Minecraft",
    "window_titles": ["minecraft"],
    "priority": ["attack", "use", "sneak", "sprint", "movement"],
    "bindings": [
        {"id": "movement",    "keys": ["w", "a", "s", "d"],              "enabled": True, "devices": ["all"], "vibe": [0.40, 0.65]},
        {"id": "sprint",      "keys": ["ctrl"],                          "enabled": True, "devices": ["all"], "vibe": [0.50, 0.70]},
        {"id": "sneak",       "keys": ["shift"],                         "enabled": True, "devices": ["all"], "vibe": [0.30, 0.50]},
        {"id": "attack",      "keys": ["mouse_left"],                    "enabled": True, "devices": ["all"], "vibe": [0.75, 1.00]},
        {"id": "use",         "keys": ["mouse_right"],                   "enabled": True, "devices": ["all"], "vibe": [0.55, 0.80]},
        {"id": "jump",        "keys": ["space"],                         "enabled": True, "devices": ["all"], "vibe": [0.70, 0.95]},
        {"id": "pick_block",  "keys": ["mouse_middle"],                  "enabled": True, "devices": ["all"], "vibe": [0.30, 0.45]},
        {"id": "drop",        "keys": ["q"],                             "enabled": True, "devices": ["all"], "vibe": [0.25, 0.40]},
        {"id": "offhand",     "keys": ["f"],                             "enabled": True, "devices": ["all"], "vibe": [0.25, 0.40]},
        {"id": "inventory",   "keys": ["e"],                             "enabled": True, "devices": ["all"], "vibe": [0.15, 0.25]},
        {"id": "switch_item", "keys": ["1","2","3","4","5","6","7","8","9","scroll"], "enabled": True, "devices": ["all"], "vibe": [0.15, 0.30]},
    ],
}

DEFAULT_MINECRAFT_PROFILE = _BUILTIN_MINECRAFT


def _seed_builtin_minecraft():
    profile_dir = PROFILES_DIR / "minecraft"
    profile_dir.mkdir(parents=True, exist_ok=True)
    dest = profile_dir / "profile.json"
    if not dest.exists():
        dest.write_text(json.dumps(_BUILTIN_MINECRAFT, indent=2), encoding="utf-8")


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
    Load and validate one profile folder (profile.json) into a Profile.
    Raises ValueError with a human-readable message for any structural
    problem - callers are expected to let this propagate rather than silently
    skip a broken profile, so mistakes surface immediately.
    """
    profile_path = profile_dir / "profile.json"
    if not profile_path.exists():
        raise ValueError("folder must contain profile.json")

    data = json.loads(profile_path.read_text(encoding="utf-8"))

    name = data.get("name", profile_dir.name)
    window_titles = [t for t in data.get("window_titles", [])]
    window_title_exact = bool(data.get("window_title_exact", False))
    if not window_titles:
        raise ValueError("profile.json needs at least one entry in window_titles")

    priority = data.get("priority", [])

    seen_ids = set()
    parsed_bindings = []  # every binding, in file order, for the banner/GUI display
    bindings_by_key = {}  # key/button token -> Binding, all enabled bindings for event-driven dispatch

    for binding in data.get("bindings", []):
        bid = binding["id"]
        if bid in seen_ids:
            raise ValueError(f"duplicate binding id '{bid}'")
        seen_ids.add(bid)

        keys = [k.lower() for k in binding.get("keys", [])]
        if not keys:
            raise ValueError(f"binding '{bid}' has no keys")

        if "vibe" not in binding:
            raise ValueError(f"binding '{bid}' has no 'vibe' field")
        vibe = VibeRange(*binding["vibe"])

        enabled = binding.get("enabled", True)
        target_devices = _parse_devices_field(binding)

        parsed_bindings.append(
            {
                "id": bid,
                "keys": keys,
                "mode": None,
                "enabled": enabled,
                "vibe": vibe,
                "duration": None,
                "devices": target_devices,
            }
        )

        if not enabled:
            continue
        b = Binding(id=bid, vibe=vibe, devices=target_devices)
        for k in keys:
            bindings_by_key[k] = b

    return Profile(
        id=profile_dir.name,
        name=name,
        window_titles=window_titles,
        window_title_exact=window_title_exact,
        bindings_by_key=bindings_by_key,
        bindings=parsed_bindings,
        priority=priority,
        steamgriddb_id=data.get("steamgriddb_id"),
        steamgriddb_grid_id=data.get("steamgriddb_grid_id"),
    )


def load_profiles() -> dict:
    """
    Seed from GitHub on first launch (empty user profiles dir), then discover
    and load every profile folder under PROFILES_DIR into a dict keyed by
    profile id (the folder name), in alphabetical order.

    Any folder that fails to load raises RuntimeError immediately (wrapping
    the underlying ValueError/OSError/KeyError with the offending folder's
    name) rather than being silently skipped - a typo in one profile shouldn't
    produce a program that silently starts with fewer profiles than the user
    configured.
    """
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    seed_user_profiles()

    profiles = {}
    for entry in sorted(PROFILES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / "profile.json").exists():
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


if __name__ == "__main__":
    print(f"{__file__} is TIGHC's game-profile module - it's a library, not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")
