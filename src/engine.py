"""HapticsController: owns the buttplug connection, per-channel output
state, active profile, and input listeners - the actual haptics engine.

Listens globally for keyboard and mouse events (via pynput) and turns them
into vibration levels sent to connected toys (via the buttplug client). It
doesn't read game state directly - just raw input - so every "binding" is
really "this key/button is currently held" or "this key/button was just
pressed." The foreground window is watched continuously (see
_update_active_profile) so haptics automatically follow whatever game
currently has focus, going idle when nothing matches.

NOTE on `haptics`: every setting-derived read in this file goes
through the module itself (`haptics.NAME`), never a bare name copied
in via `from src.haptics import NAME` - see that module's docstring
for why: apply_haptics_config() reassigns its globals via `global`, and only
module-qualified access sees that update on a running controller's next
tick. This is what makes gui.py's Settings tab able to change these values
without an app restart.
"""

import asyncio
import time
from typing import Optional

from buttplug import ButtplugClient, DeviceOutputCommand, OutputType
from pynput import keyboard, mouse

from src import haptics
from src.devices import SUPPORTED_OUTPUT_TYPES, DEVICES_PATH, DeviceChannel, load_device_registry, resolve_channel_nicknames
from src.input import InputState, get_foreground_window_title, normalize_key
from src.metadata import PROJECT_NAME, PROJECT_SHORT_NAME
from src.paths import PROFILES_DIR
from src.profiles import Profile
from src.ranges import DurationRange, VibeRange
from src.version import get_version


