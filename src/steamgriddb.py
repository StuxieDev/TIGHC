"""Cover-art fetching/caching from SteamGridDB, per game profile.

Talks to https://www.steamgriddb.com/api/v2 (confirmed against their real
OpenAPI spec at https://www.steamgriddb.com/static/openapi.yml - their docs
*page* blocks automated fetches, but the spec file itself doesn't) using a
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

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from src.paths import ARTWORK_CACHE_DIR, CONFIGS_DIR

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


def download_image_bytes(url: str) -> bytes:
    """
    Fetch raw image bytes from a SteamGridDB CDN URL (a grid's full image or
    its `thumb` preview) - same User-Agent requirement as the API itself
    (see STEAMGRIDDB_USER_AGENT above), no Bearer token needed since these
    are public assets. Shared by get_profile_artwork() (caching the chosen
    full-size grid) and gui.py's cover-image picker (previewing every
    candidate before one is chosen).
    """
    request = urllib.request.Request(url, headers={"User-Agent": STEAMGRIDDB_USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read()


def get_profile_artwork(
    profile_id: str,
    profile_name: str,
    override_id: Optional[int] = None,
    override_grid_id: Optional[int] = None,
    force_refresh: bool = False,
    log_fn=None,
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

    `override_grid_id`, if given, pins the exact grid (image) to use among
    the game's available options instead of the default top-voted one
    pick_best() would otherwise choose - see gui.py's cover-image picker. If
    that id is no longer among the game's grids (e.g. deleted upstream since
    it was chosen), this falls back to pick_best() and logs why via
    `log_fn`, rather than failing outright over one stale reference.

    The on-disk cache entry is only trusted if it still matches what's being
    asked for now (the same override_id/override_grid_id, or - with no
    override - the same profile_name); renaming a profile or changing
    either override invalidates the old entry and triggers a fresh lookup.
    """
    config = load_steamgriddb_config()
    if not config.get("enabled") or not config.get("api_key"):
        return None
    api_key = config["api_key"]

    cache = _load_steamgriddb_cache()
    entry = cache.get(profile_id, {})
    # Compares the *requested* grid_override_id against last time, not the
    # grid_id the fetch actually resolved to - comparing against the
    # resolved id can't tell "explicitly re-pinned the same image the
    # default already was" apart from "asked for the default", so switching
    # from an override back to no-override wouldn't be detected as a cache
    # miss and would just keep serving the old override's image forever.
    cache_matches_request = (
        (override_id is not None and entry.get("game_id") == override_id)
        or (override_id is None and entry.get("resolved_name") == profile_name)
    ) and entry.get("grid_override_id") == override_grid_id
    if not force_refresh and cache_matches_request and entry.get("cached_file"):
        cached_path = Path(entry["cached_file"])
        if cached_path.exists():
            return cached_path

    try:
        game_id = _resolve_game_id(api_key, profile_name, override_id)
        if game_id is None:
            return None

        grids = get_grids(api_key, game_id)
        best = None
        if override_grid_id is not None:
            best = next((g for g in grids if g["id"] == override_grid_id), None)
            if best is None and log_fn is not None:
                log_fn(
                    f"Chosen cover image {override_grid_id} for '{profile_name}' is no longer available - "
                    "falling back to the default."
                )
        if best is None:
            best = pick_best(grids)
        if best is None:
            return None

        ARTWORK_CACHE_DIR.mkdir(exist_ok=True)
        cached_path = ARTWORK_CACHE_DIR / f"{best['id']}.png"
        if force_refresh or not cached_path.exists():
            cached_path.write_bytes(download_image_bytes(best["url"]))

        cache[profile_id] = {
            "game_id": game_id,
            "resolved_name": profile_name,
            "grid_id": best["id"],
            "grid_override_id": override_grid_id,
            "cached_file": str(cached_path),
        }
        _save_steamgriddb_cache(cache)
        return cached_path
    except Exception as e:
        if log_fn is not None:
            log_fn(f"Cover art fetch failed for '{profile_name}': {e}")
        return None


if __name__ == "__main__":
    print(f"{__file__} is TIGHC's SteamGridDB cover-art module - it's a library, not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")
