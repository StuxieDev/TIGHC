"""Raw keyboard/mouse input handling: normalizing pynput's key objects into
the tokens profile JSON uses, tracking which are currently held, and
finding the focused window's title (for automatic profile switching).
"""

import ctypes
import sys
from dataclasses import dataclass, field


def normalize_key(key) -> str:
    """
    Convert a pynput key object into the plain lowercase token used
    throughout this module and in profile JSON (e.g. "w", "space", "shift").

    pynput reports letter/number keys as "'a'" and special keys as
    "Key.space" / "Key.shift_l" / "Key.ctrl_r" - strip quotes, drop the
    "key." prefix, and drop left/right suffixes so "Key.shift_l" and
    "Key.shift_r" both normalize to the same "shift" used in profile configs.
    """
    # pynput's str() on a letter/number key gives "'a'" (quoted); on a
    # special key it gives "Key.space" etc. - lowercasing first means the
    # "key." prefix check below doesn't need to worry about case.
    raw = str(key).strip("'\"").lower()
    if raw.startswith("key."):
        raw = raw[len("key."):]
    # "Key.shift_l"/"Key.shift_r" (left/right variants) both become "shift" -
    # profile configs only ever specify the side-agnostic name.
    for suffix in ("_l", "_r"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


# Cached across calls - get_foreground_window_title() runs every
# BACKGROUND_TICK (~180ms by default), and opening a fresh X11 connection
# on every single tick would be wasteful latency for no reason. Dropped and
# recreated on the next call if it ever errors (e.g. the X server restarts,
# or a session switch happens while the app is running).
_x11_display = None


def _get_x11_display():
    """Lazily create (and cache) the X11 Display connection used for Linux foreground-window lookups - see _get_foreground_window_title_x11()."""
    global _x11_display
    if _x11_display is None:
        from Xlib import display  # optional Linux-only dependency - see requirements.txt's environment marker

        _x11_display = display.Display()
    return _x11_display


def _get_foreground_window_title_x11() -> str:
    """
    Linux/X11 implementation of get_foreground_window_title(). Requires the
    optional python-xlib dependency and an actual X11 session - on Steam
    Deck, Desktop Mode defaults to Wayland as of SteamOS 3.8, which doesn't
    expose this at all (by design - Wayland's security model doesn't let
    one app query which window another has focused), so switch to X11 first
    with `steamos-session-select plasma-x11-persistent`. Missing dependency,
    no X11 session, or any other failure all just return "" - same as
    Windows' version, and same meaning: no profile matches, haptics idle.
    """
    try:
        d = _get_x11_display()
        # _NET_ACTIVE_WINDOW is an Extended Window Manager Hints (EWMH)
        # property the window manager keeps updated on the root window,
        # holding the X11 resource id of whichever window currently has
        # focus - intern_atom() looks up (or registers) the integer id
        # X11 uses internally for that property name.
        net_active_window = d.intern_atom("_NET_ACTIVE_WINDOW")
        window_id_prop = d.screen().root.get_full_property(net_active_window, 0)
        if not window_id_prop or not window_id_prop.value:
            return ""
        # The property's value is a one-element array holding the focused
        # window's id; 0 means "nothing is focused" (e.g. focus is on the
        # desktop/root window itself, not any application).
        window_id = window_id_prop.value[0]
        if window_id == 0:
            return ""
        # Wrap the raw id in a proper Xlib window object so its own
        # properties (the title, below) can be queried on it directly.
        window = d.create_resource_object("window", window_id)

        # _NET_WM_NAME is the modern EWMH title property, stored as UTF-8 -
        # preferred over the legacy WM_NAME fallback below when a window
        # bothers to set it (most current desktop apps do).
        net_wm_name = d.intern_atom("_NET_WM_NAME")
        utf8_string = d.intern_atom("UTF8_STRING")
        name_prop = window.get_full_property(net_wm_name, utf8_string)
        if name_prop and name_prop.value:
            return name_prop.value.decode("utf-8", errors="replace")

        # Fall back to the older, non-EWMH WM_NAME property - some windows
        # (typically older X11 apps, and games running under some Proton/
        # compatibility layers) don't set _NET_WM_NAME at all.
        wm_name = window.get_wm_name()
        return wm_name if isinstance(wm_name, str) else ""
    except Exception:
        global _x11_display
        _x11_display = None  # drop a possibly-broken connection so the next call opens a fresh one instead of repeating the same failure forever
        return ""


def get_foreground_window_title() -> str:
    """
    Returns the currently-focused window's title, used to match against a
    profile's window_titles - Windows via Win32 APIs, Linux via X11 (see
    _get_foreground_window_title_x11 for the Steam Deck / Wayland caveat).
    Returns "" (so no profile matches, haptics stay idle) on any other
    platform or on error - never raises.
    """
    if sys.platform == "win32":
        try:
            # GetForegroundWindow() returns an HWND (an opaque handle, not
            # the title itself) for whichever window currently has focus.
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            # Win32's GetWindowText doesn't allocate a buffer for you, so
            # the title's length has to be queried first to size one
            # correctly - +1 below leaves room for the null terminator.
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            # Copies the window's title text into `buffer` in place.
            ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
            return buffer.value
        except Exception:
            return ""
    if sys.platform.startswith("linux"):
        return _get_foreground_window_title_x11()
    return ""


@dataclass
class InputState:
    """Tracks which keys/buttons are currently held down between events."""

    pressed_keys: set = field(default_factory=set)

    def set_held(self, token: str, held: bool):
        """Add or remove a token from pressed_keys - used for mouse buttons, which have no press/release keystrokes."""
        if held:
            self.pressed_keys.add(token)
        else:
            self.pressed_keys.discard(token)


if __name__ == "__main__":
    print(f"{__file__} is TIGHC's input-handling module - it's a library, not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")
