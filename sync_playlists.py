#!/usr/bin/env python3
"""
sync_playlists.py

Downloads the tracks resolved by tidal_resolve.py (one text file of
YouTube video URLs per playlist, in resolved/) using yt-dlp, into a
per-playlist subfolder of your local staging folder, then mirrors that
staging folder onto your USB drive.

What it does each time you run it:
  1. Reads playlists.json for your playlist list and paths.
  2. For each playlist, runs yt-dlp against resolved/<playlist>.txt,
     extracting audio into staging/<playlist>/. yt-dlp keeps a per-
     playlist download-archive file so it skips anything already
     downloaded - as long as you've re-run tidal_resolve.py first to
     pick up anything new you've added on Tidal.
  3. Renames each file to "<Song> - <Artist>.ext" (no video ID visible)
     and deletes any file whose track is no longer in
     resolved/<playlist>.txt (i.e. a song you removed from that Tidal
     playlist). A small per-playlist index file (.track_index.json,
     alongside the audio files) tracks which file belongs to which
     YouTube video, so this works reliably without a cryptic ID visible
     in the filename itself. Playback order isn't preserved - if you
     shuffle anyway, this doesn't try to control file sort order.
  4. Mirrors the staging folder onto your USB drive (adds new files,
     removes files on the USB that no longer exist in staging).

Usage:
    python sync_playlists.py
    python sync_playlists.py --dry-run   (report what would happen, change nothing)

Edit playlists.json to add/remove playlists or change where things are saved.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Same fix as tidal_resolve.py - avoid crashing on track/artist names that
# don't fit Windows' legacy console codepage.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "playlists.json"
RESOLVED_DIR = SCRIPT_DIR / "resolved"
LOG_FILE = SCRIPT_DIR / "sync_log.txt"
LOCK_FILE = SCRIPT_DIR / ".sync.lock"


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        log(f"ERROR: {CONFIG_FILE} not found.")
        sys.exit(1)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        log(f"ERROR: {CONFIG_FILE.name} isn't valid JSON ({e}).")
        log("If you hand-edited it, check for a missing/extra comma or bracket.")
        sys.exit(1)
    if not isinstance(config, dict):
        log(f"ERROR: {CONFIG_FILE.name} should contain a JSON object ({{...}}), not a {type(config).__name__}.")
        sys.exit(1)
    return config


def check_ytdlp_installed() -> None:
    if shutil.which("yt-dlp") is None:
        log("ERROR: 'yt-dlp' was not found on your PATH.")
        log("Install it with: pip install -U yt-dlp")
        log("Also make sure ffmpeg is installed (needed to extract/convert audio).")
        sys.exit(1)


# Windows reserves these names for device files, regardless of extension
# (e.g. "CON.mp3" is just as invalid as "CON") - a playlist or track name
# that happens to be one of these would otherwise fail to create at all.
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", str(name or "")).strip()
    # Windows also silently strips trailing dots/spaces from a filename,
    # so "Song..." and "Song" can otherwise collide on disk without
    # looking like they would.
    cleaned = cleaned.rstrip(". ")
    if cleaned in ("", ".", "..") or cleaned.upper().split(".")[0] in _WINDOWS_RESERVED_NAMES:
        return "unnamed"
    return cleaned


OUTPUT_TEMPLATE = "%(id)s - %(uploader)s - %(title)s.%(ext)s"
# The raw name yt-dlp writes on first download: "<id> - Uploader - Title.ext".
# Only used to spot a freshly-downloaded file by its leading ID - the
# actual artist/title used for renaming comes from the .info.json
# sidecar (see below), not by parsing this string, since either field
# can itself legitimately contain " - " (e.g. YouTube's own
# "Some Artist - Topic" auto-channel naming).
RAW_ID_RE = re.compile(r'^([A-Za-z0-9_-]{11}) - ')
# Per-playlist sidecar mapping video_id -> current filename, so deletion-
# sync keeps working reliably once the ID is gone from the visible
# filename.
INDEX_FILENAME = ".track_index.json"


_VIDEO_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')


def video_id_from_url(url: str) -> str | None:
    """Accepts a youtube.com/watch?v=... URL (what tidal_resolve.py always
    writes) or a youtu.be/... short link (in case resolved/*.txt was ever
    hand-edited) - matches the URL forms fix_track.py itself accepts."""
    parsed = urlparse(url)
    vid = parse_qs(parsed.query).get("v", [None])[0]
    if vid and _VIDEO_ID_RE.match(vid):
        return vid
    if "youtu.be" in parsed.netloc:
        candidate = parsed.path.strip("/")
        if _VIDEO_ID_RE.match(candidate):
            return candidate
    return None


def sync_playlist(url_file: Path, name: str, root_path: Path, audio_format: str, dry_run: bool = False) -> bool:
    if not dry_run:
        root_path.mkdir(parents=True, exist_ok=True)
    archive_file = root_path / ".ytdlp_archive.txt"

    cmd = [
        "yt-dlp", "--no-playlist", "-x",
        # Lets yt-dlp fetch its official JS-challenge-solver component
        # (via Deno) from GitHub on demand - without this, some videos
        # fail signature solving and get skipped entirely.
        "--remote-components", "ejs:github",
        "--audio-format", audio_format,
        # YouTube's actual source audio tops out around 128-160kbps, so
        # re-encoding at a "best quality" VBR setting just wastes USB
        # space on fake precision. 160k matches the real ceiling.
        "--audio-quality", "160K",
        "--download-archive", str(archive_file),
        # Writes a "<id> - Uploader - Title.info.json" sidecar per track
        # with structured artist/title fields - used to rename files
        # reliably afterward instead of regex-guessing where the artist
        # name ends and the title begins in a combined string.
        "--write-info-json",
        "-o", str(root_path / OUTPUT_TEMPLATE),
        "-a", str(url_file),
    ]
    if dry_run:
        # yt-dlp's own no-op mode: reports what it would fetch (respecting
        # the download-archive, so already-downloaded tracks are still
        # skipped in the report) without downloading, converting, or
        # writing anything to disk.
        cmd.append("--simulate")

    log(f"{'[DRY RUN] ' if dry_run else ''}Syncing playlist: {name}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        log("ERROR: could not launch yt-dlp. Is it installed and on PATH?")
        return False

    # yt-dlp prints its own progress; only surface it on failure to keep
    # the log readable, plus a trailing snippet so you can see it worked.
    if result.returncode != 0:
        log(f"  WARNING: yt-dlp exited with code {result.returncode} for '{name}'")
        if result.stderr.strip():
            log(result.stderr.strip()[-2000:])
        return False

    log(f"  Done: {name}")
    return True


def atomic_write_text(path: Path, content: str) -> None:
    # Temp file + os.replace() (atomic on both Windows and POSIX), with a
    # PID-suffixed name so two concurrent runs can't clobber each other's
    # in-progress write, and cleanup even if os.replace() itself fails.
    tmp_file = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp_file.write_text(content, encoding="utf-8")
        os.replace(tmp_file, path)
    finally:
        tmp_file.unlink(missing_ok=True)


def load_track_index(playlist_root: Path) -> dict:
    idx_file = playlist_root / INDEX_FILENAME
    if idx_file.exists():
        try:
            data = json.loads(idx_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    str(k): str(v)
                    for k, v in data.items()
                    if isinstance(k, str) and isinstance(v, str) and v and "/" not in v and "\\" not in v
                }
            return {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_track_index(playlist_root: Path, index: dict) -> None:
    atomic_write_text(
        playlist_root / INDEX_FILENAME,
        json.dumps(index, indent=2, ensure_ascii=False),
    )


def clean_freshly_downloaded_files(playlist_root: Path, index: dict, dry_run: bool = False) -> int:
    """Renames any freshly-downloaded file (still in yt-dlp's raw
    "<id> - Uploader - Title.ext" naming) to a clean "Song - Artist.ext"
    with no video ID visible, using its .info.json sidecar for the exact
    artist/title fields - reading structured JSON instead of trying to
    regex-split "Uploader - Title" apart, which breaks when either field
    contains its own " - " (e.g. YouTube's "Some Artist - Topic" auto-
    channel naming). Matches each info.json to its audio file by the
    "id" field inside the JSON, not by filename pattern-matching, so the
    exact on-disk naming convention yt-dlp uses for the sidecar doesn't
    matter. Deletes the .info.json afterward - it's only needed once."""
    # Real YouTube IDs downloaded for this playlist - used below to guard
    # the fallback regex match, since an 11-character word followed by
    # " - " (e.g. an artist name like "Intimidated" or "Bittersweet" that
    # happens to be 11 characters) can otherwise be mistaken for a raw
    # "<id> - Uploader - Title" filename and get mangled/deleted.
    archive_file = playlist_root / ".ytdlp_archive.txt"
    known_ids = set()
    if archive_file.exists():
        for line in archive_file.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split()
            if len(parts) == 2:
                known_ids.add(parts[1])

    renamed = 0
    for info_file in playlist_root.glob("*.info.json"):
        try:
            info = json.loads(info_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(info, dict):
            continue
        vid = info.get("id")
        if not vid or not isinstance(vid, str) or vid in index:
            if not dry_run:
                info_file.unlink(missing_ok=True)
            continue

        # yt-dlp leaves behind .part/.ytdl/.temp files while a download is
        # still in progress or was interrupted - excluding only ".json"
        # let one of those get mistaken for the finished audio file,
        # silently indexing a broken fragment as if it were the real track.
        INCOMPLETE_SUFFIXES = (".json", ".part", ".ytdl", ".temp")
        audio_file = None
        for f in playlist_root.iterdir():
            if f.is_file() and f.suffix not in INCOMPLETE_SUFFIXES and f.name.startswith(f"{vid} - "):
                audio_file = f
                break
        if audio_file is None:
            continue  # audio download itself must have failed

        title_raw = info.get("title")
        title = safe_filename(title_raw if isinstance(title_raw, str) and title_raw.strip() else "Unknown Title")
        artist_raw = info.get("uploader")
        artist = safe_filename(artist_raw if isinstance(artist_raw, str) and artist_raw.strip() else "Unknown Artist")
        desired_name = f"{title} - {artist}{audio_file.suffix}"
        # Guard against two different tracks producing the same clean name.
        n = 2
        while (playlist_root / desired_name).exists() and (playlist_root / desired_name) != audio_file:
            desired_name = f"{title} - {artist} ({n}){audio_file.suffix}"
            n += 1

        if dry_run:
            log(f"  [DRY RUN] would rename to: {desired_name}")
        else:
            audio_file.rename(playlist_root / desired_name)
            index[vid] = desired_name
            info_file.unlink(missing_ok=True)
        renamed += 1

    # Fallback for files downloaded before this script wrote .info.json
    # sidecars - no structured data available, so best-effort split on
    # the first " - " after the ID. Correct for the vast majority of
    # uploader names; only wrong for the rare uploader name that itself
    # contains " - " (e.g. "Artist - Topic"), and only for this one-time
    # migration of pre-existing files - new downloads always go through
    # the reliable info.json path above.
    for f in list(playlist_root.iterdir()):
        if not f.is_file() or f.suffix == ".json" or f.name == INDEX_FILENAME:
            continue
        match = RAW_ID_RE.match(f.name)
        if not match:
            continue
        vid = match.group(1)
        if vid in index or vid not in known_ids:
            continue
        remainder = f.name[len(match.group(0)):]
        if " - " in remainder:
            artist, title_with_ext = remainder.split(" - ", 1)
        else:
            artist, title_with_ext = "Unknown Artist", remainder
        title = title_with_ext.rsplit(".", 1)[0] if "." in title_with_ext else title_with_ext
        title = safe_filename(title)
        artist = safe_filename(artist)
        desired_name = f"{title} - {artist}{f.suffix}"
        n = 2
        while (playlist_root / desired_name).exists() and (playlist_root / desired_name) != f:
            desired_name = f"{title} - {artist} ({n}){f.suffix}"
            n += 1
        if dry_run:
            log(f"  [DRY RUN] would rename to: {desired_name}")
        else:
            f.rename(playlist_root / desired_name)
            index[vid] = desired_name
        renamed += 1

    return renamed


def reorganize_playlist_files(url_file: Path, playlist_root: Path, name: str, dry_run: bool = False) -> None:
    """Deletes any file whose track is no longer in
    resolved/<playlist>.txt (i.e. a song removed from that Tidal
    playlist), and renames freshly-downloaded files to a clean
    "Song - Artist.ext" with no video ID visible. Tracks video_id ->
    filename in a small per-playlist index file (.track_index.json) so
    deletion-sync keeps working reliably once the ID is no longer part
    of the filename - only ever touches files it put there or already
    knows about, so nothing else in the folder is at risk. Safe to
    re-run."""
    if not playlist_root.exists():
        return

    desired_ids = set()
    for line in url_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        vid = video_id_from_url(line)
        if vid:
            desired_ids.add(vid)

    index = load_track_index(playlist_root)
    renamed = clean_freshly_downloaded_files(playlist_root, index, dry_run=dry_run)

    removed_ids = set()
    for vid in list(index.keys()):
        if vid not in desired_ids:
            removed_ids.add(vid)
            if dry_run:
                continue
            f = playlist_root / index[vid]
            if f.exists():
                f.unlink()
            del index[vid]
    if removed_ids:
        prefix = "[DRY RUN] Would remove" if dry_run else "Removed"
        log(f"  {prefix} {len(removed_ids)} track(s) from '{name}' no longer in the Tidal playlist")
        if not dry_run:
            # Also drop these from yt-dlp's own download-archive - otherwise if
            # the same track is ever re-added to the playlist later, yt-dlp
            # would see its video ID already marked "downloaded" and silently
            # skip it forever, even though the file was just deleted.
            archive_file = playlist_root / ".ytdlp_archive.txt"
            if archive_file.exists():
                lines = archive_file.read_text(encoding="utf-8").splitlines()
                kept = []
                for line in lines:
                    parts = line.strip().split()
                    if len(parts) == 2 and parts[1] in removed_ids:
                        continue  # this line's video ID was just removed, drop it
                    kept.append(line)
                atomic_write_text(archive_file, "\n".join(kept) + ("\n" if kept else ""))

    if not dry_run:
        save_track_index(playlist_root, index)
    if renamed:
        prefix = "[DRY RUN] Would clean up" if dry_run else "Cleaned up"
        log(f"  {prefix} {renamed} filename(s) in '{name}'")


def mirror_to_usb(staging_path: Path, usb_path: Path, dry_run: bool = False) -> bool:
    """Mirror staging_path onto usb_path (copies new files, removes files
    on the USB that no longer exist in staging)."""

    if not usb_path.exists():
        log(f"ERROR: USB path '{usb_path}' was not found. Is the drive plugged in "
            f"and is the path/drive letter in playlists.json correct?")
        return False

    log(f"{'[DRY RUN] ' if dry_run else ''}Mirroring {staging_path} -> {usb_path}")

    system = platform.system()
    try:
        if system == "Windows":
            # /MIR mirrors the tree (adds new, removes stale). Exit codes
            # 0-7 from robocopy mean success; 8+ means a real error. /L is
            # robocopy's own list-only mode for a dry run - reports what
            # would change without touching anything.
            cmd = ["robocopy", str(staging_path), str(usb_path), "/MIR", "/NFL", "/NDL"]
            if dry_run:
                cmd.append("/L")
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            ok = result.returncode < 8
        else:
            # macOS / Linux. --dry-run is rsync's own no-op mode.
            cmd = ["rsync", "-a", "--delete", f"{staging_path}/", f"{usb_path}/"]
            if dry_run:
                cmd.append("--dry-run")
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            ok = result.returncode == 0

        if not ok:
            log(f"  WARNING: mirror step exited with code {result.returncode}")
            if result.stderr.strip():
                log(result.stderr.strip()[-2000:])
            return False

    except FileNotFoundError as e:
        log(f"ERROR: mirror tool not found ({e}). On Windows this needs 'robocopy' "
            f"(built into Windows). On Mac/Linux this needs 'rsync' installed.")
        return False

    log("  Mirror complete.")
    return True


def main() -> None:
    # A second run started while one is already in progress would load the
    # same resolve_cache.json/.track_index.json, make its own changes, and
    # overwrite the other's - the atomic writes prevent file corruption but
    # not this kind of lost update. A plain lock file is enough for a
    # personal single-user tool; if a previous run crashed without cleaning
    # up, delete the lock file and try again.
    # "x" mode is an atomic create-if-not-exists (fails with FileExistsError
    # if the file is already there) - checking existence and creating it as
    # two separate steps would leave a gap where two processes started at
    # nearly the same time could both believe they got the lock.
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        log(f"ERROR: {LOCK_FILE.name} already exists - another sync may already be "
            f"running. If a previous run crashed without cleaning up, delete "
            f"{LOCK_FILE} and try again.")
        sys.exit(1)
    os.write(fd, str(os.getpid()).encode("utf-8"))
    os.close(fd)
    try:
        _run()
    finally:
        LOCK_FILE.unlink(missing_ok=True)


def _run() -> None:
    dry_run = "--dry-run" in sys.argv[1:]
    check_ytdlp_installed()
    config = load_config()

    staging_path_raw = config.get("staging_path")
    if not staging_path_raw or not isinstance(staging_path_raw, str) or not staging_path_raw.strip():
        log(f"ERROR: {CONFIG_FILE.name} is missing the required 'staging_path' field.")
        sys.exit(1)
    staging_path = Path(staging_path_raw.strip()).expanduser().resolve()
    usb_drive_path_val = config.get("usb_drive_path")
    usb_drive_path_raw = str(usb_drive_path_val).strip() if (usb_drive_path_val and isinstance(usb_drive_path_val, str)) else ""
    codec_val = config.get("codec", "mp3")
    audio_format = str(codec_val).strip() if (codec_val and isinstance(codec_val, str)) else "mp3"
    playlists = config.get("playlists", [])

    if not isinstance(playlists, list) or not all(isinstance(p, dict) for p in playlists):
        log(f'ERROR: {CONFIG_FILE.name}\'s "playlists" list should contain objects ({{...}}), not plain values.')
        sys.exit(1)
    if not playlists:
        log("No playlists configured in playlists.json. Nothing to do.")
        return

    # Two differently-named playlists can sanitize to the same folder/file
    # name (e.g. "A/B" and "A:B" both become "A_B") - letting that through
    # would mean they silently share a resolved-tracks file and a download
    # folder, so one's sync can act on the other's tracks.
    seen_names: dict[str, str] = {}
    for playlist in playlists:
        raw_name = str(playlist.get("name") or playlist.get("tidal_name") or "").strip()
        if not raw_name:
            continue
        # casefold(), not just the raw sanitized string, because NTFS and
        # macOS's default filesystem are both case-insensitive - "Foo" and
        # "foo" would otherwise pass this check but collide on disk anyway.
        key = safe_filename(raw_name).casefold()
        if key in seen_names and seen_names[key] != raw_name:
            log(f"ERROR: playlist names '{seen_names[key]}' and '{raw_name}' both "
                f"resolve to the same folder/file on disk. Rename one in "
                f"{CONFIG_FILE.name}.")
            sys.exit(1)
        seen_names[key] = raw_name

    if not dry_run:
        staging_path.mkdir(parents=True, exist_ok=True)

    log("=" * 60)
    if dry_run:
        log("DRY RUN - no files will be downloaded, deleted, renamed, or mirrored")
    log(f"Starting sync of {len(playlists)} playlist(s)")
    log(f"Staging folder: {staging_path}")
    log(f"Audio format: {audio_format}")

    successes = 0
    for playlist in playlists:
        name = str(playlist.get("name") or playlist.get("tidal_name") or "").strip()
        if not name:
            log(f"SKIPPING invalid playlist entry in {CONFIG_FILE.name}: missing 'name'.")
            continue
        url_file = RESOLVED_DIR / f"{safe_filename(name)}.txt"
        if not url_file.exists() or not url_file.read_text(encoding="utf-8").strip():
            log(f"SKIPPING '{name}': no resolved tracks yet. Run tidal_resolve.py first.")
            continue
        playlist_root = staging_path / safe_filename(name)
        if sync_playlist(url_file, name, playlist_root, audio_format, dry_run=dry_run):
            successes += 1
        reorganize_playlist_files(url_file, playlist_root, name, dry_run=dry_run)

    log(f"Download step done: {successes}/{len(playlists)} playlists synced.")

    if usb_drive_path_raw:
        mirror_to_usb(staging_path, Path(usb_drive_path_raw).expanduser(), dry_run=dry_run)
    else:
        log(f"No usb_drive_path configured - your library lives in {staging_path},")
        log("skipping the mirror step. Set usb_drive_path in playlists.json to also")
        log("copy it to a USB stick, MP3 player, or any other folder.")

    log("All done.")
    log("=" * 60)


if __name__ == "__main__":
    main()