class HapticsController:
    """Owns the buttplug connection, per-channel output state, active profile, and input listeners."""

    def __init__(self, ws_url: str, profiles: dict, log_fn=print):
        """
        `profiles` is a dict of id -> Profile (from load_profiles()) that
        this controller reads from every tick; the GUI mutates its own
        controller's copy in place (add/edit/reload profiles) rather than
        replacing the dict wholesale, so background_loop() always sees
        the latest state without needing to be told about it explicitly.
        `log_fn` defaults to print() for headless use; gui.py overrides it
        with a callback that pushes into a thread-safe queue instead.
        """
        self.ws_url = ws_url
        self.profiles = profiles
        self.log = log_fn  # swap in a GUI-friendly callback instead of print(); see gui.py
        self.client: Optional[ButtplugClient] = None
        self.devices = []
        self.channels: dict = {}  # nickname -> DeviceChannel
        self.running = True
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.input_state = InputState()
        self.active_profile: Optional[Profile] = None

        # When set (by the GUI's Test tab), this profile is always "active"
        # regardless of which window is actually focused - lets you test a
        # profile's continuous bindings without alt-tabbing into the game.
        # Cleared automatically when the user leaves the Test tab.
        self.test_profile_override: Optional[Profile] = None

        # When set (by the GUI's Run tab), this profile is forced active
        # while the engine is running, overriding window-focus matching.
        # Takes precedence over test_profile_override.
        self.run_profile_override: Optional[Profile] = None

        self._panic_until = 0.0                # Output is forced to 0 until this timestamp
        self._consecutive_send_failures = 0    # Drives auto-reconnect
        self._last_reconnect_attempt = 0.0     # Cooldown gate so reconnects aren't spammed
        self._background_task: Optional[asyncio.Task] = None
        self._kb_listener: Optional[keyboard.Listener] = None
        self._mouse_listener: Optional[mouse.Listener] = None
        self._pulse_cancel_events: dict = {}   # token -> asyncio.Event, for stop-on-release
        self._held_bindings: dict = {}          # token -> Binding, all keys currently held that have a binding

    # ---------------------------------------------------------------- setup
    async def connect(self) -> bool:
        """
        Open a fresh connection to Intiface and do an initial device scan.
        Returns True if at least one usable channel was found, False on a
        connection failure or an empty scan (both are logged via self.log,
        not raised - callers just check the return value).
        """
        # Both the headless run() path and the GUI reach connect() first, so
        # this is the one place that's guaranteed to run on the loop that
        # should service schedule()'s cross-thread coroutine handoff.
        if self.loop is None:
            self.loop = asyncio.get_running_loop()

        self.client = ButtplugClient(PROJECT_SHORT_NAME)
        try:
            await self.client.connect(self.ws_url)
            self.log("Connected to Intiface!")
        except Exception as e:
            self.log(f"Connection failed: {e}")
            return False

        return await self.scan()

    async def scan(self) -> bool:
        """
        (Re)scan for devices on the existing connection and rebuild
        self.channels from whatever's found. If there's no existing
        connection yet, this just delegates to connect() instead (so GUI
        code can always call scan() without checking connection state
        first). Returns True if at least one channel exists afterward.
        """
        if not self.client:
            return await self.connect()

        # Scan for a fixed window rather than waiting for a signal, since
        # buttplug doesn't tell us when scanning has found everything nearby.
        await self.client.start_scanning()
        await asyncio.sleep(4)
        await self.client.stop_scanning()

        self.devices = list(self.client.devices.values())
        self.channels = self._build_channels(self.devices)
        if self.channels:
            self.log(f"Found {len(self.devices)} device(s), {len(self.channels)} channel(s)")
            return True
        self.log("No devices found")
        return False

    def _build_channels(self, devices: list) -> dict:
        """
        Turn a list of connected buttplug devices into {nickname: DeviceChannel},
        one entry per (device, feature, output type) combination found in
        SUPPORTED_OUTPUT_TYPES. Nicknames are resolved (and any new ones
        persisted to devices.json) via resolve_channel_nicknames(), so a
        channel keeps the same nickname across repeated scans/reconnects.
        """
        entries = []  # (device_name, feature_index, output_type_value, description, feature)
        for device in devices:
            for feature in device.features.values():
                for output_type in SUPPORTED_OUTPUT_TYPES:
                    if feature.has_output(output_type):
                        entries.append((device.name, feature.index, output_type.value, feature.description, feature))

        registry = load_device_registry()
        nickname_map = resolve_channel_nicknames(
            registry, [(dn, fi, ot, desc) for dn, fi, ot, desc, _ in entries]
        )

        channels = {}
        for device_name, feature_index, output_type_value, description, feature in entries:
            nickname = nickname_map[(device_name, feature_index, output_type_value)]
            channels[nickname] = DeviceChannel(
                nickname=nickname,
                feature=feature,
                output_type=OutputType(output_type_value),
                device_name=device_name,
                description=description,
            )
        return channels

    async def _attempt_reconnect(self):
        """
        Called by background_loop() once too many consecutive output sends
        have failed (see FAILURE_RECONNECT_THRESHOLD) - drops the current
        client (if any) and tries connect() again from scratch. Doesn't
        raise on failure; just logs and lets the next threshold trip retry.
        """
        self.log("Lost contact with device(s) - attempting to reconnect...")
        self._consecutive_send_failures = 0
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        if await self.connect():
            self.log("Reconnected!")
        else:
            self.log("Reconnect attempt failed, will retry.")

    # ----------------------------------------------------------- profiles
    def _match_profile(self, window_title: str) -> Optional[Profile]:
        """Return the first loaded profile whose window_titles matches, or None if nothing matches."""
        # Profiles are matched in self.profiles' insertion order (i.e.
        # alphabetical by folder name, per load_profiles()) - if two
        # profiles' window_titles could both match the same window, the
        # alphabetically-first one wins.
        for profile in self.profiles.values():
            if profile.matches(window_title):
                return profile
        return None

    def _update_active_profile(self):
        """
        Re-evaluate which profile should be "active" and update
        self.active_profile if it changed. Called once per background_loop()
        tick. A pinned test_profile_override always wins over real window
        matching (see its definition in __init__) so the GUI's Test tab can
        force a specific profile active regardless of what's focused.
        Switching profiles (including to/from None) always clears
        pressed_keys, since held tokens from the old profile's keybinds
        wouldn't mean anything under a different one anyway.
        """
        if self.run_profile_override is not None:
            matched = self.run_profile_override
        elif self.test_profile_override is not None:
            matched = self.test_profile_override
        else:
            title = get_foreground_window_title()
            matched = self._match_profile(title)
        if matched is self.active_profile:
            return
        self.active_profile = matched
        self.input_state.pressed_keys.clear()
        self._held_bindings.clear()
        if matched:
            self.log(f"[Profile] Switched to: {matched.name}")
        else:
            self.log("[Profile] No matching game focused - haptics idle.")

    # ------------------------------------------------------- channel targets
    def _channels_for(self, target: Optional[frozenset]) -> list:
        """Resolve a binding's `devices` target (None = "all") to the actual list of currently-connected DeviceChannels."""
        if target is None:
            return list(self.channels.values())
        return [c for nickname, c in self.channels.items() if nickname in target]

    # -------------------------------------------------------------- output
    def roll(self, vibe_range: VibeRange) -> float:
        """Roll a level from the given range, unless master randomization overrides it."""
        # MASTER_RANDOM_ENABLED is a global "ignore every per-binding range
        # and just roll from one fixed band instead" override, set in
        # configs/haptics.json - useful for testing or for players
        # who want pure randomness regardless of what they're doing in-game.
        active_range = haptics.MASTER_VIBE_RANGE if haptics.MASTER_RANDOM_ENABLED else vibe_range
        return active_range.roll()

    def _smooth(self, channel: DeviceChannel, target: float) -> float:
        """Exponentially smooth a channel's output level toward `target`, or return `target` unchanged if smoothing is off."""
        # Closes part of the gap to the target each tick instead of jumping
        # straight there, so idle/continuous levels feel like they drift
        # rather than strobe every 180ms. Each channel keeps its own
        # last_level, so smoothing is independent per channel.
        if not haptics.ENABLE_SMOOTHING:
            return target
        return channel.last_level + (target - channel.last_level) * haptics.SMOOTHING_FACTOR

    async def _set_channel_level(self, channel: DeviceChannel, level: float):
        """
        Send `level` to one channel's underlying feature, update its
        last_level, and track the send's success/failure toward the
        auto-reconnect threshold. This is the single place every code path
        (background_loop, pulses, panic, GUI test controls) funnels through
        to actually talk to a device - so failure counting and the panic
        override only need to live in one spot.
        """
        # Panic always wins, even over a manually-held test level.
        if time.time() < self._panic_until:
            level = 0.0

        sent = False
        try:
            await channel.feature.run_output(DeviceOutputCommand(channel.output_type, level))
            sent = True
        except Exception:
            # A single failed send just counts toward the reconnect
            # threshold below rather than raising - a flaky BLE packet
            # shouldn't kill the whole loop.
            pass

        if self.channels:
            self._consecutive_send_failures = 0 if sent else self._consecutive_send_failures + 1
        channel.last_level = level

    def _priority_index(self, binding_id: str) -> int:
        """Lower return value = higher priority. Bindings absent from the priority list rank last."""
        if not self.active_profile or not self.active_profile.priority:
            return 0
        try:
            return self.active_profile.priority.index(binding_id)
        except ValueError:
            return len(self.active_profile.priority)

    async def _resume_held_binding(self, released_token: str):
        """
        After the pulse for `released_token` ends (key released or preempted),
        restart the highest-priority binding whose key is still held but whose
        pulse is no longer running. Skips resumption if all target channels are
        already owned by a binding with equal or higher priority.
        """
        if not self.active_profile or not self._held_bindings:
            return
        priority = self.active_profile.priority
        candidates = {
            t: b for t, b in self._held_bindings.items()
            if t != released_token and t not in self._pulse_cancel_events
        }
        if not candidates:
            return
        best_token = min(
            candidates,
            key=lambda t: priority.index(candidates[t].id) if candidates[t].id in priority else len(priority),
        )
        best_binding = candidates[best_token]
        best_pri = self._priority_index(best_binding.id)
        channels = self._channels_for(best_binding.devices)
        if channels and all(
            c.pulse_active and self._priority_index(c.active_binding_id or "") <= best_pri
            for c in channels
        ):
            return  # all channels already owned by equal or higher priority
        await self.pulse(best_binding.vibe, None, best_binding.devices, token=best_token, binding_id=best_binding.id)

    async def _do_pulse(self, vibe_range: VibeRange, duration: Optional[float], target: Optional[frozenset], cancel_event: Optional[asyncio.Event] = None, *, token: Optional[str] = None, binding_id: Optional[str] = None):
        """
        Shared implementation behind pulse() and test_pulse().

        When `binding_id` is given and the active profile has a priority list,
        channels currently owned by a lower-priority binding are preempted: their
        pulse is cancelled and this one takes over immediately. When this pulse
        ends the best remaining held binding is resumed via _resume_held_binding().

        `duration=None` means "hold until cancel_event fires" (real input path).
        A float duration is used by test_pulse() and scroll for timed bursts.
        """
        now = time.time()

        if binding_id and self.active_profile and self.active_profile.priority:
            new_pri = self._priority_index(binding_id)
            targets = []
            to_preempt = []
            for c in self._channels_for(target):
                if now < c.ignore_until:
                    continue
                if not c.pulse_active:
                    targets.append(c)
                elif c.active_token is not None and self._priority_index(c.active_binding_id or "") > new_pri:
                    targets.append(c)
                    to_preempt.append((c, c.active_token))
        else:
            targets = [c for c in self._channels_for(target) if now >= c.ignore_until and not c.pulse_active]
            to_preempt = []

        if not targets:
            return

        # Cancel lower-priority pulses we are taking over
        for _c, preempted_token in to_preempt:
            ev = self._pulse_cancel_events.get(preempted_token)
            if ev is not None:
                ev.set()

        for channel in targets:
            channel.pulse_active = True
            channel.active_binding_id = binding_id
            channel.active_token = token
            if duration is not None:
                channel.ignore_until = now + duration

        await asyncio.gather(*(self._set_channel_level(c, self.roll(vibe_range)) for c in targets))

        if cancel_event is not None:
            if duration is not None:
                try:
                    await asyncio.wait_for(cancel_event.wait(), timeout=duration)
                    for channel in targets:
                        channel.ignore_until = 0.0
                except asyncio.TimeoutError:
                    pass
            else:
                # Hold indefinitely until the key is released
                await cancel_event.wait()
                for channel in targets:
                    channel.ignore_until = 0.0
        elif duration is not None:
            await asyncio.sleep(duration)

        # Only release ownership of channels this pulse still owns.
        # A channel whose active_token has changed was preempted by a higher-priority
        # binding that already took ownership - leave it alone.
        for channel in targets:
            if channel.active_token == token:
                channel.pulse_active = False
                channel.active_binding_id = None
                channel.active_token = None

        # Send 0 only to channels we still own; preempted channels keep their new level.
        owned = [c for c in targets if c.active_token is None]
        if owned:
            await asyncio.gather(
                *(self._set_channel_level(c, c.manual_override if c.manual_override is not None else 0.0) for c in owned)
            )

        # Resume the best lower-priority held binding now that this pulse has ended.
        if token is not None:
            await self._resume_held_binding(token)

    async def pulse(self, vibe_range: VibeRange, duration: Optional[float], target: Optional[frozenset], token: Optional[str] = None, binding_id: Optional[str] = None):
        """Vibration triggered by real input - a no-op while no profile is active.

        When `token` is supplied the pulse holds until the key/button is released (duration=None)
        or until the optional max duration elapses, whichever comes first.
        """
        if self.active_profile is None:
            return
        if token is not None:
            cancel_event = asyncio.Event()
            self._pulse_cancel_events[token] = cancel_event
            try:
                await self._do_pulse(vibe_range, duration, target, cancel_event, token=token, binding_id=binding_id)
            finally:
                self._pulse_cancel_events.pop(token, None)
        else:
            await self._do_pulse(vibe_range, duration, target, binding_id=binding_id)

    async def test_pulse(self, vibe_range: VibeRange, duration: float, target: Optional[frozenset]):
        """Same as pulse(), but for the GUI's manual test controls - fires even with no active profile."""
        await self._do_pulse(vibe_range, duration, target)

    async def set_test_level(self, nickname: str, level: float):
        """Manually pin one channel to `level` until clear_test_level() - for GUI testing only."""
        channel = self.channels.get(nickname)
        if channel is None:
            return
        channel.manual_override = level
        await self._set_channel_level(channel, level)

    async def clear_test_level(self, nickname: str):
        """Release a manual test hold, letting background_loop() drive the channel normally again."""
        channel = self.channels.get(nickname)
        if channel is None:
            return
        channel.manual_override = None

    async def panic(self):
        """Force haptics off via the panic key: either suppress for a timed hold or stop the engine entirely."""
        if haptics.PANIC_MODE == "stop":
            self.log(f"PANIC key ({haptics.PANIC_KEY.upper()}) pressed - stopping engine.")
            await self.stop_engine()
        else:
            self._panic_until = time.time() + haptics.PANIC_HOLD_DURATION
            await asyncio.gather(*(self._set_channel_level(c, 0.0) for c in self.channels.values()))
            self.log(f"PANIC key ({haptics.PANIC_KEY.upper()}) pressed - haptics forced off for {haptics.PANIC_HOLD_DURATION:.1f}s.")

    async def background_loop(self):
        """
        The main output loop: re-evaluates the active profile and every
        channel's target level once every BACKGROUND_TICK seconds, for as
        long as self.running is True. Started by start_engine() and left
        running as an asyncio.Task; stop_engine() cancels it.

        Each tick, per channel: if a pulse or a manual test hold currently
        "owns" that channel, it's left alone; otherwise its level is
        resolved from the active profile's continuous bindings (or the
        idle background range if nothing's held), smoothed, and sent. This
        is also where the auto-reconnect threshold is checked.
        """
        while self.running:
            self._update_active_profile()

            now = time.time()
            for channel in self.channels.values():
                if channel.pulse_active or now < channel.ignore_until or channel.manual_override is not None:
                    continue
                # No binding is currently holding this channel - keep idle at zero.
                await self._set_channel_level(channel, 0.0)

            if (
                haptics.ENABLE_AUTO_RECONNECT
                and self._consecutive_send_failures >= haptics.FAILURE_RECONNECT_THRESHOLD
                and time.time() - self._last_reconnect_attempt >= haptics.RECONNECT_COOLDOWN
            ):
                self._last_reconnect_attempt = time.time()
                await self._attempt_reconnect()

            await asyncio.sleep(haptics.BACKGROUND_TICK)

    # --------------------------------------------------------------- input
    def schedule(self, coro):
        """
        Hand a coroutine off to the asyncio loop from any thread.

        pynput's keyboard/mouse listeners each run on their own OS thread,
        not the asyncio event loop, so they can't just `await` something -
        every on_key_press()/on_mouse_click()/on_mouse_scroll() callback
        below calls this instead of awaiting directly. No-ops once
        self.running is False (e.g. mid-shutdown), so a straggling input
        event can't schedule work against a loop that's going away.
        """
        if self.running and self.loop:
            asyncio.run_coroutine_threadsafe(coro, self.loop)

    def on_key_press(self, key):
        """pynput callback: track the key as held, handle the panic key, and fire a pulse if it's a pulse-mode binding."""
        try:
            k = normalize_key(key)
        except Exception:
            return
        was_held = k in self.input_state.pressed_keys
        self.input_state.pressed_keys.add(k)

        if haptics.ENABLE_PANIC_KEY and k == haptics.PANIC_KEY:
            self.schedule(self.panic())
            return

        if self.active_profile is None or was_held:
            return
        binding = self.active_profile.bindings_by_key.get(k)
        if binding:
            self._held_bindings[k] = binding
            self.log(f"[{binding.id}]: activated ({k}) [{binding.vibe}]")
            self.schedule(self.pulse(binding.vibe, None, binding.devices, token=k, binding_id=binding.id))

    def on_key_release(self, key):
        """pynput callback: stop tracking the key as held; cancels any in-progress pulse for that key."""
        try:
            k = normalize_key(key)
        except Exception:
            return
        self.input_state.pressed_keys.discard(k)
        self._held_bindings.pop(k, None)
        cancel_event = self._pulse_cancel_events.get(k)
        if cancel_event is not None and self.loop:
            self.loop.call_soon_threadsafe(cancel_event.set)
            self.log(f"[{k}]: released")

    def on_mouse_click(self, _x, _y, button, pressed):
        """
        pynput callback for left/right/middle mouse button press+release.
        Mouse buttons have no natural "held" concept the way keys do, so
        this both tracks the button as held (for continuous bindings, via
        set_held) and fires a pulse on press (for pulse bindings) - whether
        either actually does anything depends on how the active profile
        configured that button. `_x`/`_y` (cursor position) are unused but
        required by pynput's callback signature.
        """
        token = {
            mouse.Button.left: "mouse_left",
            mouse.Button.right: "mouse_right",
            mouse.Button.middle: "mouse_middle",
        }.get(button)
        if token is None:
            return

        self.input_state.set_held(token, pressed)
        if pressed and self.active_profile is not None:
            binding = self.active_profile.bindings_by_key.get(token)
            if binding:
                self._held_bindings[token] = binding
                self.log(f"[{binding.id}]: activated ({token}) [{binding.vibe}]")
                self.schedule(self.pulse(binding.vibe, None, binding.devices, token=token, binding_id=binding.id))
        elif not pressed:
            self._held_bindings.pop(token, None)
            cancel_event = self._pulse_cancel_events.get(token)
            if cancel_event is not None and self.loop:
                self.loop.call_soon_threadsafe(cancel_event.set)
                self.log(f"[{token}]: released")

    def on_mouse_scroll(self, _x, _y, _dx, _dy):
        """
        pynput callback for the scroll wheel. Each scroll tick is a discrete
        event with no release, so scroll bindings fire a short fixed-duration
        burst (0.15s) rather than holding until release.
        `_x`/`_y`/`_dx`/`_dy` (position and scroll delta) are unused but
        required by pynput's callback signature.
        """
        if self.active_profile is None:
            return
        binding = self.active_profile.bindings_by_key.get("scroll")
        if binding:
            self.log(f"[{binding.id}]: activated (scroll) [{binding.vibe}]")
            self.schedule(self.pulse(binding.vibe, 0.15, binding.devices, binding_id=binding.id))

    # ----------------------------------------------------------------- run
    @staticmethod
    def _devices_label(devices: Optional[frozenset]) -> str:
        """Render a binding's resolved devices target as a display string for the startup banner."""
        return "all" if devices is None else ",".join(sorted(devices))

    @classmethod
    def _binding_line(
        cls, label: str, enabled: bool, vibe_range: VibeRange, duration_range: Optional[DurationRange], devices: Optional[frozenset]
    ) -> str:
        """Format one banner line for a binding (or the idle/background level, passed with duration_range=None)."""
        if not enabled:
            return f"  - {label:<32} -> disabled"
        target = "" if devices is None else f" [{cls._devices_label(devices)}]"
        if duration_range is not None:
            return f"  - {label:<32} -> {vibe_range} pulse ({duration_range}){target}"
        return f"  - {label:<32} -> {vibe_range}{target}"

    @staticmethod
    def _status_line(label: str, value: str) -> str:
        """Format one banner line for a global on/off-style status (panic key, auto-reconnect, smoothing)."""
        return f"- {label:<24} -> {value}"

    def print_banner(self):
        """
        Log a human-readable summary of the current setup right after the
        engine starts: every loaded profile's bindings and their resolved
        ranges/devices, which channels are connected, and the global
        settings in effect. Purely informational - nothing here affects
        behavior, it's just what a headless run prints to the console (or
        what the GUI's Run tab log shows after clicking Start).
        """
        self.log(f"{PROJECT_SHORT_NAME} v{get_version()} active - {PROJECT_NAME}")
        if haptics.MASTER_RANDOM_ENABLED:
            self.log(
                f"- MASTER RANDOM ON        -> every binding rolls {haptics.MASTER_VIBE_RANGE} "
                "(per-binding ranges ignored)"
            )

        if self.channels:
            self.log(f"Channels ({len(self.channels)}): {', '.join(sorted(self.channels))}")
        else:
            self.log("No channels connected - haptics will stay idle.")

        if not self.profiles:
            self.log(f"No profiles found in {PROFILES_DIR} - haptics will stay idle.")
        for profile in self.profiles.values():
            self.log(f"[{profile.name}]  (window match: {'/'.join(profile.window_titles)})")
            for b in profile.bindings:
                label = f"{b['id']} ({'+'.join(b['keys'])})"
                self.log(self._binding_line(label, b["enabled"], b["vibe"], b["duration"], b["devices"]))

        self.log(self._status_line("Profile switching", "automatic, based on the focused window"))
        if haptics.ENABLE_PANIC_KEY:
            self.log(
                self._status_line(
                    f"Panic key ({haptics.PANIC_KEY.upper()})",
                    f"forces output off for {haptics.PANIC_HOLD_DURATION:.1f}s",
                )
            )
        else:
            self.log(self._status_line("Panic key", "disabled"))
        self.log(self._status_line("Auto-reconnect", "enabled" if haptics.ENABLE_AUTO_RECONNECT else "disabled"))
        self.log(
            self._status_line(
                "Level smoothing",
                f"enabled (factor {haptics.SMOOTHING_FACTOR})" if haptics.ENABLE_SMOOTHING else "disabled",
            )
        )
        self.log(f"Global config: {haptics.HAPTICS_CONFIG_PATH}")
        self.log(f"Profiles dir:  {PROFILES_DIR}")
        self.log(f"Devices file:  {DEVICES_PATH}")

    def start_engine(self):
        """Start the background output loop and input listeners. Safe to call once per connection."""
        if self._background_task and not self._background_task.done():
            return
        self.running = True
        self._background_task = asyncio.create_task(self.background_loop())

        # pynput listeners run in background threads and call our handlers
        # from there; they stay alive independently of the asyncio loop.
        self._kb_listener = keyboard.Listener(on_press=self.on_key_press, on_release=self.on_key_release)
        self._mouse_listener = mouse.Listener(on_click=self.on_mouse_click, on_scroll=self.on_mouse_scroll)
        self._kb_listener.start()
        self._mouse_listener.start()

        self.print_banner()

    async def stop_engine(self):
        """Stop input listeners and the background loop, forcing every channel off. Connection stays open."""
        self.running = False
        self._panic_until = 0.0
        self._held_bindings.clear()
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except (asyncio.CancelledError, Exception):
                pass
            self._background_task = None
        if self._kb_listener:
            self._kb_listener.stop()
            self._kb_listener = None
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        await asyncio.gather(*(self._set_channel_level(c, 0.0) for c in self.channels.values()))

    async def shutdown(self):
        """
        Stop the engine and disconnect from Intiface, resetting connection
        state back to how it looks before connect() is ever called - safe
        to call while the app keeps running (e.g. a GUI "Disconnect"
        button), not just once at exit. Without clearing self.client, a
        subsequent connect() would still succeed (it always builds a fresh
        ButtplugClient), but anything checking "is there already a client"
        first - like gui.py's Start button, which skips connect() entirely
        if self.client is truthy - would wrongly treat a disconnected
        client as still connected. Clearing self.channels similarly avoids
        stale entries (from features on a connection that no longer exists)
        lingering in a device list the GUI hasn't otherwise been told to
        refresh.
        """
        await self.stop_engine()
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.channels = {}

    async def run(self):
        """Headless entry point: connect, run until Ctrl+C, then shut down."""
        if not await self.connect():
            return
        self.start_engine()
        try:
            # Nothing to do here but keep the event loop alive; all the real
            # work happens in background_loop() and the input callbacks.
            while self.running:
                await asyncio.sleep(0.5)
        finally:
            await self.shutdown()


if __name__ == "__main__":
    print(f"{__file__} is TIGHC's haptics engine module - it's a library, not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")
