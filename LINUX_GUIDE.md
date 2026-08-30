<p align="center">
  <img src="assets/logo.png" width="500" alt="The Intiface Game Haptics Controller (TIGHC)">
</p>

# Linux / Steam Deck Guide

TIGHC's primary target is Windows, but the engine also runs on Linux -
including Steam Deck's **Desktop Mode**. This guide walks through the
Linux-specific setup end to end. For everything else (profiles, the GUI,
global settings, cover art), the [main README](README.md) applies exactly
as written - this guide only covers what's different on Linux.

Not covered here: Steam Deck's **Game Mode**. Game Mode doesn't run
arbitrary Python GUIs, so TIGHC has to run from Desktop Mode - alongside
your game, not launched instead of it. If you're on a regular Linux desktop
(not a Steam Deck) the rest of this guide still applies; just skip the
Steam Deck-specific steps.

> **Looking ahead:** a pre-compiled executable (covering both Linux and
> Windows) is in the works, packaged so it can be added as a non-Steam game
> shortcut instead of needing a Python environment - which should make Game
> Mode support possible. It should work on any SteamOS device in principle,
> not just Steam Deck specifically, but this is a roadmap item, not a
> current capability - it hasn't been built, tested, or verified yet.

## Why an X11 session is required

Two things TIGHC depends on - detecting which window currently has focus
(so it knows which profile to switch to), and `pynput`'s global keyboard/
mouse capture (so it can react to input while a game has focus, not just
while TIGHC's own window does) - both need **X11**, not Wayland.

This isn't a bug to work around later: Wayland's security model
*deliberately* doesn't let one app see what window another app has focused,
or listen to another app's input. That's normally a good thing (it's why a
random Wayland app can't keylog everything you type), but it's exactly what
a "vibrate based on what you're doing in-game" tool needs to do. As of
SteamOS 3.8, Desktop Mode defaults to Wayland, so this needs switching
before anything else here will work.

## 1. Switch to the X11 session (Steam Deck)

From Desktop Mode, open a terminal (Konsole) and run:

```
steamos-session-select plasma-x11-persistent
```

This logs you out and back into an X11 Plasma session - "persistent" means
it stays your default until you change it again. Switch back to Wayland any
time the same way:

```
steamos-session-select plasma-wayland-persistent
```

There's no real downside to leaving X11 as your Desktop Mode default if
you mainly use Desktop Mode for TIGHC - Game Mode is unaffected either way,
since it always uses its own gamescope session regardless of which Desktop
Mode session you've picked.

On a regular Linux desktop (not Steam Deck), the equivalent is just:
picking an X11 session at your display manager's login screen instead of a
Wayland one (exact steps depend on your distro/desktop environment).

## 2. Get Python, pip, and the repo

Steam Deck's SteamOS ships Python 3, but confirm pip and Tkinter (needed
for `gui.py`) are both available:

```
python3 --version
python3 -m pip --version
python3 -c "import tkinter"
```

If `pip` is missing: `python3 -m ensurepip --upgrade`. If `tkinter` errors
with `ModuleNotFoundError`, your distro likely splits it into a separate
package (e.g. `python3-tk` on Debian/Ubuntu-based distros, `tk` on Arch);
SteamOS's own Python normally has it built in.

Clone the repo **with its submodule** (`profiles/` is a separate git
repository - see the main README's Quick start for why):

```
git clone --recurse-submodules https://github.com/TIGHC/Engine.git
cd TIGHC
```

Then install dependencies:

```
python3 -m pip install --user -r requirements.txt
```

`requirements.txt` pulls in `python-xlib` automatically on Linux (it's
marked Linux-only, so Windows installs never see it) - this is what
implements the X11 focused-window lookup. `--user` avoids touching
SteamOS's read-only system Python; a virtualenv (`python3 -m venv .venv`)
works too if you prefer one.

## 3. Install Intiface Central

[Intiface Central](https://intiface.com/central/) - the app that actually
talks to your Bluetooth toy over the Buttplug protocol - ships a Linux
AppImage (and a [Flathub](https://flathub.org/en/apps/com.nonpolynomial.intiface_central)
build). Download whichever you prefer, make the AppImage executable if
needed (`chmod +x Intiface_Central-*.AppImage`), and run it. Steam Deck's
built-in Bluetooth should work with it like any other Linux Bluetooth
adapter - pair your toy from Intiface Central's device scan the same way
you would on Windows.

## 4. Run TIGHC

Same commands as everywhere else:

```
python3 gui.py       # interactive configurator (needs the X11 session from step 1)
python3 cli.py        # headless, once profiles/config are already set up
```

Both still show the 18+ age-gate exactly as on Windows.

## Troubleshooting

- **Haptics never trigger, even mid-game.** Almost always means the
  foreground-window lookup is returning nothing - either you're still on
  the Wayland session (go back to step 1), or `python-xlib` didn't install
  (check `python3 -c "import Xlib"` doesn't error).
- **A specific game's profile never matches, but others do.** Some games
  (particularly ones running through certain Proton/compatibility layers)
  don't set the modern `_NET_WM_NAME` window property, only the older
  `WM_NAME` one. TIGHC already falls back to `WM_NAME` automatically: if a
  profile still won't match, open a terminal and check what title the
  window actually reports (e.g. via `xdotool getactivewindow getwindowname`
  if you have `xdotool` installed, or `xprop WM_NAME` and click the window)
  and compare it against that profile's `window_titles` in `keybinds.json`.
- **`gui.py` opens with no visible window, or the terminal just hangs.**
  Confirm you're actually in an X11 session (`echo $XDG_SESSION_TYPE`
  should print `x11`, not `wayland`) - a leftover Wayland session is the
  most common cause here too.
- **Bluetooth pairing issues in Intiface Central.** This is a Buttplug/
  Intiface-level concern, not a TIGHC one - check Intiface Central's own
  docs/Discord if your toy won't pair; TIGHC only talks to devices *through*
  Intiface Central's WebSocket connection, it never touches Bluetooth
  directly.
