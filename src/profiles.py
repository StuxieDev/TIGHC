"""Game profiles (profiles/<id>/{keybinds,ranges}.json): what each game's
keybinds/ranges are, and loading/validating them.

Each profile is a folder under profiles/<id>/ with two files:
  keybinds.json - which keys/buttons do what, their mode (continuous/pulse),
                  and which device nickname(s) (or "all") they drive
  ranges.json   - the vibe/duration bands for each binding, by id

Profiles are matched against the foreground window title (see
HapticsController._match_profile in engine.py) so haptics automatically
follow whatever game currently has focus, and go idle when nothing matches.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.paths import PROFILES_DIR
from src.ranges import DurationRange, PulseSpec, VibeRange

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
        steamgriddb_grid_id=keybinds.get("steamgriddb_grid_id"),
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


if __name__ == "__main__":
    print(f"{__file__} is TIGHC's game-profile module - it's a library, not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")
