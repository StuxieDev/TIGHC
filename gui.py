"""The Intiface Game Haptics Controller (TIGHC) - interactive GUI.

Tkinter configurator and launcher for src/tighc.py.

Connect to Intiface, scan for devices, assign friendly nicknames to
individual motors/capabilities, build game profiles (keybinds + ranges +
which device each keybind drives), tweak global settings, and start/stop
the haptics engine - all from one window. Everything you do here is written
to the same JSON files src/tighc.py reads (configs/haptics_config.json,
configs/devices.json, profiles/<id>/{keybinds,ranges}.json), so hand-editing
those files and using this GUI are fully interchangeable.

The buttplug/asyncio side of things runs on a dedicated background thread
(AsyncBridge) so the Tkinter main loop never blocks; button handlers submit
coroutines to it and marshal results back to the UI thread via `root.after`.
"""

import asyncio
import copy
import json
import queue
import shutil
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, scrolledtext, simpledialog, ttk

# sv_ttk ("Sun Valley") is a third-party ttk theme that reskins every stock
# ttk widget to look like modern Windows 11/Fluent UI (flat buttons, a
# deliberate color palette, working light/dark modes) - something the
# built-in ttk themes (vista/clam/etc.) can't approximate on their own.
# Install with: pip install sv_ttk
import sv_ttk

from src import tighc
from src.tighc import (
    PROFILES_DIR,
    PROJECT_NAME,
    PROJECT_SHORT_NAME,
    REPO_URL,
    DurationRange,
    HapticsController,
    VibeRange,
    __version__,
    load_haptics_config,
    load_device_registry,
    load_profiles,
    save_device_registry,
)

CHANGELOG_PATH = Path(__file__).with_name("CHANGELOG.md")
ARTWORK_THUMBNAIL_WIDTH = 140  # target width in pixels; see _load_thumbnail_image
GRID_PICKER_THUMBNAIL_WIDTH = 100  # smaller than ARTWORK_THUMBNAIL_WIDTH - many of these tile at once
# Caps how many of a game's cover images _on_choose_cover_image() downloads
# to preview - grids are already ranked by community score (most relevant
# first), so this keeps the picker responsive without downloading every
# option a popular game might have (sometimes 100+, each several hundred KB).
GRID_PICKER_LIMIT = 24

BINDING_COLUMNS = ("id", "keys", "mode", "devices", "vibe", "duration", "enabled")
BINDING_HEADERS = {
    "id": "ID", "keys": "Keys", "mode": "Mode", "devices": "Devices",
    "vibe": "Vibe %", "duration": "Duration (s)", "enabled": "Enabled",
}
BINDING_WIDTHS = {"id": 100, "keys": 170, "mode": 90, "devices": 150, "vibe": 90, "duration": 100, "enabled": 70}

# Shared layout constants so spacing stays consistent across every tab
# instead of a different magic number wherever a widget got added.
PADX = 10
PADY = 8
MONOSPACE_FONT = ("Consolas", 10)
HEADER_FONT = ("Segoe UI", 12, "bold")
# A mid-gray that stays legible against both sv_ttk's light background
# (near-white) and its dark background (near-black) - avoids needing a
# separate hint color per theme.
HINT_COLOR = "#8f8f8f"
DEFAULT_THEME = "dark"


class AsyncBridge:
    """Runs an asyncio event loop on a background thread so the Tk main loop never blocks on I/O."""

    def __init__(self):
        """Create a fresh event loop and immediately start a daemon thread running it forever (see _run)."""
        # A fresh event loop (not the default one, since there isn't one on
        # a non-main thread) that self._run() will drive for the lifetime
        # of the app.
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        """Thread target: make `self.loop` this thread's event loop and run it forever (until App._on_close stops it)."""
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        """
        Schedule a coroutine to run on the bridge's loop from any thread
        (typically the Tk main thread, from a button handler) and return a
        concurrent.futures.Future for it. Callers attach a callback with
        `.add_done_callback(...)` and marshal back to the Tk thread via
        `root.after(0, ...)` inside it, rather than blocking on `.result()`
        - see e.g. App._on_connect_clicked for the pattern used everywhere.
        """
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


