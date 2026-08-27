"""Device/channel registry (configs/devices.json) and per-channel runtime state.

Devices are addressed per *capability* (one buttplug "feature" x one output
type it supports), not per physical toy - a dual-motor device (e.g. Lovense
Edge) exposes two independent vibrate features, and a feature that supports
more than one output type (e.g. vibrate + oscillate) exposes each as its
own channel too, so nothing is ever forced to move in lockstep with
something else unless a keybind's "devices" list explicitly says so.
devices.json remembers a friendly nickname for each (device name, feature
index, output type) triple so nicknames survive reconnects/rescans;
keybinds then target one or more nicknames, or "all".
"""

import json
from dataclasses import dataclass
from typing import Optional

from buttplug import OutputType

from src.paths import CONFIGS_DIR

DEVICES_PATH = CONFIGS_DIR / "devices.json"


def _slugify(text: str) -> str:
    """Turn arbitrary text (a device name, a profile display name, ...) into a lowercase_with_underscores id."""
    # Replace every non-alphanumeric character with "_", then collapse runs
    # of underscores and trim the ends so "Lovense Edge v2!" -> "lovense_edge_v2".
    slug = "".join(c.lower() if c.isalnum() else "_" for c in text).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "device"  # never return an empty string - callers use this as a dict key / filename


# Every output capability a feature exposes becomes its own channel, so a
# feature that can both vibrate and oscillate - or a device with several
# independent vibrating motors ("dual vibrating" toys) - exposes each as a
# separately nicknameable, separately targetable channel rather than forcing
# them to always move together. POSITION/POSITION_WITH_DURATION are excluded:
# those represent "move to an absolute position", not a continuous intensity
# level, so they don't fit the roll-a-random-level-every-tick model the rest
# of this script uses - a stroker-type device would need a different config
# shape entirely.
SUPPORTED_OUTPUT_TYPES = (
    OutputType.VIBRATE,
    OutputType.ROTATE,
    OutputType.OSCILLATE,
    OutputType.CONSTRICT,
    OutputType.SPRAY,
    OutputType.TEMPERATURE,
    OutputType.LED,
)


def load_device_registry() -> list:
    """
    Read devices.json's "channels" list: dicts with device_name,
    feature_index, output_type, description, and nickname. Returns an empty
    list (never raises) if the file is missing, unreadable, or not valid
    JSON - a fresh install or a corrupted file just means every channel gets
    treated as brand-new and re-registered with an auto-generated nickname.
    """
    if not DEVICES_PATH.exists():
        return []
    try:
        data = json.loads(DEVICES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("channels", [])


def save_device_registry(registry: list):
    """Write the full channel registry back to devices.json, overwriting whatever was there."""
    DEVICES_PATH.write_text(json.dumps({"channels": registry}, indent=2), encoding="utf-8")


def resolve_channel_nicknames(registry: list, entries: list) -> dict:
    """Map each (device_name, feature_index, output_type) in entries to a nickname.

    Reuses a saved nickname when the triple is already in the registry;
    otherwise auto-generates one (device name, plus the feature's
    description or a motorN suffix if the device has more than one feature,
    plus the output type itself if the device exposes more than one kind of
    output) and appends it to registry, saving to disk if anything changed.
    entries is a list of (device_name, feature_index, output_type, description).
    """
    by_key = {(e["device_name"], e["feature_index"], e["output_type"]): e["nickname"] for e in registry}
    existing_nicknames = set(by_key.values())

    device_output_types = {}
    device_feature_indices = {}
    for device_name, feature_index, output_type, _description in entries:
        device_output_types.setdefault(device_name, set()).add(output_type)
        device_feature_indices.setdefault(device_name, set()).add(feature_index)

    result = {}
    motor_numbers = {}  # (device_name, feature_index) -> assigned motorN number
    motor_counters = {}  # device_name -> next motor number to assign
    changed = False
    for device_name, feature_index, output_type, description in entries:
        key = (device_name, feature_index, output_type)
        if key in by_key:
            result[key] = by_key[key]
            continue

        parts = [_slugify(device_name)]
        if description:
            parts.append(_slugify(description))
        elif len(device_feature_indices[device_name]) > 1:
            motor_key = (device_name, feature_index)
            if motor_key not in motor_numbers:
                motor_counters[device_name] = motor_counters.get(device_name, 0) + 1
                motor_numbers[motor_key] = motor_counters[device_name]
            parts.append(f"motor{motor_numbers[motor_key]}")
        if len(device_output_types[device_name]) > 1:
            parts.append(_slugify(output_type))
        nickname = "_".join(parts)

        final = nickname
        n = 2
        while final in existing_nicknames:
            final = f"{nickname}_{n}"
            n += 1

        existing_nicknames.add(final)
        by_key[key] = final
        result[key] = final
        registry.append(
            {
                "device_name": device_name,
                "feature_index": feature_index,
                "output_type": output_type,
                "description": description,
                "nickname": final,
            }
        )
        changed = True

    if changed:
        save_device_registry(registry)
    return result


@dataclass
class DeviceChannel:
    """One independently controllable output - a buttplug DeviceFeature's single capability plus its own state."""

    nickname: str
    feature: object  # buttplug.feature.DeviceFeature
    output_type: OutputType
    device_name: str = ""
    description: Optional[str] = None
    last_level: float = 0.0
    pulse_active: bool = False
    ignore_until: float = 0.0
    # Set by the GUI's manual "Hold" test control (see set_test_level()); while
    # this isn't None, background_loop() leaves the channel alone entirely so
    # a manual test level doesn't get immediately overwritten by real input.
    manual_override: Optional[float] = None
    # Which binding currently owns this channel and the input token that fired it.
    # Used by priority preemption: a higher-priority binding checks these to decide
    # whether it can take over; the owning pulse checks them on cleanup to avoid
    # zeroing a channel that has already been handed to someone else.
    active_binding_id: Optional[str] = None
    active_token: Optional[str] = None


if __name__ == "__main__":
    print(f"{__file__} is TIGHC's device-registry module - it's a library, not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")
