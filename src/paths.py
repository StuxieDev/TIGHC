"""Filesystem layout for TIGHC's per-install runtime state.

Split out on its own so every other module (haptics_config, devices,
profiles, steamgriddb) can depend on these paths without pulling in
anything heavier - keeps the dependency graph a simple fan-out from here
rather than everything routing through one large module.
"""

from pathlib import Path

# Every path below is resolved relative to the repo root, not to this
# file's own directory - this file lives at <repo root>/src/paths.py, so
# two parents up is the actual project root regardless of which specific
# src/ module ends up importing it.
REPO_ROOT = Path(__file__).resolve().parent.parent

# Per-install runtime config/state - gitignored, never source (see
# .gitignore) - created with sensible defaults the first time it's needed.
CONFIGS_DIR = REPO_ROOT / "configs"
CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

# profiles/ is its own git repository (TIGHC-Profiles) checked out as a
# submodule at the repo root - see the main README's Quick start for how
# it's cloned/updated.
PROFILES_DIR = REPO_ROOT / "profiles"

# Downloaded cover-art images - generated/cached, not really a "config", so
# it stays at the repo root alongside profiles/ rather than inside configs/.
ARTWORK_CACHE_DIR = REPO_ROOT / "artwork_cache"


if __name__ == "__main__":
    print(f"{__file__} is TIGHC's filesystem-layout module - it's a library, not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")
