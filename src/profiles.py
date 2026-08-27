"""Game profiles (profiles/<id>/profile.json): what each game's bindings are
and how to match its window title.

Each profile is a folder under profiles/<id>/ containing a single profile.json
with window matching metadata and a bindings array where each entry contains
its own keys, devices, and vibe range inline.

Profiles are matched against the foreground window title so haptics
automatically follow whatever game currently has focus, and go idle when
nothing matches.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.paths import PROFILES_DIR
from src.ranges import VibeRange

# Seeded to profiles/minecraft/ the first time the script runs (i.e. when
# profiles/ doesn't exist yet). Copy this folder to add another game.
DEFAULT_MINECRAFT_PROFILE = {
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


def _seed_default_profile():
    """Write profiles/minecraft/profile.json from DEFAULT_MINECRAFT_PROFILE."""
    # Only ever called when PROFILES_DIR doesn't exist yet (see
    # load_profiles()), so this always creates a brand-new folder - it's not
    # meant to "reset" an existing, possibly user-edited minecraft profile.
    profile_dir = PROFILES_DIR / "minecraft"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "profile.json").write_text(json.dumps(DEFAULT_MINECRAFT_PROFILE, indent=2), encoding="utf-8")
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
        # Folders without profile.json (e.g. profiles/assets/, holding the
        # submodule's own README images) aren't profiles at all - skip them.
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
