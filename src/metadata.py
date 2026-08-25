"""Project identity - name, repo URL, website, and author, read by the
engine's startup banner, cli.py, and gui.py's About tab.

See src/version.py for the version number itself.
"""

PROJECT_NAME = "The Intiface Game Haptics Controller"
PROJECT_SHORT_NAME = "TIGHC"
REPO_URL = "https://github.com/StuxieDev/TIGHC"
WEBSITE_URL = "https://tighc.stuxie.dev"
AUTHOR_NAME = "StuxieDev"
AUTHOR_URL = "https://github.com/StuxieDev"


if __name__ == "__main__":
    print(f"{__file__} is TIGHC's project-identity module - it's a library, not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")
