#!/usr/bin/env python3
"""
setup.py

One-time interactive setup: checks for FFmpeg, installs the Python
dependencies, and walks you through building playlists.json if you don't
already have one. Run this once after cloning/downloading the repo, then
use sync_all.bat / sync_all.sh for every sync after that.

Usage:
    python setup.py
"""

from __future__ import annotations

import json
import shutil
import string
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
PLAYLISTS_FILE = SCRIPT_DIR / "playlists.json"
EXAMPLE_FILE = SCRIPT_DIR / "playlists.example.json"
REQUIREMENTS_FILE = SCRIPT_DIR / "requirements.txt"


def offer_install(display_name: str, winget_id: str, brew_formula: str, apt_pkg: str, url: str) -> bool:
    """Offers to install a missing command-line tool automatically (winget
    on Windows, Homebrew on Mac) rather than just telling the user to go
    do it themselves - this is the step most likely to stump someone with
    no coding background, so it's worth doing for them when we can."""
    manager = None
    if sys.platform == "win32" and shutil.which("winget"):
        manager = ["winget", "install", "-e", "--id", winget_id,
                   "--accept-source-agreements", "--accept-package-agreements"]
    elif sys.platform == "darwin" and shutil.which("brew"):
        manager = ["brew", "install", brew_formula]

    if manager is None:
        print(f"  Install it yourself from {url}", end="")
        if sys.platform.startswith("linux"):
            print(f", or: sudo apt install {apt_pkg}")
        else:
            print(".")
        return False

    answer = input(f"  Install {display_name} automatically now? (Y/n): ").strip().lower()
    if answer == "n":
        print(f"  Skipping. Install it yourself from {url} and re-run this setup.")
        return False

    print(f"  Installing {display_name}, this may take a minute...")
    result = subprocess.run(manager)
    if result.returncode != 0:
        print(f"  Install failed - install it yourself from {url}.")
        return False

    if shutil.which({"FFmpeg": "ffmpeg", "Deno": "deno"}.get(display_name, display_name.lower())):
        print(f"  {display_name} installed and found.")
        return True
    # A fresh install often isn't on PATH until a new terminal is opened -
    # this doesn't mean it failed, just that this same window can't see it yet.
    print(f"  {display_name} was installed, but this window can't see it yet.")
    print("  Close this window, open a new one, and run setup again to confirm.")
    return False


def check_ffmpeg() -> bool:
    print("Checking for FFmpeg...")
    if shutil.which("ffmpeg"):
        print("  Found.")
        return True
    print("  NOT FOUND. FFmpeg is required (yt-dlp uses it to extract audio).")
    return offer_install("FFmpeg", "Gyan.FFmpeg", "ffmpeg", "ffmpeg", "https://ffmpeg.org/download.html")


def check_deno() -> bool:
    print("Checking for Deno...")
    if shutil.which("deno"):
        print("  Found.")
        return True
    print("  NOT FOUND. yt-dlp uses Deno to solve YouTube's JS challenge on")
    print("  some videos - without it, some downloads silently fail.")
    return offer_install("Deno", "DenoLand.Deno", "deno", "unzip curl", "https://deno.com/")


def install_dependencies() -> bool:
    print("\nInstalling Python dependencies from requirements.txt...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)]
    )
    if result.returncode != 0:
        print("  pip install failed - see the output above.")
        return False
    print("  Done.")
    return True


def detect_removable_drives() -> list[str]:
    """Best-effort Windows drive-letter scan, just to help the user pick.
    Not exhaustive or guaranteed accurate - always allow free-text entry too.
    Skips A:/B: (floppy-era) and C: (almost always the system drive, never
    what someone means by a mirror target)."""
    if sys.platform != "win32":
        return []
    found = []
    for letter in string.ascii_uppercase[3:]:  # skip A:, B:, and C:
        path = Path(f"{letter}:/")
        if path.exists():
            found.append(f"{letter}:")
    return found


def prompt(question: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or (default or "")


def slugify(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_ " else "" for c in name).strip().replace(" ", "-").lower() or "playlist"


def build_playlists_json() -> None:
    print("\nNo playlists.json found - let's build one.")
    print("(You can always edit this file by hand later.)\n")

    drives = detect_removable_drives()
    if drives:
        print(f"Drive letters currently present: {', '.join(drives)}")
    print(
        "Where should the library be mirrored? A USB stick, an MP3 player, any\n"
        "folder - or leave this blank to keep it local-only (your synced music\n"
        "will still live in MusicLibrary/ either way)."
    )
    usb_path = prompt("Mirror target path (e.g. E:/Music, /Volumes/MYUSB, blank for none)")

    playlists = []
    print("\nNow enter your Tidal playlists one at a time.")
    while True:
        tidal_name = prompt("  Exact Tidal playlist name (blank to finish)")
        if not tidal_name:
            break
        default_local_name = slugify(tidal_name)
        local_name = prompt("  Local folder name for it", default=default_local_name)
        playlists.append({"tidal_name": tidal_name, "name": local_name})
        print(f"  Added '{tidal_name}' -> {local_name}/\n")

    if not playlists:
        print("No playlists entered - copying the example file as a starting point instead.")
        print("Edit playlists.json by hand before running a sync.")
        shutil.copy(EXAMPLE_FILE, PLAYLISTS_FILE)
        return

    codec = prompt("Audio format (mp3/vorbis)", default="mp3")

    config = {
        "_comment": "Edit this file whenever you want to add, remove, or rename a playlist.",
        "staging_path": "./MusicLibrary",
        "usb_drive_path": usb_path,
        "codec": codec,
        "playlists": playlists,
    }
    PLAYLISTS_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {PLAYLISTS_FILE.name}.")


def main() -> None:
    print("=" * 60)
    print("tidal-mp3-sync setup")
    print("=" * 60)

    ffmpeg_ok = check_ffmpeg()
    deno_ok = check_deno()
    deps_ok = install_dependencies()

    if PLAYLISTS_FILE.exists():
        print(f"\n{PLAYLISTS_FILE.name} already exists - leaving it as is.")
    else:
        build_playlists_json()

    print("\n" + "=" * 60)
    if ffmpeg_ok and deno_ok and deps_ok:
        print("Setup complete.")
        print("Next: run sync_all.bat (Windows) or ./sync_all.sh (Mac/Linux).")
        print("Plug in your mirror target first if you configured one.")
        print("First run will open a browser tab to log into Tidal")
        print("(read-only access) - after that it's saved locally.")
    else:
        print("Setup finished with warnings above - fix those before your first sync.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print("\n\nSetup cancelled - no input received. Run this again in an")
        print("interactive terminal, or set up playlists.json by hand instead")
        print("(see README.md).")
        sys.exit(1)
