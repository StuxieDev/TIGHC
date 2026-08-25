"""TIGHC's version - single source of truth, read by the engine's startup
banner, cli.py, and gui.py's About tab.

Versioning follows Semantic Versioning (semver.org): MAJOR.MINOR.PATCH,
where MAJOR bumps mark breaking config-format/behavior changes, MINOR marks
backward-compatible feature additions, and PATCH marks fixes. Bump
__version__ here and add a matching entry to CHANGELOG.md together.
"""

__version__ = "3.3.2"


def get_version() -> str:
    """Return the current version string, e.g. "3.2.0"."""
    return __version__


def get_version_tuple() -> tuple:
    """
    Parse __version__ into a (major, minor, patch) int tuple, for callers
    that need to compare versions programmatically rather than just
    display the string (e.g. "is this build new enough to have feature X").
    """
    major, minor, patch = __version__.split(".")
    return (int(major), int(minor), int(patch))


if __name__ == "__main__":
    print(f"{__file__} is TIGHC's version module - it's a library, not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")
