#!/usr/bin/env python3
"""
fix_track.py

Corrects a single track's YouTube match - whether tidal_resolve.py picked
the wrong version (wrong language, live take, dead/removed video) or never
found a match at all (common for a track that's brand new and not yet in
YouTube Music's clean catalog).

Looks the track up directly on your Tidal playlist (not just the local
files), shows you its current status, and lets you paste a replacement
link - then automatically checks that link's real length against Tidal's
actual track length before accepting it, the same sanity check you'd do
by hand. Updates resolve_cache.json and resolved/<playlist>.txt so the fix
sticks on every future sync, not just this one.

Usage:
    python fix_track.py <playlist name> <search text>
    python fix_track.py                    (interactive - asks for both)

Example:
    python fix_track.py workout "song title"

After running this, do a normal sync (sync_all.bat / sync_all.sh) to
download the corrected track - any old wrong file is removed
automatically, the same way a track removed from Tidal would be.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "playlists.json"

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# A pasted link whose real length differs from Tidal's by more than this
# many seconds gets a loud warning before being accepted - same threshold
# tidal_resolve.py itself uses to reject an automated match.
MAX_DURATION_DELTA = 12


def video_id_from_input(text: str) -> str | None:
    """Accepts a full YouTube URL (youtube.com or youtu.be, including the
    music.youtube.com form) or a bare 11-character video ID."""
    text = text.strip()
    if VIDEO_ID_RE.match(text):
        return text
    parsed = urlparse(text)
    vid = parse_qs(parsed.query).get("v", [None])[0]
    if vid and VIDEO_ID_RE.match(vid):
        return vid
    if "youtu.be" in parsed.netloc:
        candidate = parsed.path.strip("/")
        if VIDEO_ID_RE.match(candidate):
            return candidate
    return None


def check_real_duration(video_id: str, expected_seconds: int | None) -> None:
    """Fetches the pasted video's real length via yt-dlp and warns loudly
    if it doesn't match Tidal's actual track length - this is the exact
    check worth doing by hand before trusting a manually-picked link, done
    automatically instead."""
    if expected_seconds is None:
        return
    print("Checking the link's actual length against Tidal's track length...")
    try:
        result = subprocess.run(
            ["yt-dlp", "--print", "%(duration)s|%(title)s", f"https://www.youtube.com/watch?v={video_id}"],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("  Couldn't check (yt-dlp not available or timed out) - proceeding without verifying.")
        return
    line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if "|" not in line:
        print("  Couldn't read that video's length - proceeding without verifying.")
        return
    duration_str, title = line.split("|", 1)
    try:
        real_seconds = int(float(duration_str))
    except ValueError:
        print("  Couldn't read that video's length - proceeding without verifying.")
        return

    delta = abs(real_seconds - expected_seconds)
    print(f"  '{title}' is {real_seconds}s long; Tidal's track is {expected_seconds}s.")
    if delta > MAX_DURATION_DELTA:
        print(f"  WARNING: that's {delta}s different - this might be the wrong version")
        print("  (a remix, live take, or different edit). Double-check before continuing.")
        confirm = input("  Use it anyway? (y/N): ").strip().lower()
        if confirm != "y":
            print("Cancelled, nothing changed.")
            sys.exit(0)
    else:
        print("  Looks right.")


def main() -> None:
    if len(sys.argv) >= 3:
        playlist_name = sys.argv[1]
        search_text = " ".join(sys.argv[2:])
    elif len(sys.argv) == 1:
        playlist_name = input("Playlist name (as in playlists.json): ").strip()
        search_text = input("Song name or part of it to search for: ").strip()
    else:
        print(f"Usage: python {Path(sys.argv[0]).name} <playlist name> <search text>")
        print(f"   or: python {Path(sys.argv[0]).name}   (interactive)")
        sys.exit(1)

    if not playlist_name or not search_text:
        print("Need both a playlist name and search text.")
        sys.exit(1)

    if not CONFIG_FILE.exists():
        print(f"ERROR: {CONFIG_FILE.name} not found. Run setup.py first.")
        sys.exit(1)
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        print(f"ERROR: {CONFIG_FILE.name} isn't valid JSON ({e}).")
        print("If you hand-edited it, check for a missing/extra comma or bracket.")
        sys.exit(1)
    if not isinstance(config, dict):
        print(f"ERROR: {CONFIG_FILE.name} should contain a JSON object ({{...}}), not a {type(config).__name__}.")
        sys.exit(1)
    playlists = config.get("playlists", [])
    if not isinstance(playlists, list) or not all(isinstance(p, dict) for p in playlists):
        print(f'ERROR: {CONFIG_FILE.name}\'s "playlists" list should contain objects ({{...}}), not plain values.')
        sys.exit(1)
    playlist_entry = next(
        (
            p for p in playlists
            if str(p.get("name") or "").strip().lower() == playlist_name.lower()
            or str(p.get("tidal_name") or "").strip().lower() == playlist_name.lower()
        ),
        None,
    )
    if playlist_entry is None:
        print(f"ERROR: no playlist named '{playlist_name}' in playlists.json.")
        print("Check the 'name' field for each playlist in that file.")
        sys.exit(1)
    tidal_name = str(playlist_entry.get("tidal_name") or playlist_entry.get("name") or "").strip()
    if not tidal_name:
        print(f"ERROR: the '{playlist_name}' entry in playlists.json has no 'tidal_name' field.")
        sys.exit(1)
    local_name = str(playlist_entry.get("name") or tidal_name).strip()

    # Imported lazily so a user who only wants local-file lookups (none
    # currently, but kept for a cheap, honest error if tidalapi is
    # missing) gets a clear message rather than an import crash at the
    # top of the file. Reuses tidal_resolve.py's own login/cache/matching
    # helpers rather than re-implementing them, so there's one source of
    # truth for how a track's cache key and duration are determined.
    try:
        from tidal_resolve import (
            tidal_login, find_tidal_playlist, cache_key,
            load_cache, save_cache, CACHE_FILE, RESOLVED_DIR, safe_filename,
        )
    except ImportError as e:
        print(f"ERROR: couldn't load tidal_resolve.py's helpers ({e}).")
        print("Run: pip install -r requirements.txt")
        sys.exit(1)

    session = tidal_login()
    tidal_playlist = find_tidal_playlist(session, tidal_name)
    if tidal_playlist is None:
        print(f"ERROR: couldn't find a Tidal playlist named '{tidal_name}'.")
        sys.exit(1)

    tracks = tidal_playlist.tracks()
    search_lower = search_text.lower()
    matches = [
        t for t in tracks
        if search_lower in t.name.lower()
        or (getattr(t, "artist", None) and search_lower in t.artist.name.lower())
    ]
    if not matches:
        print(f"No tracks matching '{search_text}' found in the '{tidal_name}' Tidal playlist.")
        sys.exit(1)

    cache = load_cache()

    if len(matches) > 1:
        print(f"Found {len(matches)} matching tracks:")
        for i, t in enumerate(matches, 1):
            artist = t.artist.name if getattr(t, "artist", None) else "?"
            print(f"  {i}. {t.name} - {artist}")
        choice = input("Which one? (number): ").strip()
        try:
            choice_num = int(choice)
            if choice_num < 1 or choice_num > len(matches):
                raise ValueError
            track = matches[choice_num - 1]
        except ValueError:
            print("Not a valid choice, cancelled.")
            sys.exit(1)
    else:
        track = matches[0]

    artist_name = track.artist.name if getattr(track, "artist", None) else ""
    isrc = getattr(track, "isrc", None)
    duration = getattr(track, "duration", None)
    key = cache_key(isrc, track.name, artist_name)
    cached = cache.get(key)
    old_id = cached.get("video_id") if (cached and isinstance(cached, dict)) else None

    print(f"\nTrack: {track.name} - {artist_name}")
    if duration:
        mins, secs = divmod(duration, 60)
        print(f"Tidal's track length: {mins}:{secs:02d}")
    if old_id:
        print(f"Currently matched to: https://www.youtube.com/watch?v={old_id}")
    else:
        print("Currently: not matched to anything (no downloaded file for this track).")

    print("\nIf you're not sure which link is right: search for the song on")
    print("music.youtube.com rather than regular YouTube - it's the same catalog")
    print("this tool searches automatically, so you're less likely to grab an MV,")
    print("remix, or live version by accident. A regular youtube.com or youtu.be")
    print("link works too, just double-check it's the actual studio version.")
    new_input = input("\nPaste the correct link or video ID (blank to cancel): ").strip()
    if not new_input:
        print("Cancelled, nothing changed.")
        sys.exit(0)

    new_id = video_id_from_input(new_input)
    if not new_id:
        print(f"ERROR: couldn't find a video ID in '{new_input}'.")
        sys.exit(1)
    if new_id == old_id:
        print("That's the same video ID that's already set - nothing to change.")
        sys.exit(0)

    check_real_duration(new_id, duration)

    cache[key] = {"video_id": new_id, "confidence": "high"}
    save_cache(cache)
    print(f"\nUpdated {CACHE_FILE.name}.")

    resolved_file = RESOLVED_DIR / f"{safe_filename(local_name)}.txt"
    new_url = f"https://www.youtube.com/watch?v={new_id}"
    if resolved_file.exists():
        lines = resolved_file.read_text(encoding="utf-8").splitlines()
        old_url = f"https://www.youtube.com/watch?v={old_id}" if old_id else None
        if old_url and old_url in lines:
            lines = [new_url if line == old_url else line for line in lines]
            print(f"Updated {resolved_file.name} (replaced the old link).")
        elif new_url not in lines:
            lines.append(new_url)
            print(f"Updated {resolved_file.name} (added the new link - this track had never matched before).")
        resolved_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        RESOLVED_DIR.mkdir(exist_ok=True)
        resolved_file.write_text(new_url + "\n", encoding="utf-8")
        print(f"Created {resolved_file.name}.")

    print("\nDone. Run sync_all.bat / sync_all.sh next to download the corrected")
    print("track - any old wrong file is removed automatically, same as any")
    print("track that's no longer in the playlist.")


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        sys.exit(1)
