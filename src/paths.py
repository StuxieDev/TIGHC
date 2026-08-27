"""Filesystem layout for TIGHC's per-install runtime state.

Split out on its own so every other module (haptics, devices,
profiles, steamgriddb) can depend on these paths without pulling in
anything heavier - keeps the dependency graph a simple fan-out from here
rather than everything routing through one large module.

User data (configs, profiles, artwork cache) lives in the platform-standard
per-user app directory so it survives git updates and submodule updates:
  Windows: %APPDATA%\TIGHC\
  Linux:   ~/.local/share/TIGHC/

On first launch (empty user profiles dir), profiles.py fetches all profiles
from the TIGHC-Profiles GitHub repo and seeds them into the user profiles dir.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Platform-standard per-user app directory.
if os.name == "nt":
    _base = Path(os.environ.get("APPDATA", Path.home()))
else:
    _base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

USER_DATA_DIR = _base / "TIGHC"

# Per-install runtime config/state (haptics.json, devices.json, ...)
CONFIGS_DIR = USER_DATA_DIR / "configs"
CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

# User's working copy of game profiles - seeded from BUNDLED_PROFILES_DIR on
# first run, then fully owned by the user (GUI reads/writes here).
PROFILES_DIR = USER_DATA_DIR / "profiles"
PROFILES_DIR.mkdir(parents=True, exist_ok=True)

# Downloaded cover-art images.
ARTWORK_CACHE_DIR = USER_DATA_DIR / "artwork_cache"


if __name__ == "__main__":
    print(f"{__file__} is TIGHC's filesystem-layout module - it's a library, not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")
