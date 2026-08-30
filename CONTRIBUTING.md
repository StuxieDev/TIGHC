<p align="center">
  <img src="assets/logo.png" width="500" alt="The Intiface Game Haptics Controller (TIGHC)">
</p>

# Contributing to TIGHC

Issues and pull requests are welcome at
[github.com/TIGHC/Engine](https://github.com/TIGHC/Engine).

## Getting set up

```
git clone https://github.com/TIGHC/Engine.git
pip install -r requirements.txt
python gui.py    # or: python cli.py
```

See the [README](README.md) for how the engine is organized (`src/`) and
what each module does.

## Making a change

There's no automated test suite - verify changes manually:

- The GUI's **Test** tab lets you simulate keybinds and drive channels
  directly without needing the real game or a connected toy.
- If you touched profile loading/parsing, confirm a profile still loads
  cleanly from `%APPDATA%\TIGHC\profiles\` (or `~/.local/share/TIGHC/profiles/`
  on Linux) - a structurally invalid profile should fail fast with a clear
  error, not crash mid-session.
- If you touched Linux-specific code (`src/input.py`'s X11 path), test on
  an actual X11 session where possible - see [LINUX_GUIDE.md](LINUX_GUIDE.md).

## Versioning

Every user-facing change should bump [`VERSION.md`](VERSION.md) and add a
matching entry to [`CHANGELOG.md`](CHANGELOG.md) in the same PR, following
[Semantic Versioning](https://semver.org/): MAJOR for breaking config-format/
behavior changes, MINOR for backward-compatible feature additions, PATCH for
fixes. Small non-user-facing changes (typo fixes, comments) don't need a bump.

## Adding a game profile

Profiles live in a separate repo: open a pull request on
[TIGHC/Profiles](https://github.com/TIGHC/Profiles) instead, or use the
GUI's Profiles tab -> "New profile...".

## Reporting a bug

Open an issue with your OS, what you expected vs. what happened, and (if
relevant) which profile/game and what's in the Run tab's log at the time.
