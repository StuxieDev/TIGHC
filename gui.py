"""The Intiface Game Haptics Controller (TIGHC) - interactive GUI.

Tkinter configurator and launcher for haptics.py.

Connect to Intiface, scan for devices, assign friendly nicknames to
individual motors/capabilities, build game profiles (keybinds + ranges +
which device each keybind drives), tweak global settings, and start/stop
the haptics engine - all from one window. Everything you do here is written
to the same JSON files haptics.py reads (haptics_config.json, devices.json,
profiles/<id>/{keybinds,ranges}.json), so hand-editing those files and using
this GUI are fully interchangeable.

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

import haptics
from haptics import (
    CONFIG_PATH,
    PROFILES_DIR,
    PROJECT_NAME,
    PROJECT_SHORT_NAME,
    REPO_URL,
    DurationRange,
    HapticsController,
    VibeRange,
    __version__,
    load_config,
    load_device_registry,
    load_profiles,
    save_device_registry,
)

CHANGELOG_PATH = Path(__file__).with_name("CHANGELOG.md")

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
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"{PROJECT_SHORT_NAME} - {PROJECT_NAME} (v{__version__})")
        root.geometry("1040x720")
        root.minsize(860, 600)

        self._apply_style(root)

        self.log_queue = queue.Queue()
        self.bridge = AsyncBridge()
        self.controller = HapticsController(haptics.INTIFACE_WS, dict(haptics.PROFILES), log_fn=self._enqueue_log)

        self.current_profile_id = None
        self.current_bindings = []  # editable plain-dict copies of the loaded profile's bindings
        self._test_channel_widgets = {}  # nickname -> {"level_var", "hold_var"}, rebuilt by _refresh_test_channels
        self._test_binding_widgets = {}  # binding id -> (hold_var, tokens), continuous bindings only

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
        return "Switch to light mode" if self.theme == "dark" else "Switch to dark mode"

    # =============================================================== UI shell
    def _build_ui(self):
        # A slim top bar sits above the tab notebook, outside it, so the
        # theme toggle is reachable from every tab instead of duplicated
        # into each one.
        top_bar = ttk.Frame(self.root)
        top_bar.pack(fill="x", padx=PADX // 2, pady=(PADY // 2, 0))
        self.theme_toggle_btn = ttk.Button(top_bar, text=self._theme_toggle_label(), command=self._on_toggle_theme)
        self.theme_toggle_btn.pack(side="right")

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

    @staticmethod
    def _add_header(frame, text):
        """A bold title at the top of a tab, so each one reads like a distinct page rather than a bare form."""
        ttk.Label(frame, text=text, style="Header.TLabel").pack(fill="x", padx=PADX, pady=(PADY, 0))

    # =============================================================== Devices tab
    def _build_devices_tab(self):
        frame = self.devices_tab
        self._add_header(frame, "Devices")

        # Connection controls: URL + Connect (fresh connection, includes an
        # initial scan) + Rescan (reuse the existing connection, look again).
        top = ttk.Frame(frame)
        top.pack(fill="x", padx=PADX, pady=PADY)
        ttk.Label(top, text="Intiface WebSocket URL:").pack(side="left")
        self.ws_url_var = tk.StringVar(value=self.controller.ws_url)
        ttk.Entry(top, textvariable=self.ws_url_var, width=30).pack(side="left", padx=6)
        self.connect_btn = ttk.Button(top, text="Connect + Scan", command=self._on_connect_clicked)
        self.connect_btn.pack(side="left", padx=4)
        self.rescan_btn = ttk.Button(top, text="Rescan", command=self._on_rescan_clicked)
        self.rescan_btn.pack(side="left", padx=4)

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
        self.controller.ws_url = self.ws_url_var.get().strip()
        self.connect_btn.config(state="disabled")
        self._enqueue_log(f"Connecting to {self.controller.ws_url} ...")
        fut = self.bridge.submit(self.controller.connect())
        fut.add_done_callback(lambda f: self.root.after(0, self._after_connect, f))

    def _after_connect(self, fut):
        self.connect_btn.config(state="normal")
        try:
            fut.result()
        except Exception as e:
            self._enqueue_log(f"Connect failed: {e}")
        self._refresh_channels_tree()

    def _on_rescan_clicked(self):
        self.rescan_btn.config(state="disabled")
        fut = self.bridge.submit(self.controller.scan())
        fut.add_done_callback(lambda f: self.root.after(0, self._after_rescan, f))

    def _after_rescan(self, fut):
        self.rescan_btn.config(state="normal")
        try:
            fut.result()
        except Exception as e:
            self._enqueue_log(f"Scan failed: {e}")
        self._refresh_channels_tree()

    def _refresh_channels_tree(self):
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
        references = []
        for profile in self.controller.profiles.values():
            for binding in profile.bindings:
                devices = binding["devices"]
                if devices is not None and nickname in devices:
                    references.append(f"{profile.name}: {binding['id']}")
        return references

    # =============================================================== Profiles tab
    def _build_profiles_tab(self):
        frame = self.profiles_tab
        self._add_header(frame, "Profiles")

        top = ttk.Frame(frame)
        top.pack(fill="x", padx=PADX, pady=PADY)
        ttk.Label(top, text="Profile:").pack(side="left")
        self.profile_combo = ttk.Combobox(top, state="readonly", width=25)
        self.profile_combo.pack(side="left", padx=6)
        self.profile_combo.bind("<<ComboboxSelected>>", lambda e: self._load_profile_into_form(self.profile_combo.get()))
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

    def _refresh_profile_list(self):
        ids = sorted(self.controller.profiles.keys())
        self.profile_combo["values"] = ids
        self.test_profile_combo["values"] = ids
        if not ids:
            self.current_profile_id = None
            self._refresh_test_bindings()
            return
        if self.current_profile_id not in ids:
            self.current_profile_id = ids[0]
        self.profile_combo.set(self.current_profile_id)
        self._load_profile_into_form(self.current_profile_id)
        if self.test_profile_combo.get() not in ids:
            self.test_profile_combo.set(self.current_profile_id)
        self._refresh_test_bindings()

    @staticmethod
    def _binding_to_editable(binding: dict) -> dict:
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

    def _refresh_bindings_tree(self):
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
        new_id = simpledialog.askstring("New profile", "Folder id (letters/numbers/underscores):", parent=self.root)
        if not new_id:
            return
        new_id = haptics._slugify(new_id)
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
            keybinds = copy.deepcopy(haptics.DEFAULT_MINECRAFT_KEYBINDS)
            ranges = copy.deepcopy(haptics.DEFAULT_MINECRAFT_RANGES)
        keybinds["name"] = display_name
        keybinds["window_titles"] = [window_title.lower()]

        new_dir.mkdir(parents=True)
        (new_dir / "keybinds.json").write_text(json.dumps(keybinds, indent=2), encoding="utf-8")
        (new_dir / "ranges.json").write_text(json.dumps(ranges, indent=2), encoding="utf-8")
        try:
            profile = haptics._load_profile(new_dir)
        except Exception as e:
            shutil.rmtree(new_dir)
            messagebox.showerror("New profile", f"Failed to create profile: {e}")
            return

        self.controller.profiles[profile.id] = profile
        self.current_profile_id = profile.id
        self._refresh_profile_list()
        self._enqueue_log(f"Created profile '{profile.name}' (copied bindings from the Minecraft template).")

    def _on_reload_profiles(self):
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
            profile = haptics._load_profile(profile_dir)
        except Exception as e:
            keybinds_path.write_text(backup[0], encoding="utf-8")
            ranges_path.write_text(backup[1], encoding="utf-8")
            messagebox.showerror("Save profile", f"Not saved - config is invalid:\n{e}")
            return

        self.controller.profiles[profile.id] = profile
        self._enqueue_log(f"Saved profile '{profile.name}'.")
        self._load_profile_into_form(profile.id)

    def _open_binding_dialog(self, existing=None):
        dialog = tk.Toplevel(self.root)
        dialog.title("Binding")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        def row_entry(r, label, var, width=32):
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

        result = {}

        def on_ok():
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
        result = self._open_binding_dialog()
        if not result:
            return
        if any(b["id"] == result["id"] for b in self.current_bindings):
            messagebox.showerror("Add binding", f"id '{result['id']}' already exists in this profile.")
            return
        self.current_bindings.append(result)
        self._refresh_bindings_tree()

    def _on_edit_binding(self):
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
    # and a "pinned" active profile - see test_profile_override in haptics.py).
    def _build_test_tab(self):
        frame = self.test_tab
        self._add_header(frame, "Test")

        top = ttk.Frame(frame)
        top.pack(fill="x", padx=PADX, pady=PADY)
        ttk.Label(top, text="Profile to test:").pack(side="left")
        self.test_profile_combo = ttk.Combobox(top, state="readonly", width=22)
        self.test_profile_combo.pack(side="left", padx=6)
        self.test_profile_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_test_bindings())
        self.test_pin_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top, text="Pin as active profile (ignores the real focused window)",
            variable=self.test_pin_var, command=self._on_toggle_test_pin,
        ).pack(side="left", padx=12)
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

    # --- manual per-channel control -----------------------------------
    def _refresh_test_channels(self):
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
        pct_label.config(text=f"{level_var.get()}%")
        if hold_var.get():
            self.bridge.submit(self.controller.set_test_level(nickname, level_var.get() / 100.0))

    def _on_toggle_channel_hold(self, nickname, level_var, hold_var):
        if hold_var.get():
            self.bridge.submit(self.controller.set_test_level(nickname, level_var.get() / 100.0))
        else:
            self.bridge.submit(self.controller.clear_test_level(nickname))

    def _on_test_channel_pulse(self, nickname, level_var):
        level = level_var.get() / 100.0
        vibe = VibeRange(level, level)
        self.bridge.submit(self.controller.test_pulse(vibe, 0.6, frozenset({nickname})))

    # --- simulated keybinds ---------------------------------------------
    def _refresh_test_bindings(self):
        for child in self.test_bindings_container.winfo_children():
            child.destroy()
        self._test_binding_widgets = {}

        profile_id = self.test_profile_combo.get()
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
        # Simulates the key(s) being physically held - background_loop()
        # picks this up exactly like real pynput input would, as long as
        # this profile is pinned active (see _on_toggle_test_pin below).
        if hold_var.get():
            self.controller.input_state.pressed_keys |= set(tokens)
        else:
            self.controller.input_state.pressed_keys -= set(tokens)

    def _on_trigger_binding(self, binding):
        duration = binding["duration"].roll() if binding["duration"] else 0.3
        self.bridge.submit(self.controller.test_pulse(binding["vibe"], duration, binding["devices"]))

    def _on_toggle_test_pin(self):
        profile = self.controller.profiles.get(self.test_profile_combo.get())
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
        frame = self.settings_tab
        self._add_header(frame, "Global settings")
        # A separate grid-managed body frame, since the header above uses
        # pack() - Tkinter doesn't allow mixing geometry managers on the
        # same parent's direct children.
        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True, padx=PADX, pady=PADY)

        cfg = load_config()
        pad = {"padx": 6, "pady": 3}
        self.cfg_vars = {}
        row = 0

        def add(label, key, default):
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
            body, text="Global settings are read once at startup - restart the app for changes to take effect.",
            style="Hint.TLabel",
        ).grid(row=row, column=0, columnspan=2, sticky="w", **pad)

    def _on_save_settings(self):
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
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        self._enqueue_log("Settings saved. Restart the app for changes to take effect.")

    # =============================================================== Run tab
    def _build_run_tab(self):
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
        self.start_btn.config(state="disabled")

        async def _start():
            if not self.controller.client:
                if not await self.controller.connect():
                    return False
            self.controller.start_engine()
            return True

        fut = self.bridge.submit(_start())
        fut.add_done_callback(lambda f: self.root.after(0, self._after_start, f))

    def _after_start(self, fut):
        try:
            ok = fut.result()
        except Exception as e:
            ok = False
            self._enqueue_log(f"Start failed: {e}")
        self._refresh_channels_tree()
        if ok:
            self.stop_btn.config(state="normal")
            self.status_var.set("Running")
        else:
            self.start_btn.config(state="normal")

    def _on_stop(self):
        self.stop_btn.config(state="disabled")
        fut = self.bridge.submit(self.controller.stop_engine())
        fut.add_done_callback(lambda f: self.root.after(0, self._after_stop, f))

    def _after_stop(self, fut):
        try:
            fut.result()
        except Exception as e:
            self._enqueue_log(f"Stop error: {e}")
        self.start_btn.config(state="normal")
        self.status_var.set("Stopped")

    # =============================================================== About tab
    def _build_about_tab(self):
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
        self.log_queue.put(str(message))

    def _poll_log_queue(self):
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
        if self.controller.channels:
            active = self.controller.active_profile.name if self.controller.active_profile else "(none - idle)"
            lines = [f"Active profile: {active}"]
            for nickname, channel in sorted(self.controller.channels.items()):
                lines.append(f"  {nickname}: {channel.last_level * 100:.0f}%")
            self.levels_label.config(text="\n".join(lines))
        else:
            self.levels_label.config(text="(not connected)")
        self._status_poll_id = self.root.after(400, self._poll_status)

    def _on_close(self):
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
    dialog.transient(root)
    dialog.grab_set()

    confirmed = {"value": False}

    def confirm():
        confirmed["value"] = True
        dialog.destroy()

    def decline():
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

    root.wait_window(dialog)
    return confirmed["value"]


def main():
    root = tk.Tk()
    root.withdraw()  # stay hidden until the age gate is cleared
    if not _show_age_gate(root):
        root.destroy()
        return
    root.deiconify()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
