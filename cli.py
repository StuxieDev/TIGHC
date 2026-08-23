"""The Intiface Game Haptics Controller (TIGHC) - headless CLI entry point.

Run this to start the engine from the command line: `python cli.py`. Age
gate, then builds a controller from src/core.py's module-level config/
profiles (configs/haptics_config.json, configs/devices.json, profiles/) and
runs it until Ctrl+C. No GUI - hand-edit the JSON files and restart to
change anything.

Prefer an interactive configurator? Use `python gui.py` instead.
"""

import asyncio

from src.core import HapticsController, INTIFACE_WS, PROFILES, PROJECT_NAME, PROJECT_SHORT_NAME, __version__


def _confirm_age() -> bool:
    """Self-attestation age gate for the headless CLI - the GUI has its own dialog equivalent (see gui.py)."""
    print(f"\n{PROJECT_NAME} ({PROJECT_SHORT_NAME}) v{__version__}")
    print("This software connects to and controls adult haptic/sex toy devices based on")
    print("your keyboard and mouse input while gaming. Intended for use only by adults")
    print("aged 18 or older.")
    try:
        answer = input("Type 'yes' to confirm you are 18 or older and continue: ").strip().lower()
    except EOFError:
        return False
    return answer == "yes"


async def main():
    """Age gate, then build a controller from src/core.py's module-level config/profiles and run it."""
    if not _confirm_age():
        print("Age not confirmed - exiting.")
        return
    controller = HapticsController(INTIFACE_WS, PROFILES)
    await controller.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")

# Run with:
#   python cli.py                (headless, hand-edited JSON configs)
#   python gui.py                (interactive: connect devices, configure, start/stop)
#
# Or if that doesn't work:
#   py cli.py
#
# Or full path example (edit to match your Python install + where you saved the file, if you are on windows you may have saved to onedrive in the cloud, I also had that issue):
#   & "C:\Path\To\python.exe" "C:\Path\To\cli.py"

# Happy Vibes