class App:
    """
    The whole GUI: builds every tab, owns the one HapticsController and
    AsyncBridge for the app's lifetime, and bridges between Tkinter's
    single-threaded event loop and the controller's asyncio coroutines.
    """

    def __init__(self, root):
        """
        Apply styling, create the controller/bridge, build every tab, then
        populate them from whatever's already on disk (profiles, devices
        registry, config). `root` is expected to already be a real,
        visible Tk() - the age gate in main() handles showing/hiding it
        before this ever runs.
        """
        self.root = root
        root.title(f"{PROJECT_SHORT_NAME} - {PROJECT_NAME} (v{__version__})")
        root.geometry("1040x720")
        root.minsize(860, 600)

        self._apply_style(root)

        self.log_queue = queue.Queue()
        self.bridge = AsyncBridge()
        self.controller = HapticsController(tighc.INTIFACE_WS, dict(tighc.PROFILES), log_fn=self._enqueue_log)

        self.current_profile_id = None
        self.current_bindings = []  # editable plain-dict copies of the loaded profile's bindings
        self._test_channel_widgets = {}  # nickname -> {"level_var", "hold_var"}, rebuilt by _refresh_test_channels
        self._test_binding_widgets = {}  # binding id -> (hold_var, tokens), continuous bindings only
        self._scrollable_canvases = []  # plain Tk Canvases from _build_scrollable_body(), re-themed alongside log_text/changelog_text

        self._build_ui()
        # Population order matters: profiles first (so the Test tab's profile
        # picker has something to select), then channels (so its manual-test
        # rows exist before anything tries to reference them).
        self._refresh_profile_list()
        self._refresh_channels_tree()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._log_poll_id = self.root.after(150, self._poll_log_queue)
        self._status_poll_id = self.root.after(400, self._poll_status)

    def _apply_style(self, root):
        """
        Reskin every ttk widget with sv_ttk's "Sun Valley" theme (modern
        Windows 11/Fluent look: flat buttons, a deliberate light/dark
        palette) instead of the dated stock ttk themes (vista/clam/etc).

        sv_ttk owns widget colors and shape; we only layer fonts/padding and
        our own named styles (Header.TLabel, Hint.TLabel) on top - and since
        sv_ttk.set_theme() reloads ttk's entire style database, this whole
        method (not just the sv_ttk call) has to re-run after every theme
        switch. See _on_toggle_theme().
        """
        self.theme = DEFAULT_THEME
        sv_ttk.set_theme(self.theme, root)
        self._apply_custom_style_layer(root)

    @staticmethod
    def _apply_custom_style_layer(root):
        """The style tweaks that sit on top of whichever sv_ttk palette is currently active."""
        style = ttk.Style(root)
        root.option_add("*Font", "{Segoe UI} 10")
        style.configure("TNotebook.Tab", padding=(16, 8), font=("Segoe UI", 10))
        style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", padding=(8, 4))
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("Header.TLabel", font=HEADER_FONT)
        style.configure("Hint.TLabel", foreground=HINT_COLOR)

    def _on_toggle_theme(self):
        """Flip sv_ttk between its light and dark palettes and refresh our own style layer to match."""
        sv_ttk.toggle_theme(self.root)
        self.theme = sv_ttk.get_theme(self.root)
        self._apply_custom_style_layer(self.root)
        self.theme_toggle_btn.config(text=self._theme_toggle_label())
        self._restyle_text_widgets()

    def _theme_toggle_label(self):
        """Button text names the theme you'd switch *to*, not the one currently active."""
        return "Switch to light mode" if self.theme == "dark" else "Switch to dark mode"

    # =============================================================== UI shell
    def _build_ui(self):
        """Construct the top bar (theme toggle) and the tab notebook, then delegate to each tab's own _build_*_tab method."""
        # A slim top bar sits above the tab notebook, outside it, so the
        # theme toggle is reachable from every tab instead of duplicated
        # into each one.
        top_bar = ttk.Frame(self.root)
        top_bar.pack(fill="x", padx=PADX // 2, pady=(PADY // 2, 0))
        self.theme_toggle_btn = ttk.Button(top_bar, text=self._theme_toggle_label(), command=self._on_toggle_theme)
        self.theme_toggle_btn.pack(side="right")
        # Visible from every tab, not just Devices/Run - kept current by
        # _poll_status() (same 400ms poll that already drove the Run tab's
        # "Live status" label) so there's always a clear, hard-to-miss
        # answer to "is this actually connected right now", regardless of
        # which tab happens to be open.
        self.connection_status_var = tk.StringVar(value="Not connected")
        ttk.Label(top_bar, textvariable=self.connection_status_var, style="Hint.TLabel").pack(side="left")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=PADX // 2, pady=PADY // 2)

        self.devices_tab = ttk.Frame(notebook)
        self.profiles_tab = ttk.Frame(notebook)
        self.test_tab = ttk.Frame(notebook)
        self.settings_tab = ttk.Frame(notebook)
        self.run_tab = ttk.Frame(notebook)
        self.about_tab = ttk.Frame(notebook)
        notebook.add(self.devices_tab, text="Devices")
        notebook.add(self.profiles_tab, text="Profiles")
        notebook.add(self.test_tab, text="Test")
        notebook.add(self.settings_tab, text="Settings")
        notebook.add(self.run_tab, text="Run")
        notebook.add(self.about_tab, text="About")

        self._build_devices_tab()
        self._build_profiles_tab()
        self._build_test_tab()
        self._build_settings_tab()
        self._build_run_tab()
        self._build_about_tab()

        # log_text and changelog_text now both exist - color them to match
        # the active theme (see _restyle_text_widgets for why this is needed
        # at all).
        self._restyle_text_widgets()

    def _text_widget_colors(self) -> dict:
        """bg/fg/cursor-color for classic Tk text widgets, matched to sv_ttk's current palette."""
        if self.theme == "dark":
            return {"bg": "#1e1e1e", "fg": "#e6e6e6", "insertbackground": "#e6e6e6"}
        return {"bg": "#ffffff", "fg": "#1c1c1c", "insertbackground": "#1c1c1c"}

    def _restyle_text_widgets(self):
        """
        scrolledtext.ScrolledText (used for the Run tab's log and the About
        tab's changelog viewer) is a classic Tk widget, not ttk - sv_ttk only
        reskins ttk widgets, so these two would otherwise stay a plain white
        Tk text box regardless of theme. Called once after both are built,
        and again every time the theme is toggled.
        """
        colors = self._text_widget_colors()
        for widget in (getattr(self, "log_text", None), getattr(self, "changelog_text", None)):
            if widget is not None:
                widget.configure(**colors)
        for canvas in self._scrollable_canvases:
            canvas.configure(bg=colors["bg"])

    @staticmethod
    def _add_header(frame, text):
        """A bold title at the top of a tab, so each one reads like a distinct page rather than a bare form."""
        ttk.Label(frame, text=text, style="Header.TLabel").pack(fill="x", padx=PADX, pady=(PADY, 0))

    def _build_scrollable_body(self, parent) -> ttk.Frame:
        """
        Wrap a tab's content area in a vertically-scrollable canvas and
        return the inner ttk.Frame to build the tab's real content into -
        used as a drop-in replacement for a plain `ttk.Frame(parent)` body.

        Tabs with a lot of stacked form rows (Settings, in particular) can
        end up taller than the window once every setting/section is laid
        out; a bare grid/pack body just clips anything past the visible
        area with no way to reach it, since the app's minsize allows a
        window well short of that. The canvas is a raw Tk widget (not
        ttk), so its background is set from _text_widget_colors() and kept
        in sync by _restyle_text_widgets() on every theme toggle, same as
        the log/changelog text boxes.
        """
        canvas = tk.Canvas(parent, highlightthickness=0, bg=self._text_widget_colors()["bg"])
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas, padding=(PADX, PADY))

        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")
        # Keep the embedded frame exactly as wide as the visible canvas, so
        # its grid columns lay out against the real available width instead
        # of shrinking to whatever the widest row happens to need.
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(body_window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mouse wheel scrolling only while the cursor is actually over this
        # canvas - bind_all/unbind_all on hover keeps the wheel from being
        # hijacked by whichever scrollable tab happened to build last.
        def _on_wheel(event):
            """<MouseWheel> handler: Windows/macOS report a signed delta in multiples of 120, so dividing by it gives whole scroll "clicks"."""
            canvas.yview_scroll(int(-event.delta / 120), "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        self._scrollable_canvases.append(canvas)
        return body

    # =============================================================== Cover art (SteamGridDB)
    # Shared by the Profiles and Test tabs, each of which keeps its own
    # thumbnail Label + PhotoImage reference (see _refresh_profile_artwork /
    # _refresh_test_artwork) but funnels through this one fetch/load path.
    @staticmethod
    def _load_thumbnail_image(path):
        """
        Load a cached artwork PNG into a tk.PhotoImage, downscaled to
        roughly ARTWORK_THUMBNAIL_WIDTH wide. tk.PhotoImage only supports
        PNG/GIF/PPM natively (no Pillow dependency needed, since
        tighc.py's cover-art fetcher only ever downloads PNGs) and can only shrink by
        integer factors via subsample() - fine for a small thumbnail, if
        slightly cruder than a real resize.
        """
        image = tk.PhotoImage(file=str(path))
        width = image.width()
        if width > ARTWORK_THUMBNAIL_WIDTH:
            factor = max(1, width // ARTWORK_THUMBNAIL_WIDTH)
            image = image.subsample(factor, factor)
        return image

    @staticmethod
    def _photoimage_from_bytes(data: bytes, target_width: int):
        """
        Same downscaling trick as _load_thumbnail_image(), but from
        in-memory PNG bytes rather than a cached file - used by
        _on_choose_cover_image()'s picker, which previews every candidate
        image before any of them is actually chosen/cached to disk.
        """
        image = tk.PhotoImage(data=data)
        width = image.width()
        if width > target_width:
            factor = max(1, width // target_width)
            image = image.subsample(factor, factor)
        return image

    def _fetch_artwork_async(self, profile_id, profile_name, override_id, override_grid_id, on_done, force_refresh=False):
        """
        Fetch (or load from cache) one profile's artwork on a daemon
        thread - tighc.get_profile_artwork() does blocking network
        I/O, which must not run on the Tk main thread - then hand the
        resulting Path (or None) back to `on_done` via root.after so it's
        safe to touch widgets from there.
        """
        def worker():
            """Runs on the background thread: do the blocking fetch, then hand off to on_done via root.after (thread-safe)."""
            path = tighc.get_profile_artwork(
                profile_id,
                profile_name,
                override_id,
                override_grid_id=override_grid_id,
                force_refresh=force_refresh,
                log_fn=self._enqueue_log,
            )
            self.root.after(0, on_done, path)

        threading.Thread(target=worker, daemon=True).start()

    # =============================================================== Devices tab
    def _build_devices_tab(self):
        """Build the Devices tab: connection controls, the channel list, and the rename button."""
        frame = self.devices_tab
        self._add_header(frame, "Devices")

        # Connection controls: URL + Connect (fresh connection, includes an
        # initial scan) + Rescan (reuse the existing connection, look again).
        top = ttk.Frame(frame)
        top.pack(fill="x", padx=PADX, pady=PADY)
        # Read-only display, not an editable field here - the Settings tab
        # is the one place that actually persists intiface_ws (to
        # haptics_config.json), so letting this one be edited too would
        # just be a second, easy-to-forget-about place the URL could
        # silently diverge from what's actually saved.
        ttk.Label(top, text="Intiface WebSocket URL:").pack(side="left")
        self.ws_url_var = tk.StringVar(value=self.controller.ws_url)
        ttk.Entry(top, textvariable=self.ws_url_var, width=30, state="readonly").pack(side="left", padx=6)
        ttk.Label(top, text="(set in Settings)", style="Hint.TLabel").pack(side="left", padx=(0, 6))
        self.connect_btn = ttk.Button(top, text="Connect + Scan", command=self._on_connect_clicked)
        self.connect_btn.pack(side="left", padx=4)
        self.rescan_btn = ttk.Button(top, text="Rescan", command=self._on_rescan_clicked)
        self.rescan_btn.pack(side="left", padx=4)
        self.disconnect_btn = ttk.Button(top, text="Disconnect", command=self._on_disconnect_clicked, state="disabled")
        self.disconnect_btn.pack(side="left", padx=4)

        # One row per channel - i.e. per motor/capability, not per physical
        # toy - so a dual-motor device shows up as two rows here.
        columns = ("nickname", "device", "motor", "output")
        self.devices_tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        for col, label, width in (
            ("nickname", "Nickname", 220), ("device", "Device", 220), ("motor", "Motor", 180), ("output", "Output", 100)
        ):
            self.devices_tree.heading(col, text=label)
            self.devices_tree.column(col, width=width, anchor="w")
        self.devices_tree.pack(fill="both", expand=True, padx=PADX, pady=(0, PADY))

        btns = ttk.Frame(frame)
        btns.pack(fill="x", padx=PADX, pady=(0, PADY))
        ttk.Button(btns, text="Rename nickname...", command=self._on_rename_channel).pack(side="left")
        ttk.Label(
            btns,
            text="Bindings target channels by nickname (or \"all\"). Each motor/capability is its own channel.",
            style="Hint.TLabel",
        ).pack(side="left", padx=12)

    def _on_connect_clicked(self):
        """
        "Connect + Scan" button handler. Disables the button, submits
        controller.connect() to the async bridge, and re-enables it once
        the result comes back via _after_connect - the button itself is
        the only thing guarding against a second click firing a second
        connection attempt while the first is still in flight. Uses
        controller.ws_url as-is; the URL field next to this button is
        read-only (see _build_devices_tab) - Settings is the only place
        that changes it.
        """
        self.connect_btn.config(state="disabled")
        self.connection_status_var.set(f"Connecting to {self.controller.ws_url} ...")
        self._enqueue_log(f"Connecting to {self.controller.ws_url} ...")
        fut = self.bridge.submit(self.controller.connect())
        fut.add_done_callback(lambda f: self.root.after(0, self._after_connect, f))

    def _after_connect(self, fut):
        """
        Runs on the Tk main thread (via root.after) once connect() finishes.
        `fut` is the concurrent.futures.Future from AsyncBridge.submit();
        `.result()` re-raises whatever exception the coroutine raised, if
        any, which is caught here and logged rather than crashing the GUI.
        """
        self.connect_btn.config(state="normal")
        try:
            fut.result()
        except Exception as e:
            self._enqueue_log(f"Connect failed: {e}")
        self.disconnect_btn.config(state="normal" if self.controller.client else "disabled")
        self._refresh_channels_tree()

    def _on_disconnect_clicked(self):
        """
        "Disconnect" button handler: stops the engine (if running) and
        disconnects from Intiface via controller.shutdown() - unlike at app
        close, this leaves the app running so the user can reconnect
        afterward (e.g. to point at a different WebSocket URL, or after
        restarting Intiface itself). Also resets the Run tab's Start/Stop
        buttons and status, since shutdown() stops the engine along the way.
        """
        self.connect_btn.config(state="disabled")
        self.rescan_btn.config(state="disabled")
        self.disconnect_btn.config(state="disabled")
        fut = self.bridge.submit(self.controller.shutdown())
        fut.add_done_callback(lambda f: self.root.after(0, self._after_disconnect, f))

    def _after_disconnect(self, fut):
        """Runs on the Tk main thread once shutdown() finishes; see _after_connect for the pattern."""
        try:
            fut.result()
        except Exception as e:
            self._enqueue_log(f"Disconnect error: {e}")
        self.connect_btn.config(state="normal")
        self.rescan_btn.config(state="normal")
        self.disconnect_btn.config(state="disabled")
        # shutdown() stops the engine along the way - reflect that on the
        # Run tab too, same button/status states _after_stop() sets.
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_var.set("Stopped")
        self._refresh_channels_tree()
        self._enqueue_log("Disconnected from Intiface.")

    def _on_rescan_clicked(self):
        """"Rescan" button handler - same async pattern as _on_connect_clicked, but calls controller.scan() instead."""
        self.rescan_btn.config(state="disabled")
        fut = self.bridge.submit(self.controller.scan())
        fut.add_done_callback(lambda f: self.root.after(0, self._after_rescan, f))

    def _after_rescan(self, fut):
        """Runs on the Tk main thread once scan() finishes - see _after_connect for the pattern."""
        self.rescan_btn.config(state="normal")
        try:
            fut.result()
        except Exception as e:
            self._enqueue_log(f"Scan failed: {e}")
        self._refresh_channels_tree()

    def _refresh_channels_tree(self):
        """Rebuild the Devices tab's channel list from self.controller.channels - call after anything that changes it."""
        self.devices_tree.delete(*self.devices_tree.get_children())
        for nickname, channel in sorted(self.controller.channels.items()):
            self.devices_tree.insert(
                "", "end", iid=nickname,
                values=(nickname, channel.device_name, channel.description or "-", channel.output_type.value),
            )
        # The Test tab's per-channel controls mirror this list 1:1, so any
        # time the channel set changes (connect/rescan/rename) they do too.
        self._refresh_test_channels()

    def _on_rename_channel(self):
        """
        "Rename nickname..." button handler: prompts for a new nickname for
        the selected channel, updates its entry in devices.json (matched by
        device_name+feature_index+output_type, not by the old nickname, so
        this works even if devices.json's copy is already out of sync),
        renames the live DeviceChannel in memory, and warns - without
        blocking - if any saved profile binding still references the old
        nickname (those bindings simply stop targeting this channel until
        the user updates them; nothing here rewrites profile JSON).
        """
        selection = self.devices_tree.selection()
        if not selection:
            messagebox.showinfo("Rename", "Select a channel first.")
            return
        old_nickname = selection[0]
        new_nickname = simpledialog.askstring(
            "Rename channel", f"New nickname for '{old_nickname}':", initialvalue=old_nickname, parent=self.root
        )
        if not new_nickname:
            return
        new_nickname = new_nickname.strip().lower()
        if not new_nickname or new_nickname == old_nickname:
            return
        if new_nickname in self.controller.channels:
            messagebox.showerror("Rename", f"'{new_nickname}' is already in use.")
            return

        channel = self.controller.channels[old_nickname]
        registry = load_device_registry()
        matched = False
        for entry in registry:
            if (
                entry["device_name"] == channel.device_name
                and entry["feature_index"] == channel.feature.index
                and entry["output_type"] == channel.output_type.value
            ):
                entry["nickname"] = new_nickname
                matched = True
                break
        if not matched:
            registry.append(
                {
                    "device_name": channel.device_name,
                    "feature_index": channel.feature.index,
                    "output_type": channel.output_type.value,
                    "description": channel.description,
                    "nickname": new_nickname,
                }
            )
        save_device_registry(registry)

        stale_references = self._find_nickname_references(old_nickname)
        del self.controller.channels[old_nickname]
        channel.nickname = new_nickname
        self.controller.channels[new_nickname] = channel
        self._refresh_channels_tree()
        self._enqueue_log(f"Renamed channel '{old_nickname}' -> '{new_nickname}'.")

        if stale_references:
            listed = "\n".join(stale_references)
            messagebox.showwarning(
                "Rename",
                f"Renamed to '{new_nickname}'.\n\nThese saved bindings still reference the old nickname "
                f"'{old_nickname}' and won't target this channel until you update them:\n\n{listed}",
            )

    def _find_nickname_references(self, nickname):
        """Return ["Profile Name: binding_id", ...] for every currently-loaded binding whose devices list includes `nickname`."""
        references = []
        for profile in self.controller.profiles.values():
            for binding in profile.bindings:
                devices = binding["devices"]
                if devices is not None and nickname in devices:
                    references.append(f"{profile.name}: {binding['id']}")
        return references

    # =============================================================== Profiles tab
    def _build_profiles_tab(self):
        """
        Build the Profiles tab: a profile picker, an editable form for the
        profile-level settings (name/window match/priority/idle range), and
        a bindings table with add/edit/remove buttons. The form and table
        only hold a working copy of whatever profile is selected (see
        self.current_bindings) - nothing here writes to disk until "Save
        profile" is clicked (_on_save_profile).
        """
        frame = self.profiles_tab
        self._add_header(frame, "Profiles")

        top = ttk.Frame(frame)
        top.pack(fill="x", padx=PADX, pady=PADY)
        ttk.Label(top, text="Profile:").pack(side="left")
        self.profile_combo = ttk.Combobox(top, state="readonly", width=25)
        self.profile_combo.pack(side="left", padx=6)
        self.profile_combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self._load_profile_into_form(self._profile_id_for_display(self.profile_combo.get())),
        )
        ttk.Button(top, text="New profile...", command=self._on_new_profile).pack(side="left", padx=4)
        ttk.Button(top, text="Reload all from disk", command=self._on_reload_profiles).pack(side="left", padx=4)

        meta = ttk.LabelFrame(frame, text="Profile settings")
        meta.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(meta, text="Display name:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.profile_name_var = tk.StringVar()
        ttk.Entry(meta, textvariable=self.profile_name_var, width=30).grid(row=0, column=1, sticky="w", padx=4, pady=2)

        ttk.Label(meta, text="Window title match(es), comma-separated:").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        self.profile_windows_var = tk.StringVar()
        ttk.Entry(meta, textvariable=self.profile_windows_var, width=50).grid(row=1, column=1, sticky="w", padx=4, pady=2)

        ttk.Label(meta, text="Continuous priority order (comma-separated ids):").grid(row=2, column=0, sticky="w", padx=4, pady=2)
        self.profile_priority_var = tk.StringVar()
        ttk.Entry(meta, textvariable=self.profile_priority_var, width=50).grid(row=2, column=1, sticky="w", padx=4, pady=2)

        ttk.Label(meta, text="Idle (background) vibe % low/high:").grid(row=3, column=0, sticky="w", padx=4, pady=2)
        bg_frame = ttk.Frame(meta)
        bg_frame.grid(row=3, column=1, sticky="w")
        self.profile_bg_low_var = tk.StringVar()
        self.profile_bg_high_var = tk.StringVar()
        ttk.Entry(bg_frame, textvariable=self.profile_bg_low_var, width=8).pack(side="left")
        ttk.Entry(bg_frame, textvariable=self.profile_bg_high_var, width=8).pack(side="left", padx=4)

        ttk.Button(meta, text="Save profile", command=self._on_save_profile).grid(row=4, column=1, sticky="w", pady=6)

        # Cover art thumbnail sits in its own column, spanning every row
        # above so it reads as "attached to this profile" rather than just
        # another form field.
        artwork_frame = ttk.Frame(meta)
        artwork_frame.grid(row=0, column=2, rowspan=5, sticky="n", padx=(16, 4))
        self.profile_artwork_label = ttk.Label(artwork_frame, text="(no cover art)", style="Hint.TLabel")
        self.profile_artwork_label.pack()
        ttk.Button(artwork_frame, text="Change cover art...", command=self._on_change_artwork).pack(pady=(6, 0))
        ttk.Button(artwork_frame, text="Choose image...", command=self._on_choose_cover_image).pack(pady=(4, 0))
        self._profile_artwork_image = None  # keep a reference - PhotoImage is garbage-collected otherwise

        bindings_frame = ttk.LabelFrame(frame, text="Bindings")
        bindings_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.bindings_tree = ttk.Treeview(bindings_frame, columns=BINDING_COLUMNS, show="headings", selectmode="browse")
        for col in BINDING_COLUMNS:
            self.bindings_tree.heading(col, text=BINDING_HEADERS[col])
            self.bindings_tree.column(col, width=BINDING_WIDTHS[col], anchor="w")
        self.bindings_tree.pack(fill="both", expand=True, side="top")

        btns = ttk.Frame(bindings_frame)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="Add binding...", command=self._on_add_binding).pack(side="left")
        ttk.Button(btns, text="Edit binding...", command=self._on_edit_binding).pack(side="left", padx=4)
        ttk.Button(btns, text="Remove binding", command=self._on_remove_binding).pack(side="left")
        ttk.Label(
            btns, text="(remember to Save profile above after changing bindings)", style="Hint.TLabel"
        ).pack(side="left", padx=12)

    def _profile_display_name(self, profile_id: str) -> str:
        """
        Display text for one profile in the Profiles/Test tab pickers -
        the profile's own `name` from keybinds.json (e.g. "Cult of the
        Lamb"), not the raw folder id (e.g. "cult_of_the_lamb") the picker
        widgets are otherwise keyed by everywhere else in this file. Falls
        back to the id itself if it's somehow not loaded (shouldn't
        normally happen).
        """
        profile = self.controller.profiles.get(profile_id)
        return profile.name if profile else profile_id

    def _profile_id_for_display(self, display_name: str) -> str:
        """
        Reverse of _profile_display_name(): given a picker's currently-
        shown text, find which profile id it corresponds to. Falls back to
        treating the text itself as an id if nothing matches (e.g. the
        picker is empty, or - extremely unlikely - two profiles share a
        display name and this just picks whichever iterates first).
        """
        for profile_id, profile in self.controller.profiles.items():
            if profile.name == display_name:
                return profile_id
        return display_name

    def _refresh_profile_list(self):
        """
        Repopulate both the Profiles tab's and the Test tab's profile
        dropdowns from self.controller.profiles, keeping the current
        selection if it's still valid (falling back to the alphabetically
        first profile otherwise). Called after anything that adds, removes,
        or reloads profiles. The pickers display each profile's `name`
        (see _profile_display_name), while every other piece of code in
        this file keys off the raw id - _profile_id_for_display() bridges
        the two wherever a picker's current selection needs to be read.
        """
        ids = sorted(self.controller.profiles.keys())
        display_names = sorted(self._profile_display_name(pid) for pid in ids)
        self.profile_combo["values"] = display_names
        self.test_profile_combo["values"] = display_names
        if not ids:
            self.current_profile_id = None
            self._refresh_test_bindings()
            self._refresh_test_artwork()
            return
        if self.current_profile_id not in ids:
            self.current_profile_id = ids[0]
        self.profile_combo.set(self._profile_display_name(self.current_profile_id))
        self._load_profile_into_form(self.current_profile_id)
        if self._profile_id_for_display(self.test_profile_combo.get()) not in ids:
            self.test_profile_combo.set(self._profile_display_name(self.current_profile_id))
        self._refresh_test_bindings()
        self._refresh_test_artwork()

    @staticmethod
    def _binding_to_editable(binding: dict) -> dict:
        """
        Convert one of Profile.bindings' parsed dicts (which hold VibeRange/
        DurationRange/frozenset objects, as produced by tighc._load_profile)
        into a plain, JSON-friendly, Tkinter-Var-friendly dict that the
        bindings table and the add/edit dialog can work with directly.
        The reverse conversion happens in _compose_profile_files().
        """
        return {
            "id": binding["id"],
            "keys": list(binding["keys"]),
            "mode": binding["mode"],
            "enabled": binding["enabled"],
            "devices": ["all"] if binding["devices"] is None else sorted(binding["devices"]),
            "vibe_low": binding["vibe"].low,
            "vibe_high": binding["vibe"].high,
            "duration_low": binding["duration"].low if binding["duration"] else None,
            "duration_high": binding["duration"].high if binding["duration"] else None,
        }

    def _load_profile_into_form(self, profile_id):
        """
        Populate the Profiles tab's form fields and bindings table from
        `self.controller.profiles[profile_id]`, replacing whatever unsaved
        edits were in the form for the previously-selected profile. This is
        also how a save re-syncs the form: _on_save_profile() calls back
        into this after writing+reloading, so the form always reflects the
        canonical on-disk/parsed state rather than raw user input.
        """
        if not profile_id or profile_id not in self.controller.profiles:
            return
        self.current_profile_id = profile_id
        profile = self.controller.profiles[profile_id]
        self.profile_name_var.set(profile.name)
        self.profile_windows_var.set(", ".join(profile.window_titles))
        self.profile_priority_var.set(", ".join(b.id for b in profile.continuous))
        self.profile_bg_low_var.set(f"{profile.background.low:.2f}")
        self.profile_bg_high_var.set(f"{profile.background.high:.2f}")
        self.current_bindings = [self._binding_to_editable(b) for b in profile.bindings]
        self._refresh_bindings_tree()
        self._refresh_profile_artwork()

    def _refresh_profile_artwork(self, force_refresh=False):
        """
        Kick off an async fetch of the current profile's cover art and
        update the thumbnail once it resolves (or show "(no cover art)" if
        it can't be found/fetched/disabled - see tighc.get_profile_artwork
        for exactly what that covers). Called whenever the selected profile
        changes, and after cover-art settings or the profile's
        steamgriddb_id are changed.
        """
        profile = self.controller.profiles.get(self.current_profile_id)
        if not profile:
            self.profile_artwork_label.config(image="", text="(no profile selected)")
            return
        self.profile_artwork_label.config(image="", text="Loading cover art...")
        requested_profile_id = self.current_profile_id

        def on_done(path):
            """Runs on the Tk main thread once the fetch resolves; updates the thumbnail unless the selection has since moved on."""
            # The user may have switched to a different profile while this
            # fetch was in flight - discard a stale result rather than
            # showing the wrong game's art on top of the current selection.
            if requested_profile_id != self.current_profile_id:
                return
            if path is None:
                self.profile_artwork_label.config(image="", text="(no cover art)")
                return
            self._profile_artwork_image = self._load_thumbnail_image(path)
            self.profile_artwork_label.config(image=self._profile_artwork_image, text="")

        self._fetch_artwork_async(
            profile.id, profile.name, profile.steamgriddb_id, profile.steamgriddb_grid_id, on_done, force_refresh
        )

    def _refresh_bindings_tree(self):
        """Rebuild the bindings table from self.current_bindings (the in-memory working copy, not necessarily what's on disk)."""
        self.bindings_tree.delete(*self.bindings_tree.get_children())
        for i, b in enumerate(self.current_bindings):
            vibe = f"{b['vibe_low'] * 100:.0f}-{b['vibe_high'] * 100:.0f}"
            duration = f"{b['duration_low']:.2f}-{b['duration_high']:.2f}" if b["duration_low"] is not None else "-"
            self.bindings_tree.insert(
                "", "end", iid=str(i),
                values=(
                    b["id"], "+".join(b["keys"]), b["mode"], ",".join(b["devices"]),
                    vibe, duration, "yes" if b["enabled"] else "no",
                ),
            )

    def _on_new_profile(self):
        """
        "New profile..." button handler: asks for a folder id, display name,
        and window-title match, then seeds the new profile's keybinds.json/
        ranges.json from an existing profiles/minecraft/ on disk if present
        (so a user's own edits to it get carried forward as the template),
        falling back to the hardcoded DEFAULT_MINECRAFT_* dicts otherwise.
        Rolls the new folder back (shutil.rmtree) if the result somehow
        fails to parse, so a bad template can't leave a broken profile
        folder lying around.
        """
        new_id = simpledialog.askstring("New profile", "Folder id (letters/numbers/underscores):", parent=self.root)
        if not new_id:
            return
        new_id = tighc._slugify(new_id)
        new_dir = PROFILES_DIR / new_id
        if new_dir.exists():
            messagebox.showerror("New profile", f"profiles/{new_id} already exists.")
            return
        display_name = simpledialog.askstring(
            "New profile", "Display name:", initialvalue=new_id.title(), parent=self.root
        ) or new_id
        window_title = simpledialog.askstring(
            "New profile", "Window title to match (lowercase substring):", initialvalue=new_id.lower(), parent=self.root
        ) or new_id.lower()

        template_dir = PROFILES_DIR / "minecraft"
        if (template_dir / "keybinds.json").exists() and (template_dir / "ranges.json").exists():
            keybinds = json.loads((template_dir / "keybinds.json").read_text(encoding="utf-8"))
            ranges = json.loads((template_dir / "ranges.json").read_text(encoding="utf-8"))
        else:
            keybinds = copy.deepcopy(tighc.DEFAULT_MINECRAFT_KEYBINDS)
            ranges = copy.deepcopy(tighc.DEFAULT_MINECRAFT_RANGES)
        keybinds["name"] = display_name
        keybinds["window_titles"] = [window_title.lower()]

        new_dir.mkdir(parents=True)
        (new_dir / "keybinds.json").write_text(json.dumps(keybinds, indent=2), encoding="utf-8")
        (new_dir / "ranges.json").write_text(json.dumps(ranges, indent=2), encoding="utf-8")
        try:
            profile = tighc._load_profile(new_dir)
        except Exception as e:
            shutil.rmtree(new_dir)
            messagebox.showerror("New profile", f"Failed to create profile: {e}")
            return

        self.controller.profiles[profile.id] = profile
        self.current_profile_id = profile.id
        self._refresh_profile_list()
        self._enqueue_log(f"Created profile '{profile.name}' (copied bindings from the Minecraft template).")

    def _on_reload_profiles(self):
        """
        "Reload all from disk" button handler: re-runs the full profile
        loader (picking up any hand-edited JSON) and replaces the
        controller's profile set in place - mutating the existing dict
        rather than assigning a new one, since background_loop() holds a
        reference to `self.controller.profiles` and needs to see the
        update immediately, not just after some future re-read.
        """
        try:
            fresh = load_profiles()
        except RuntimeError as e:
            messagebox.showerror("Reload profiles", str(e))
            return
        self.controller.profiles.clear()
        self.controller.profiles.update(fresh)
        self._refresh_profile_list()
        self._enqueue_log("Profiles reloaded from disk.")

    def _compose_profile_files(self):
        """
        Build the (keybinds_dict, ranges_dict) pair to write to disk from
        the current form fields and self.current_bindings - the reverse of
        _binding_to_editable(). Raises ValueError (with a message meant to
        be shown to the user directly) on anything that won't parse:
        missing window title, or a VibeRange/DurationRange constructed with
        low > high or out-of-bounds values. Does not touch disk itself -
        see _on_save_profile() for the write+validate+rollback flow that
        calls this.
        """
        name = self.profile_name_var.get().strip() or self.current_profile_id
        window_titles = [t.strip().lower() for t in self.profile_windows_var.get().split(",") if t.strip()]
        if not window_titles:
            raise ValueError("at least one window title is required")
        priority = [t.strip() for t in self.profile_priority_var.get().split(",") if t.strip()]
        bg_low = float(self.profile_bg_low_var.get())
        bg_high = float(self.profile_bg_high_var.get())
        VibeRange(bg_low, bg_high)

        keybinds = {
            "name": name,
            "window_titles": window_titles,
            "priority": priority,
            "bindings": [
                {
                    "id": b["id"], "keys": b["keys"], "mode": b["mode"],
                    "enabled": b["enabled"], "devices": b["devices"],
                }
                for b in self.current_bindings
            ],
        }
        # steamgriddb_id isn't editable via this form (see
        # _on_change_artwork's dialog instead) - carry the currently-loaded
        # value forward so a routine save here doesn't silently clear it.
        current_profile = self.controller.profiles.get(self.current_profile_id)
        if current_profile and current_profile.steamgriddb_id is not None:
            keybinds["steamgriddb_id"] = current_profile.steamgriddb_id

        ranges = {"background": {"vibe": [bg_low, bg_high]}}
        for b in self.current_bindings:
            VibeRange(b["vibe_low"], b["vibe_high"])
            entry = {"vibe": [b["vibe_low"], b["vibe_high"]]}
            if b["mode"] == "pulse":
                DurationRange(b["duration_low"], b["duration_high"])
                entry["duration"] = [b["duration_low"], b["duration_high"]]
            ranges[b["id"]] = entry
        return keybinds, ranges

    def _on_save_profile(self):
        """
        "Save profile" button handler. Snapshots the current on-disk JSON
        first, writes the new keybinds.json/ranges.json from the form, then
        immediately tries to load them back through the real engine parser
        (tighc._load_profile) - if that fails, the snapshot is restored
        so a bad edit never leaves the profile folder in a broken state,
        and the error is shown to the user instead of only surfacing the
        next time the app starts. On success, refreshes both the
        controller's live profile and the form (to reflect the canonical
        parsed values, e.g. rounded numbers).
        """
        if not self.current_profile_id:
            return
        profile_dir = PROFILES_DIR / self.current_profile_id
        keybinds_path = profile_dir / "keybinds.json"
        ranges_path = profile_dir / "ranges.json"
        backup = (keybinds_path.read_text(encoding="utf-8"), ranges_path.read_text(encoding="utf-8"))

        try:
            keybinds, ranges = self._compose_profile_files()
        except ValueError as e:
            messagebox.showerror("Save profile", f"Invalid value: {e}")
            return

        keybinds_path.write_text(json.dumps(keybinds, indent=2), encoding="utf-8")
        ranges_path.write_text(json.dumps(ranges, indent=2), encoding="utf-8")
        try:
            profile = tighc._load_profile(profile_dir)
        except Exception as e:
            keybinds_path.write_text(backup[0], encoding="utf-8")
            ranges_path.write_text(backup[1], encoding="utf-8")
            messagebox.showerror("Save profile", f"Not saved - config is invalid:\n{e}")
            return

        self.controller.profiles[profile.id] = profile
        self._enqueue_log(f"Saved profile '{profile.name}'.")
        self._load_profile_into_form(profile.id)

    def _update_profile_steamgriddb_fields(self, profile_id, updates, log_message_fn):
        """
        Shared mechanics behind _set_profile_steamgriddb_id() and
        _set_profile_steamgriddb_grid_id() below: apply `updates` (a dict of
        keybinds.json field -> new value, where None means "remove this
        field") to a profile's keybinds.json, reload it through the real
        parser, refresh its artwork if it's the currently-selected profile,
        and log via `log_message_fn(profile)` - called after reload so it
        can use the profile's resolved display name. A targeted update
        rather than going through _compose_profile_files, since that only
        knows about what the Profiles tab's form itself edits.
        """
        profile_dir = PROFILES_DIR / profile_id
        keybinds_path = profile_dir / "keybinds.json"
        keybinds = json.loads(keybinds_path.read_text(encoding="utf-8"))
        for key, value in updates.items():
            if value is None:
                keybinds.pop(key, None)
            else:
                keybinds[key] = value
        keybinds_path.write_text(json.dumps(keybinds, indent=2), encoding="utf-8")

        try:
            profile = tighc._load_profile(profile_dir)
        except Exception as e:
            messagebox.showerror("Cover art", f"Failed to apply: {e}")
            return
        self.controller.profiles[profile.id] = profile
        if profile.id == self.current_profile_id:
            self._refresh_profile_artwork(force_refresh=True)
        self._enqueue_log(log_message_fn(profile))

    def _set_profile_steamgriddb_id(self, profile_id, game_id):
        """
        Write `game_id` (or None, to go back to searching by name) into a
        profile's keybinds.json as steamgriddb_id. Also clears any pinned
        cover image (steamgriddb_grid_id) - a specific image chosen for the
        old game wouldn't mean anything once the game itself changes. Used
        by _on_change_artwork.
        """
        self._update_profile_steamgriddb_fields(
            profile_id,
            {"steamgriddb_id": game_id, "steamgriddb_grid_id": None},
            lambda profile: (
                f"Cleared SteamGridDB override for '{profile.name}'." if game_id is None
                else f"Set SteamGridDB game id {game_id} for '{profile.name}'."
            ),
        )

    def _set_profile_steamgriddb_grid_id(self, profile_id, grid_id):
        """
        Write `grid_id` (or None, to go back to the default top-voted
        image) into a profile's keybinds.json as steamgriddb_grid_id,
        without touching any existing game override. Used by
        _on_choose_cover_image.
        """
        self._update_profile_steamgriddb_fields(
            profile_id,
            {"steamgriddb_grid_id": grid_id},
            lambda profile: (
                f"Cleared cover image override for '{profile.name}' (using the default again)." if grid_id is None
                else f"Set cover image {grid_id} for '{profile.name}'."
            ),
        )

    def _on_change_artwork(self):
        """
        "Change cover art..." button handler: opens a search dialog so the
        user can pin a profile to an exact SteamGridDB game (bypassing
        auto-search, which can pick the wrong game for an ambiguous or very
        new title). Requires cover art to already be enabled with a valid
        API key in Settings - this dialog only searches/selects, it doesn't
        configure the credential itself.
        """
        if not self.current_profile_id:
            return
        profile = self.controller.profiles[self.current_profile_id]
        config = tighc.load_steamgriddb_config()
        if not config.get("enabled") or not config.get("api_key"):
            messagebox.showinfo("Cover art", "Enable cover art and set an API key in Settings first.")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Choose SteamGridDB game")
        dialog.transient(self.root)
        dialog.grab_set()

        body = ttk.Frame(dialog, padding=10)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Search term:").grid(row=0, column=0, sticky="w")
        term_var = tk.StringVar(value=profile.name)
        ttk.Entry(body, textvariable=term_var, width=32).grid(row=0, column=1, sticky="w", padx=6)

        results_list = tk.Listbox(body, width=55, height=10)
        results_list.grid(row=1, column=0, columnspan=3, pady=8, sticky="nsew")
        results = []

        def do_search():
            """"Search" button handler (also called once up front to pre-populate results for the profile's own name)."""
            nonlocal results
            try:
                results = tighc.search_game(config["api_key"], term_var.get().strip())
            except Exception as e:
                messagebox.showerror("Search failed", str(e), parent=dialog)
                return
            results_list.delete(0, "end")
            for game in results:
                tag = " (verified)" if game.get("verified") else ""
                results_list.insert("end", f"{game['name']}{tag}  [id {game['id']}]")

        ttk.Button(body, text="Search", command=do_search).grid(row=0, column=2, padx=6)

        def use_selected():
            """"Use selected" button handler: pin the profile to the chosen search result's game id."""
            selection = results_list.curselection()
            if not selection:
                messagebox.showinfo("Cover art", "Select a game from the results first.", parent=dialog)
                return
            self._set_profile_steamgriddb_id(self.current_profile_id, results[selection[0]]["id"])
            dialog.destroy()

        def use_automatic():
            """"Use automatic search instead" button handler: clear any override so the profile goes back to searching by name."""
            self._set_profile_steamgriddb_id(self.current_profile_id, None)
            dialog.destroy()

        btns = ttk.Frame(body)
        btns.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        ttk.Button(btns, text="Use selected", command=use_selected).pack(side="left")
        ttk.Button(btns, text="Use automatic search instead", command=use_automatic).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=dialog.destroy).pack(side="right")

        do_search()

    def _on_choose_cover_image(self):
        """
        "Choose image..." button handler: resolves the current profile's
        game (via its steamgriddb_id override if set, else by name search -
        the same resolution get_profile_artwork() itself would use) and
        opens a dialog of thumbnails for every available cover image, so
        the user can pin a specific one instead of the default top-voted
        pick _on_change_artwork()/get_profile_artwork() use automatically.
        Requires cover art to already be enabled with a valid API key in
        Settings, same as _on_change_artwork.
        """
        if not self.current_profile_id:
            return
        profile = self.controller.profiles[self.current_profile_id]
        config = tighc.load_steamgriddb_config()
        if not config.get("enabled") or not config.get("api_key"):
            messagebox.showinfo("Cover art", "Enable cover art and set an API key in Settings first.")
            return
        api_key = config["api_key"]

        dialog = tk.Toplevel(self.root)
        dialog.title(f"Choose cover image - {profile.name}")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("620x520")
        loading_label = ttk.Label(dialog, text="Loading available cover images...", style="Hint.TLabel")
        loading_label.pack(pady=40)

        def worker():
            """
            Runs on the background thread: resolve the game, list its
            grids, and download every candidate's full image (get_grids()
            already restricts results to PNG, so these load directly into
            tk.PhotoImage - see _photoimage_from_bytes). Everything is
            handed back to on_resolved() via root.after in one shot rather
            than incrementally, so partial/interleaved UI updates from a
            background thread are never a concern.
            """
            result = {"game_id": None, "total": 0, "previews": [], "error": None}
            try:
                game_id = tighc._resolve_game_id(api_key, profile.name, profile.steamgriddb_id)
                result["game_id"] = game_id
                if game_id is not None:
                    grids = tighc.get_grids(api_key, game_id)
                    result["total"] = len(grids)
                    for grid in grids[:GRID_PICKER_LIMIT]:
                        try:
                            image_bytes = tighc.download_image_bytes(grid["url"])
                        except Exception:
                            continue  # one bad image shouldn't sink the whole picker
                        result["previews"].append((grid, image_bytes))
            except Exception as e:
                result["error"] = str(e)
            self.root.after(0, on_resolved, result)

        def on_resolved(result):
            """Runs on the Tk main thread once every preview has been fetched: replace the loading placeholder with the actual picker UI."""
            loading_label.destroy()

            if result["error"]:
                ttk.Label(
                    dialog, text=f"Failed to load cover images:\n{result['error']}", style="Hint.TLabel", justify="center"
                ).pack(pady=40, padx=20)
                return
            if result["game_id"] is None:
                ttk.Label(
                    dialog, text="Could not resolve this profile to a SteamGridDB game.", style="Hint.TLabel"
                ).pack(pady=40, padx=20)
                return
            if not result["previews"]:
                ttk.Label(dialog, text="No cover images found for this game.", style="Hint.TLabel").pack(pady=40, padx=20)
                return

            shown, total = len(result["previews"]), result["total"]
            header = f"{shown} of {total} available" if total > shown else f"{shown} available"
            self._add_header(dialog, f"Choose a cover image ({header})")

            body = self._build_scrollable_body(dialog)
            current_grid_id = profile.steamgriddb_grid_id
            default_grid_id = result["previews"][0][0]["id"]  # first = top-voted = what pick_best() would choose
            columns = 4

            def make_choose(grid_id, is_default):
                """Selecting the default tile clears the override entirely, rather than redundantly pinning the id it'd already resolve to."""
                def choose():
                    """The actual button `command` - captures `grid_id`/`is_default` from the enclosing make_choose() call."""
                    self._set_profile_steamgriddb_grid_id(self.current_profile_id, None if is_default else grid_id)
                    dialog.destroy()
                return choose

            for index, (grid, image_bytes) in enumerate(result["previews"]):
                try:
                    photo = self._photoimage_from_bytes(image_bytes, GRID_PICKER_THUMBNAIL_WIDTH)
                except tk.TclError:
                    continue

                is_default = grid["id"] == default_grid_id
                is_current = (current_grid_id is None and is_default) or (current_grid_id == grid["id"])
                cell = ttk.Frame(body, padding=4, relief="solid" if is_current else "flat", borderwidth=2 if is_current else 0)
                cell.grid(row=index // columns, column=index % columns, padx=4, pady=4)

                btn = ttk.Button(cell, image=photo, command=make_choose(grid["id"], is_default))
                btn.image = photo  # keep a reference - PhotoImage is garbage-collected otherwise
                btn.pack()
                caption = "Default" if is_default else (grid.get("style") or "")
                ttk.Label(cell, text=caption, style="Hint.TLabel").pack()

            footer = ttk.Frame(dialog)
            footer.pack(fill="x", padx=10, pady=(0, 10))
            ttk.Button(
                footer,
                text="Use default (top-voted)",
                command=lambda: (self._set_profile_steamgriddb_grid_id(self.current_profile_id, None), dialog.destroy()),
            ).pack(side="left")
            ttk.Button(footer, text="Cancel", command=dialog.destroy).pack(side="right")

        threading.Thread(target=worker, daemon=True).start()

    def _open_binding_dialog(self, existing=None):
        """
        Modal add/edit dialog for one binding. Pass `existing` (an editable
        binding dict, per _binding_to_editable) to pre-fill and edit it in
        place, or omit it to create a new one with sensible defaults.

        Blocks (via dialog.wait_window()) until the dialog closes, then
        returns the new/edited editable-binding dict on "OK", or None if
        cancelled or closed without saving. Validation (non-empty id/keys,
        valid VibeRange/DurationRange, no 'scroll' on a continuous binding)
        happens inside on_ok() and re-prompts via messagebox rather than
        closing the dialog, so a mistake doesn't lose everything typed in.
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("Binding")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        def row_entry(r, label, var, width=32):
            """Place a label+entry pair on grid row `r` - just a shorthand to avoid repeating this twice per field below."""
            ttk.Label(dialog, text=label).grid(row=r, column=0, sticky="w", padx=6, pady=3)
            ttk.Entry(dialog, textvariable=var, width=width).grid(row=r, column=1, sticky="w", padx=6, pady=3)

        id_var = tk.StringVar(value=existing["id"] if existing else "")
        keys_var = tk.StringVar(value=",".join(existing["keys"]) if existing else "")
        mode_var = tk.StringVar(value=existing["mode"] if existing else "pulse")
        devices_var = tk.StringVar(value=",".join(existing["devices"]) if existing else "all")
        vibe_low_var = tk.StringVar(value=str(existing["vibe_low"]) if existing else "0.30")
        vibe_high_var = tk.StringVar(value=str(existing["vibe_high"]) if existing else "0.60")
        has_duration = bool(existing and existing["duration_low"] is not None)
        duration_low_var = tk.StringVar(value=str(existing["duration_low"]) if has_duration else "0.15")
        duration_high_var = tk.StringVar(value=str(existing["duration_high"]) if has_duration else "0.25")
        enabled_var = tk.BooleanVar(value=existing["enabled"] if existing else True)

        row_entry(0, "Binding id:", id_var)
        row_entry(1, "Keys (comma-separated):", keys_var)
        ttk.Label(dialog, text="Mode:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        ttk.Combobox(dialog, textvariable=mode_var, values=["continuous", "pulse"], state="readonly", width=29).grid(
            row=2, column=1, sticky="w", padx=6, pady=3
        )
        row_entry(3, "Devices (nicknames or 'all'):", devices_var)
        row_entry(4, "Vibe % low (0-100):", vibe_low_var, width=10)
        row_entry(5, "Vibe % high (0-100):", vibe_high_var, width=10)
        row_entry(6, "Duration sec low (pulse only):", duration_low_var, width=10)
        row_entry(7, "Duration sec high (pulse only):", duration_high_var, width=10)
        ttk.Checkbutton(dialog, text="Enabled", variable=enabled_var).grid(row=8, column=1, sticky="w", padx=6, pady=3)

        result = {}  # populated by on_ok() below; stays empty if the dialog is cancelled

        def on_ok():
            """
            "OK" button handler: validate every field and, if all is well,
            populate the enclosing `result` dict and close the dialog. On
            any ValueError (bad number, empty field, invalid range, ...)
            shows the message in a messagebox and leaves the dialog open
            with whatever was typed intact, so the user can just fix the
            one bad field rather than starting over.
            """
            try:
                bid = id_var.get().strip()
                if not bid:
                    raise ValueError("binding id is required")
                keys = [k.strip().lower() for k in keys_var.get().split(",") if k.strip()]
                if not keys:
                    raise ValueError("at least one key is required")
                mode = mode_var.get()
                devices_raw = [d.strip().lower() for d in devices_var.get().split(",") if d.strip()] or ["all"]
                vibe_low = float(vibe_low_var.get()) / 100.0
                vibe_high = float(vibe_high_var.get()) / 100.0
                VibeRange(vibe_low, vibe_high)
                duration_low = duration_high = None
                if mode == "pulse":
                    duration_low = float(duration_low_var.get())
                    duration_high = float(duration_high_var.get())
                    DurationRange(duration_low, duration_high)
                elif "scroll" in keys:
                    raise ValueError("continuous bindings can't include 'scroll' (it has no held state)")
                result.update(
                    {
                        "id": bid, "keys": keys, "mode": mode, "enabled": enabled_var.get(), "devices": devices_raw,
                        "vibe_low": vibe_low, "vibe_high": vibe_high,
                        "duration_low": duration_low, "duration_high": duration_high,
                    }
                )
                dialog.destroy()
            except ValueError as e:
                messagebox.showerror("Binding", str(e), parent=dialog)

        btns = ttk.Frame(dialog)
        btns.grid(row=9, column=0, columnspan=2, pady=8)
        ttk.Button(btns, text="OK", command=on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)

        dialog.wait_window()
        return result or None

    def _on_add_binding(self):
        """"Add binding..." button handler: opens a blank dialog and appends the result to the working copy, if any."""
        result = self._open_binding_dialog()
        if not result:
            return
        if any(b["id"] == result["id"] for b in self.current_bindings):
            messagebox.showerror("Add binding", f"id '{result['id']}' already exists in this profile.")
            return
        self.current_bindings.append(result)
        self._refresh_bindings_tree()

    def _on_edit_binding(self):
        """
        "Edit binding..." button handler. The bindings Treeview's row iids
        are just str(index) into self.current_bindings (see
        _refresh_bindings_tree), so the selected row maps directly back to
        the binding dict to edit.
        """
        selection = self.bindings_tree.selection()
        if not selection:
            messagebox.showinfo("Edit binding", "Select a binding first.")
            return
        idx = int(selection[0])
        result = self._open_binding_dialog(existing=self.current_bindings[idx])
        if result:
            self.current_bindings[idx] = result
            self._refresh_bindings_tree()

    def _on_remove_binding(self):
        """"Remove binding" button handler: deletes the selected row from the working copy (see _on_edit_binding on row iids)."""
        selection = self.bindings_tree.selection()
        if not selection:
            messagebox.showinfo("Remove binding", "Select a binding first.")
            return
        del self.current_bindings[int(selection[0])]
        self._refresh_bindings_tree()

    # =============================================================== Test tab
    # Two independent ways to check things work without needing the actual
    # game running: drive a channel directly (bypassing profiles/keybinds
    # entirely), or simulate a profile's keybinds being pressed (exercising
    # the same code path real input does, via HapticsController.test_pulse()
    # and a "pinned" active profile - see test_profile_override in src/tighc.py).
    def _build_test_tab(self):
        """
        Build the Test tab: a profile picker + pin toggle + "Stop all
        testing" up top, and two side-by-side panels below it (manual
        channel control, simulate keybinds - see the module comment above
        for what each does). The two panels' contents are rebuilt
        dynamically by _refresh_test_channels()/_refresh_test_bindings(),
        not laid out statically here, since they depend on how many
        channels/bindings currently exist.
        """
        frame = self.test_tab
        self._add_header(frame, "Test")

        top = ttk.Frame(frame)
        top.pack(fill="x", padx=PADX, pady=PADY)
        ttk.Label(top, text="Profile to test:").pack(side="left")
        self.test_profile_combo = ttk.Combobox(top, state="readonly", width=22)
        self.test_profile_combo.pack(side="left", padx=6)
        self.test_profile_combo.bind(
            "<<ComboboxSelected>>", lambda e: (self._refresh_test_bindings(), self._refresh_test_artwork())
        )
        self.test_pin_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top, text="Pin as active profile (ignores the real focused window)",
            variable=self.test_pin_var, command=self._on_toggle_test_pin,
        ).pack(side="left", padx=12)
        self.test_artwork_label = ttk.Label(top, text="")
        self.test_artwork_label.pack(side="left", padx=12)
        self._test_artwork_image = None  # keep a reference - PhotoImage is garbage-collected otherwise
        ttk.Button(top, text="Stop all testing", command=self._on_stop_all_test).pack(side="right")

        panes = ttk.Panedwindow(frame, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=PADX, pady=(0, PADY))

        channels_frame = ttk.LabelFrame(panes, text="Manual channel control")
        bindings_frame = ttk.LabelFrame(panes, text="Simulate keybinds")
        panes.add(channels_frame, weight=1)
        panes.add(bindings_frame, weight=1)

        ttk.Label(
            channels_frame, text="Drives a channel directly, no profile needed.", style="Hint.TLabel"
        ).pack(anchor="w", padx=6, pady=(6, 0))
        self.test_channels_container = ttk.Frame(channels_frame)
        self.test_channels_container.pack(fill="both", expand=True, padx=6, pady=6)

        ttk.Label(
            bindings_frame,
            text="\"Hold\" needs the engine Started (Run tab) and this profile pinned above to take effect.",
            style="Hint.TLabel", wraplength=320, justify="left",
        ).pack(anchor="w", padx=6, pady=(6, 0))
        self.test_bindings_container = ttk.Frame(bindings_frame)
        self.test_bindings_container.pack(fill="both", expand=True, padx=6, pady=6)

    def _refresh_test_artwork(self):
        """Same idea as _refresh_profile_artwork, but for the Test tab's small toolbar-row thumbnail next to its own profile picker."""
        profile = self.controller.profiles.get(self._profile_id_for_display(self.test_profile_combo.get()))
        if not profile:
            self.test_artwork_label.config(image="", text="")
            return
        requested_profile_id = profile.id

        def on_done(path):
            """Runs on the Tk main thread once the fetch resolves; see the sibling on_done in _refresh_profile_artwork for the pattern."""
            if self._profile_id_for_display(self.test_profile_combo.get()) != requested_profile_id:
                return
            if path is None:
                self.test_artwork_label.config(image="", text="")
                return
            self._test_artwork_image = self._load_thumbnail_image(path)
            self.test_artwork_label.config(image=self._test_artwork_image, text="")

        self._fetch_artwork_async(profile.id, profile.name, profile.steamgriddb_id, profile.steamgriddb_grid_id, on_done)

    # --- manual per-channel control -----------------------------------
    def _refresh_test_channels(self):
        """
        Rebuild the manual-control panel with one row per connected
        channel (slider + %, Hold checkbox, Pulse button), tearing down and
        recreating every widget rather than diffing - simple, and cheap
        enough since this only runs after connect/rescan/rename, not on a
        timer. Populates self._test_channel_widgets so other handlers
        (_on_stop_all_test in particular) can find each row's Vars again.
        """
        for child in self.test_channels_container.winfo_children():
            child.destroy()
        self._test_channel_widgets = {}

        if not self.controller.channels:
            ttk.Label(self.test_channels_container, text="(no channels - connect on the Devices tab)").pack(anchor="w")
            return

        for nickname, _channel in sorted(self.controller.channels.items()):
            row = ttk.Frame(self.test_channels_container)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=nickname, width=20).pack(side="left")

            level_var = tk.IntVar(value=0)
            pct_label = ttk.Label(row, text="0%", width=5)
            hold_var = tk.BooleanVar(value=False)

            scale = ttk.Scale(row, from_=0, to=100, orient="horizontal", variable=level_var, length=130)
            scale.pack(side="left", padx=4)
            pct_label.pack(side="left")
            # Dragging the slider while "Hold" is on updates the live level
            # immediately, so you can feel out an intensity in real time.
            level_var.trace_add("write", lambda *_a, n=nickname, lv=level_var, hv=hold_var, pl=pct_label: self._on_test_level_changed(n, lv, hv, pl))

            ttk.Checkbutton(
                row, text="Hold", variable=hold_var,
                command=lambda n=nickname, lv=level_var, hv=hold_var: self._on_toggle_channel_hold(n, lv, hv),
            ).pack(side="left", padx=4)
            ttk.Button(
                row, text="Pulse", command=lambda n=nickname, lv=level_var: self._on_test_channel_pulse(n, lv)
            ).pack(side="left")

            self._test_channel_widgets[nickname] = {"level_var": level_var, "hold_var": hold_var}

    def _on_test_level_changed(self, nickname, level_var, hold_var, pct_label):
        """Slider `write`-trace callback: update the "N%" label live, and push the new level if this channel is currently held."""
        pct_label.config(text=f"{level_var.get()}%")
        if hold_var.get():
            self.bridge.submit(self.controller.set_test_level(nickname, level_var.get() / 100.0))

    def _on_toggle_channel_hold(self, nickname, level_var, hold_var):
        """Per-channel "Hold" checkbox handler: engage or release a manual level override at the slider's current position."""
        if hold_var.get():
            self.bridge.submit(self.controller.set_test_level(nickname, level_var.get() / 100.0))
        else:
            self.bridge.submit(self.controller.clear_test_level(nickname))

    def _on_test_channel_pulse(self, nickname, level_var):
        """Per-channel "Pulse" button handler: fire a fixed-duration test pulse at the slider's current level, just this one channel."""
        level = level_var.get() / 100.0
        vibe = VibeRange(level, level)
        self.bridge.submit(self.controller.test_pulse(vibe, 0.6, frozenset({nickname})))

    # --- simulated keybinds ---------------------------------------------
    def _refresh_test_bindings(self):
        """
        Rebuild the "simulate keybinds" panel for whichever profile is
        selected in test_profile_combo: a "Hold" checkbox per enabled
        continuous binding, a "Trigger" button per enabled pulse binding.
        Disabled bindings are skipped - there's nothing to test on a
        binding that won't fire anyway. Called whenever the profile
        selector changes, or the profile list itself changes.
        """
        for child in self.test_bindings_container.winfo_children():
            child.destroy()
        self._test_binding_widgets = {}

        profile_id = self._profile_id_for_display(self.test_profile_combo.get())
        profile = self.controller.profiles.get(profile_id)
        if not profile:
            ttk.Label(self.test_bindings_container, text="(no profile selected)").pack(anchor="w")
            return

        # Continuous bindings need their resolved token set (movement's
        # {"w","a","s","d"}, etc.) so "Hold" can mark all of them pressed at
        # once - pull that from profile.continuous rather than re-deriving
        # it from the raw binding dict.
        continuous_tokens = {b.id: b.tokens for b in profile.continuous}

        for binding in profile.bindings:
            if not binding["enabled"]:
                continue
            row = ttk.Frame(self.test_bindings_container)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{binding['id']} ({binding['mode']})", width=24).pack(side="left")

            if binding["mode"] == "continuous":
                tokens = continuous_tokens.get(binding["id"], frozenset(binding["keys"]))
                hold_var = tk.BooleanVar(value=False)
                ttk.Checkbutton(
                    row, text="Hold", variable=hold_var,
                    command=lambda t=tokens, v=hold_var: self._on_toggle_binding_hold(t, v),
                ).pack(side="left")
                self._test_binding_widgets[binding["id"]] = (hold_var, tokens)
            else:
                ttk.Button(row, text="Trigger", command=lambda b=binding: self._on_trigger_binding(b)).pack(side="left")

    def _on_toggle_binding_hold(self, tokens, hold_var):
        """
        Continuous-binding "Hold" checkbox handler: add or remove this
        binding's key tokens from the shared InputState.pressed_keys set.
        Simulates the key(s) being physically held - background_loop()
        picks this up exactly like real pynput input would, as long as
        this profile is pinned active (see _on_toggle_test_pin below); if
        it isn't pinned (or the engine isn't Started), toggling this has no
        visible effect since nothing is reading pressed_keys against this
        profile right now.
        """
        if hold_var.get():
            self.controller.input_state.pressed_keys |= set(tokens)
        else:
            self.controller.input_state.pressed_keys -= set(tokens)

    def _on_trigger_binding(self, binding):
        """
        Pulse-binding "Trigger" button handler: fire exactly the pulse a
        real press of this binding would, using its own vibe/duration
        range and devices target. Falls back to a fixed 0.3s if the
        binding somehow has no duration range (shouldn't happen for a
        valid pulse binding, but keeps this from erroring on an edge case).
        Uses test_pulse(), not pulse(), so this works even with no profile
        pinned/active.
        """
        duration = binding["duration"].roll() if binding["duration"] else 0.3
        self.bridge.submit(self.controller.test_pulse(binding["vibe"], duration, binding["devices"]))

    def _on_toggle_test_pin(self):
        """
        "Pin as active profile" checkbox handler: set or clear
        controller.test_profile_override to force (or stop forcing) the
        selected profile active regardless of the real focused window. If
        no profile is selected, silently un-checks itself and prompts
        instead of pinning nothing.
        """
        profile = self.controller.profiles.get(self._profile_id_for_display(self.test_profile_combo.get()))
        if self.test_pin_var.get():
            if not profile:
                messagebox.showinfo("Test mode", "Select a profile to pin first.")
                self.test_pin_var.set(False)
                return
            self.controller.test_profile_override = profile
            self._enqueue_log(f"Pinned '{profile.name}' as the active profile for testing.")
        else:
            self.controller.test_profile_override = None
            self._enqueue_log("Unpinned test profile - the active profile now follows the focused window again.")

    def _on_stop_all_test(self):
        """
        "Stop all testing" button handler: releases every manual channel
        hold and every simulated keybind hold in one go, for bailing out
        quickly (does not touch the profile pin - see _on_toggle_test_pin
        for that). Note this does not zero the channels itself; releasing a
        hold just lets background_loop() (or the idle branch, if nothing's
        active) take back over on its own next tick.
        """
        # Release every manual channel hold...
        for nickname, widgets in self._test_channel_widgets.items():
            if widgets["hold_var"].get():
                widgets["hold_var"].set(False)
                self.bridge.submit(self.controller.clear_test_level(nickname))
        # ...and release every simulated keybind hold. Setting a BooleanVar
        # programmatically doesn't fire the Checkbutton's command, so the
        # pressed_keys cleanup has to happen here explicitly too.
        for hold_var, tokens in self._test_binding_widgets.values():
            if hold_var.get():
                hold_var.set(False)
                self.controller.input_state.pressed_keys -= set(tokens)
        self._enqueue_log("Test mode: released all manual holds.")

    # =============================================================== Settings tab
    def _build_settings_tab(self):
        """
        Build the Settings tab: one form field/checkbox per
        haptics_config.json key, pre-filled from load_haptics_config().
        Saving calls tighc.apply_haptics_config(), which takes effect
        immediately (see _on_save_settings) - the WebSocket URL is the one
        exception, needing a manual reconnect rather than an app restart.
        """
        frame = self.settings_tab
        self._add_header(frame, "Global settings")
        # Scrollable, not a bare grid-managed frame - between the global
        # settings and the cover art section below, this tab is taller than
        # the window's minsize allows, and a plain frame would just clip the
        # cover art fields with no way to reach them (see
        # _build_scrollable_body).
        body = self._build_scrollable_body(frame)

        cfg = load_haptics_config()
        pad = {"padx": 6, "pady": 3}
        self.cfg_vars = {}
        row = 0

        def add(label, key, default):
            """Place one label+entry settings row and remember its StringVar in self.cfg_vars[key] for _on_save_settings."""
            nonlocal row
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", **pad)
            var = tk.StringVar(value=str(default))
            ttk.Entry(body, textvariable=var, width=20).grid(row=row, column=1, sticky="w", **pad)
            self.cfg_vars[key] = var
            row += 1

        add("Intiface WS URL:", "intiface_ws", cfg["intiface_ws"])

        self.master_enabled_var = tk.BooleanVar(value=cfg["master"]["enabled"])
        ttk.Checkbutton(body, text="Master random override enabled", variable=self.master_enabled_var).grid(
            row=row, column=0, columnspan=2, sticky="w", **pad
        )
        row += 1
        add("Master range % low:", "master_low", cfg["master"]["range"][0] * 100)
        add("Master range % high:", "master_high", cfg["master"]["range"][1] * 100)

        self.smoothing_enabled_var = tk.BooleanVar(value=cfg["smoothing"]["enabled"])
        ttk.Checkbutton(body, text="Level smoothing enabled", variable=self.smoothing_enabled_var).grid(
            row=row, column=0, columnspan=2, sticky="w", **pad
        )
        row += 1
        add("Smoothing factor (0-1):", "smoothing_factor", cfg["smoothing"]["factor"])

        self.panic_enabled_var = tk.BooleanVar(value=cfg["panic_key"]["enabled"])
        ttk.Checkbutton(body, text="Panic key enabled", variable=self.panic_enabled_var).grid(
            row=row, column=0, columnspan=2, sticky="w", **pad
        )
        row += 1
        add("Panic key:", "panic_key", cfg["panic_key"]["key"])
        add("Panic hold duration (sec):", "panic_hold", cfg["panic_key"]["hold_duration"])

        self.reconnect_enabled_var = tk.BooleanVar(value=cfg["auto_reconnect"]["enabled"])
        ttk.Checkbutton(body, text="Auto-reconnect enabled", variable=self.reconnect_enabled_var).grid(
            row=row, column=0, columnspan=2, sticky="w", **pad
        )
        row += 1
        add("Reconnect cooldown (sec):", "reconnect_cooldown", cfg["auto_reconnect"]["cooldown"])
        add("Reconnect failure threshold:", "reconnect_threshold", cfg["auto_reconnect"]["failure_threshold"])
        add("Background tick (sec):", "background_tick", cfg["timing"]["background_tick"])

        ttk.Button(body, text="Save settings", command=self._on_save_settings).grid(row=row, column=0, sticky="w", **pad)
        row += 1
        ttk.Label(
            body,
            text="Takes effect immediately - no restart needed. Exception: a WebSocket URL change needs "
            "\"Connect + Scan\" (or Stop then Start) to actually reconnect to it.",
            style="Hint.TLabel",
        ).grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        row += 1

        ttk.Separator(body, orient="horizontal").grid(row=row, column=0, columnspan=2, sticky="ew", pady=12)
        row += 1
        ttk.Label(body, text="Cover art (SteamGridDB)", style="Header.TLabel").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        row += 1

        sgdb_cfg = tighc.load_steamgriddb_config()
        self.sgdb_enabled_var = tk.BooleanVar(value=sgdb_cfg.get("enabled", False))
        ttk.Checkbutton(body, text="Show profile cover art (fetched from SteamGridDB)", variable=self.sgdb_enabled_var).grid(
            row=row, column=0, columnspan=2, sticky="w", **pad
        )
        row += 1
        ttk.Label(body, text="API key:").grid(row=row, column=0, sticky="w", **pad)
        self.sgdb_api_key_var = tk.StringVar(value=sgdb_cfg.get("api_key", ""))
        ttk.Entry(body, textvariable=self.sgdb_api_key_var, width=40, show="*").grid(
            row=row, column=1, sticky="w", **pad
        )
        row += 1
        key_link = ttk.Label(
            body, text="Get a free key at steamgriddb.com/profile/preferences", foreground="#2563eb", cursor="hand2"
        )
        key_link.grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        key_link.bind("<Button-1>", lambda _e: webbrowser.open("https://www.steamgriddb.com/profile/preferences"))
        row += 1
        ttk.Button(body, text="Save cover art settings", command=self._on_save_steamgriddb_settings).grid(
            row=row, column=0, sticky="w", **pad
        )
        row += 1
        ttk.Label(
            body,
            text="This takes effect immediately (no restart needed) - artwork is fetched per profile on the Profiles/Test tabs.",
            style="Hint.TLabel",
        ).grid(row=row, column=0, columnspan=2, sticky="w", **pad)

    def _on_save_steamgriddb_settings(self):
        """"Save cover art settings" button handler: persist to steamgriddb_config.json and immediately try to (re)load artwork."""
        tighc.save_steamgriddb_config({"enabled": self.sgdb_enabled_var.get(), "api_key": self.sgdb_api_key_var.get().strip()})
        self._enqueue_log("Cover art settings saved.")
        self._refresh_profile_artwork()
        self._refresh_test_artwork()

    def _on_save_settings(self):
        """
        "Save settings" button handler: assemble a full haptics_config.json-
        shaped dict from every form field and hand it to
        tighc.apply_haptics_config(), which persists it and updates the
        engine's live settings in place - no restart needed. Validates the
        master-range values form a valid VibeRange first; unlike profile
        saving, there's no separate read-back-through-the-real-parser step
        here (load_haptics_config() doesn't validate ranges the way
        _load_profile does), so this is what catches a swapped/
        out-of-bounds master range before it's applied.
        """
        try:
            cfg = {
                "intiface_ws": self.cfg_vars["intiface_ws"].get().strip(),
                "master": {
                    "enabled": self.master_enabled_var.get(),
                    "range": [float(self.cfg_vars["master_low"].get()) / 100.0, float(self.cfg_vars["master_high"].get()) / 100.0],
                },
                "smoothing": {
                    "enabled": self.smoothing_enabled_var.get(),
                    "factor": float(self.cfg_vars["smoothing_factor"].get()),
                },
                "panic_key": {
                    "enabled": self.panic_enabled_var.get(),
                    "key": self.cfg_vars["panic_key"].get().strip(),
                    "hold_duration": float(self.cfg_vars["panic_hold"].get()),
                },
                "auto_reconnect": {
                    "enabled": self.reconnect_enabled_var.get(),
                    "cooldown": float(self.cfg_vars["reconnect_cooldown"].get()),
                    "failure_threshold": int(self.cfg_vars["reconnect_threshold"].get()),
                },
                "timing": {"background_tick": float(self.cfg_vars["background_tick"].get())},
            }
            VibeRange(*cfg["master"]["range"])
        except ValueError as e:
            messagebox.showerror("Settings", f"Invalid value: {e}")
            return

        ws_url_changed = cfg["intiface_ws"] != self.controller.ws_url
        tighc.apply_haptics_config(cfg)
        # apply_haptics_config() can't itself force an already-open
        # connection to move to a new URL - update the live controller's
        # ws_url too, so at least the *next* connect (a manual "Connect +
        # Scan"/Start, or a future auto-reconnect) picks up the change,
        # rather than silently continuing to use the old one until restart.
        self.controller.ws_url = cfg["intiface_ws"]
        self.ws_url_var.set(cfg["intiface_ws"])

        self._enqueue_log("Settings saved and applied immediately.")
        if ws_url_changed:
            self._enqueue_log("Intiface WebSocket URL changed - click \"Connect + Scan\" (or Stop then Start) to use it.")

    # =============================================================== Run tab
    def _build_run_tab(self):
        """Build the Run tab: Start/Stop buttons, a live per-channel status readout, and the log pane most self.log() calls feed."""
        frame = self.run_tab
        self._add_header(frame, "Run")

        btns = ttk.Frame(frame)
        btns.pack(fill="x", padx=PADX, pady=PADY)
        self.start_btn = ttk.Button(btns, text="Start", command=self._on_start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.status_var = tk.StringVar(value="Stopped")
        ttk.Label(btns, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).pack(side="left", padx=12)

        levels_frame = ttk.LabelFrame(frame, text="Live status")
        levels_frame.pack(fill="x", padx=PADX, pady=(0, PADY))
        self.levels_label = ttk.Label(levels_frame, text="(not connected)", justify="left", font=MONOSPACE_FONT)
        self.levels_label.pack(anchor="w", padx=6, pady=4)

        log_frame = ttk.LabelFrame(frame, text="Log")
        log_frame.pack(fill="both", expand=True, padx=PADX, pady=(0, PADY))
        self.log_text = scrolledtext.ScrolledText(log_frame, height=16, state="disabled", wrap="word", font=MONOSPACE_FONT)
        self.log_text.pack(fill="both", expand=True)

    def _on_start(self):
        """
        "Start" button handler. Connects first if this is the very first
        Start (no client yet) - so clicking Start alone is enough without
        needing to visit the Devices tab first - then calls
        controller.start_engine(), which is what actually spins up
        background_loop() and the pynput listeners. Both steps have to run
        on the bridge's event loop (start_engine() calls
        asyncio.create_task(), which requires one), hence the local
        `_start()` coroutine wrapping both instead of calling them directly.
        """
        self.start_btn.config(state="disabled")
        if not self.controller.client:
            self.connection_status_var.set(f"Connecting to {self.controller.ws_url} ...")

        async def _start():
            """Runs on the bridge's loop: connect if needed, then start the engine. Returns whether it's now actually running."""
            if not self.controller.client:
                if not await self.controller.connect():
                    return False
            self.controller.start_engine()
            return True

        fut = self.bridge.submit(_start())
        fut.add_done_callback(lambda f: self.root.after(0, self._after_start, f))

    def _after_start(self, fut):
        """Runs on the Tk main thread once _start() (see _on_start) finishes; flips button states based on success."""
        try:
            ok = fut.result()
        except Exception as e:
            ok = False
            self._enqueue_log(f"Start failed: {e}")
        self._refresh_channels_tree()
        self.disconnect_btn.config(state="normal" if self.controller.client else "disabled")
        if ok:
            self.stop_btn.config(state="normal")
            self.status_var.set("Running")
        else:
            self.start_btn.config(state="normal")

    def _on_stop(self):
        """"Stop" button handler: submits controller.stop_engine() (cancels background_loop, stops listeners, zeroes channels)."""
        self.stop_btn.config(state="disabled")
        fut = self.bridge.submit(self.controller.stop_engine())
        fut.add_done_callback(lambda f: self.root.after(0, self._after_stop, f))

    def _after_stop(self, fut):
        """Runs on the Tk main thread once stop_engine() finishes; re-enables Start regardless of whether it errored."""
        try:
            fut.result()
        except Exception as e:
            self._enqueue_log(f"Stop error: {e}")
        self.start_btn.config(state="normal")
        self.status_var.set("Stopped")

    # =============================================================== About tab
    def _build_about_tab(self):
        """Build the About tab: project blurb, age notice, versioning note, a clickable repo link, and the changelog viewer."""
        frame = self.about_tab
        self._add_header(frame, PROJECT_NAME)

        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True, padx=PADX, pady=PADY)

        ttk.Label(body, text=f"{PROJECT_SHORT_NAME} - version {__version__}", font=("Segoe UI", 10, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            body,
            text=(
                "Drives a Buttplug/Intiface haptic device from keyboard/mouse input in any game, "
                "via configurable per-game profiles, continuous/pulse keybinds, and per-motor device targeting."
            ),
            wraplength=760, justify="left",
        ).pack(anchor="w", pady=(4, 10))

        ttk.Label(
            body,
            text="Intended for use only by adults aged 18 or older.",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        ttk.Label(
            body,
            text="Versioning: Semantic Versioning (semver.org) - MAJOR.MINOR.PATCH.",
            style="Hint.TLabel",
        ).pack(anchor="w")

        contact = ttk.Frame(body)
        contact.pack(anchor="w", pady=(2, 10))
        ttk.Label(contact, text="Repository: ").pack(side="left")
        # ttk.Label has no built-in hyperlink widget, so this fakes one: a
        # colored, hand-cursor label that opens the URL in the OS browser.
        repo_link = ttk.Label(contact, text=REPO_URL, foreground="#2563eb", cursor="hand2")
        repo_link.pack(side="left")
        repo_link.bind("<Button-1>", lambda _e: webbrowser.open(REPO_URL))

        changelog_header = ttk.Frame(body)
        changelog_header.pack(fill="x", pady=(4, 4))
        ttk.Label(changelog_header, text="Changelog", style="Header.TLabel").pack(side="left")
        ttk.Button(changelog_header, text="Reload", command=self._load_changelog).pack(side="right")

        changelog_frame = ttk.Frame(body)
        changelog_frame.pack(fill="both", expand=True)
        self.changelog_text = scrolledtext.ScrolledText(changelog_frame, wrap="word", font=MONOSPACE_FONT)
        self.changelog_text.pack(fill="both", expand=True)
        self._load_changelog()

    def _load_changelog(self):
        """
        (Re)load CHANGELOG.md's raw text into the changelog viewer. Also
        wired to the "Reload" button, for after a hand edit to the file
        without restarting the app. The Text widget is briefly set to
        "normal" to allow the delete+insert, then back to "disabled" so the
        user can select/copy text but not accidentally edit the display.
        """
        try:
            content = CHANGELOG_PATH.read_text(encoding="utf-8")
        except OSError as e:
            content = f"(Could not read {CHANGELOG_PATH.name}: {e})"
        self.changelog_text.config(state="normal")
        self.changelog_text.delete("1.0", "end")
        self.changelog_text.insert("1.0", content)
        self.changelog_text.config(state="disabled")

    # =============================================================== shared plumbing
    def _enqueue_log(self, message):
        """
        The `log_fn` passed to HapticsController (and called directly by
        the GUI itself in a few places). Safe to call from any thread -
        queue.Queue is thread-safe - since controller log calls can
        originate from the bridge's asyncio thread (background_loop, pynput
        callbacks scheduled onto it) as well as the Tk main thread. Actually
        displaying the message happens later, on the Tk thread, via
        _poll_log_queue().
        """
        self.log_queue.put(str(message))

    def _poll_log_queue(self):
        """
        Runs every 150ms on the Tk main thread (self-rescheduling via
        root.after): drains every message _enqueue_log() has queued since
        the last poll and appends them to the Run tab's log pane. This
        poll-a-queue pattern is the only safe way to get text from the
        background asyncio thread onto a Tkinter widget, which can only be
        touched from the main thread.
        """
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_text.config(state="normal")
                self.log_text.insert("end", message + "\n")
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        self._log_poll_id = self.root.after(150, self._poll_log_queue)

    def _poll_status(self):
        """
        Runs every 400ms on the Tk main thread (self-rescheduling, same
        pattern as _poll_log_queue): refreshes the Run tab's "Live status"
        label with the current active profile and every channel's last-sent
        level, and the top bar's always-visible connection indicator (see
        _build_ui). Reads controller/channel attributes directly rather than
        through a queue - safe enough for a display-only read of simple
        values (floats, a profile reference) under the GIL, unlike posting
        a mutating command the other direction.
        """
        if self.controller.channels:
            active = self.controller.active_profile.name if self.controller.active_profile else "(none - idle)"
            lines = [f"Active profile: {active}"]
            for nickname, channel in sorted(self.controller.channels.items()):
                lines.append(f"  {nickname}: {channel.last_level * 100:.0f}%")
            self.levels_label.config(text="\n".join(lines))
            self.connection_status_var.set(f"Connected - {len(self.controller.channels)} channel(s)")
        else:
            self.levels_label.config(text="(not connected)")
            self.connection_status_var.set(
                "Connected - no devices found" if self.controller.client else "Not connected"
            )
        self._status_poll_id = self.root.after(400, self._poll_status)

    def _on_close(self):
        """
        Window-close handler (bound to WM_DELETE_WINDOW): stop the pollers,
        submit a full controller.shutdown() (stop engine + disconnect) and
        wait up to 5s for it, then destroy the window. The 5s wait blocks
        the Tk thread briefly, which is fine for a one-time shutdown but
        would be the wrong pattern anywhere else in this app.
        """
        # Cancel the recurring pollers first - otherwise a poller already
        # queued via `after` can fire against a destroyed root and print a
        # harmless but noisy "invalid command name" error on exit.
        self.root.after_cancel(self._log_poll_id)
        self.root.after_cancel(self._status_poll_id)
        fut = self.bridge.submit(self.controller.shutdown())
        try:
            fut.result(timeout=5)
        except Exception:
            pass
        self.root.destroy()


def _show_age_gate(root) -> bool:
    """Blocking 18+ acknowledgment shown before the main window - self-attestation, not real ID verification."""
    dialog = tk.Toplevel(root)
    dialog.title("Age Verification")
    dialog.resizable(False, False)
    # Deliberately NOT dialog.transient(root): root is still withdraw()n at
    # this point, and marking a Toplevel transient to a withdrawn/unmapped
    # master is a known Tk/Windows-window-manager quirk that can leave the
    # transient window itself stuck unmapped (created, event loop running,
    # but never actually painted) - grab_set() alone is enough to keep this
    # modal without that dependency.
    dialog.grab_set()

    # A one-item dict rather than a plain bool so confirm()/decline() (each
    # a separate nested function) can mutate it without a `nonlocal`
    # declaration per function - _show_age_gate reads it back after
    # wait_window() returns.
    confirmed = {"value": False}

    def confirm():
        """"I am 18 or older" button handler: record acceptance and close the dialog."""
        confirmed["value"] = True
        dialog.destroy()

    def decline():
        """"Exit" button handler (also the window-close [X] via WM_DELETE_WINDOW below): record refusal and close."""
        confirmed["value"] = False
        dialog.destroy()

    dialog.protocol("WM_DELETE_WINDOW", decline)

    body = ttk.Frame(dialog, padding=20)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text=f"{PROJECT_NAME} ({PROJECT_SHORT_NAME})", font=("Segoe UI", 11, "bold")).pack(pady=(0, 8))
    ttk.Label(
        body,
        text=(
            "This software connects to and controls adult haptic/sex toy devices\n"
            "based on your keyboard and mouse input while gaming.\n\n"
            "It is intended for use only by adults aged 18 or older."
        ),
        justify="center",
    ).pack(pady=(0, 16))

    btns = ttk.Frame(body)
    btns.pack()
    ttk.Button(btns, text="I am 18 or older - Continue", command=confirm).pack(side="left", padx=6)
    ttk.Button(btns, text="Exit", command=decline).pack(side="left", padx=6)

    # Center the dialog over the (currently hidden) root window.
    dialog.update_idletasks()
    x = root.winfo_screenwidth() // 2 - dialog.winfo_width() // 2
    y = root.winfo_screenheight() // 2 - dialog.winfo_height() // 2
    dialog.geometry(f"+{x}+{y}")

    # Force this to the foreground. Windows doesn't always let a process
    # launched from a terminal/IDE steal focus on its own, so without this a
    # freshly-created Toplevel can end up parked behind the launching
    # terminal window - technically open, but invisible until you manually
    # alt-tab to it. Toggling -topmost on then off (rather than leaving it
    # topmost permanently) pulls it to the front once without pinning it
    # above every other window for the rest of the session.
    dialog.lift()
    dialog.attributes("-topmost", True)
    dialog.after_idle(dialog.attributes, "-topmost", False)
    dialog.focus_force()

    root.wait_window(dialog)
    return confirmed["value"]


def main():
    """Module entry point for `python gui.py`: show the age gate first, and only build/run the real App if it's accepted."""
    root = tk.Tk()
    root.withdraw()  # stay hidden until the age gate is cleared
    # Apply the same sv_ttk look the main App uses (App._apply_style() will
    # re-apply it once App is constructed below) so the age gate isn't stuck
    # looking like a dated stock-Tk dialog while everything after it is
    # reskinned.
    sv_ttk.set_theme(DEFAULT_THEME, root)
    # set_theme() fires a <<ThemeChanged>> virtual event that sv_ttk uses to
    # (re)configure base widget colors (incl. plain TLabel foreground/
    # background), but that event is handled asynchronously via Tk's event
    # queue rather than inline - without flushing it here, the age gate's
    # labels can get built and drawn before that handler runs, leaving them
    # with stale (light-theme) text color on the new dark background.
    # Buttons don't show this because their colors come from the theme's
    # static definition, not that async callback.
    root.update()
    App._apply_custom_style_layer(root)
    if not _show_age_gate(root):
        root.destroy()
        return
    root.deiconify()
    # Same foreground-stealing fix as the age gate above - otherwise the
    # main window can open behind the terminal/IDE that launched it.
    root.lift()
    root.attributes("-topmost", True)
    root.after_idle(root.attributes, "-topmost", False)
    root.focus_force()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
