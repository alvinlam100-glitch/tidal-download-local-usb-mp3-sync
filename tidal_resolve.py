#!/usr/bin/env python3
"""
tidal_resolve.py

Reads your Tidal playlists (by name) and resolves each track to a matching
YouTube Music video, so sync_playlists.py (which downloads via yt-dlp)
knows exactly what to pull for each playlist.

This only READS from Tidal, it never downloads audio or writes anything
back to Tidal - the same lightweight, read-only access as browsing the
app in a web client.

Matching approach:
  - Each Tidal track is searched on YouTube Music (ytmusicapi's "songs"
    filter, which favors official uploads over live/remix/user videos).
  - Candidates are scored on duration closeness (a wrong version is
    almost always a different length) plus title/artist text similarity.
    Anything without a close-enough duration match is rejected rather
    than guessed at.
  - Anything that couldn't be matched with confidence gets listed clearly
    at the end so you can add it manually if you care about that track.

Output:
  - Writes one text file per playlist into resolved/<playlist_name>.txt,
    containing one YouTube video URL per line. sync_playlists.py reads
    these files and hands them to yt-dlp.

Usage:
    python tidal_resolve.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

# Windows redirects console output through a legacy codepage (e.g. cp1252)
# by default, which can't represent every character that shows up in real
# track/artist names (accents, non-Latin scripts, etc). Force UTF-8 so a
# track name never crashes the whole run.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import tidalapi
except ImportError:
    print("ERROR: the 'tidalapi' package isn't installed. Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    from ytmusicapi import YTMusic
except ImportError:
    print("ERROR: the 'ytmusicapi' package isn't installed. Run: pip install -r requirements.txt")
    sys.exit(1)

import requests


class TimeoutSession(requests.Session):
    """A request with no timeout can hang the whole script indefinitely
    on a stuck connection. Every request through this session gets a
    hard timeout so that instead raises an exception, which the existing
    retry logic already handles."""
    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", 15)
        return super().request(*args, **kwargs)

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "playlists.json"
TIDAL_TOKEN_FILE = SCRIPT_DIR / "tidal_token.json"
RESOLVED_DIR = SCRIPT_DIR / "resolved"
CACHE_FILE = SCRIPT_DIR / "resolve_cache.json"

# A candidate whose duration differs from Tidal's by more than this many
# seconds is rejected outright - a wrong length almost always means a
# wrong version (live take, remix, extended edit, etc).
MAX_DURATION_DELTA = 12
# Below this, a match is treated as "high confidence" and not flagged.
TIGHT_DURATION_DELTA = 6
TITLE_SIMILARITY_FLOOR = 0.4


def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def cache_key(isrc: str | None, title: str, artist: str) -> str:
    return f"isrc:{isrc}" if isrc else f"ta:{title.strip().lower()}|{artist.strip().lower()}"


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"ERROR: {CONFIG_FILE} not found.")
        sys.exit(1)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: {CONFIG_FILE.name} isn't valid JSON ({e}).")
        print("If you hand-edited it, check for a missing/extra comma or bracket.")
        sys.exit(1)
    if not isinstance(config, dict):
        print(f"ERROR: {CONFIG_FILE.name} should contain a JSON object ({{...}}), not a {type(config).__name__}.")
        sys.exit(1)
    return config


def tidal_login() -> "tidalapi.Session":
    """Logs into Tidal, reusing a saved token if we have one so this
    doesn't ask you to open a browser link every single run."""
    session = tidalapi.Session()

    if TIDAL_TOKEN_FILE.exists():
        try:
            with open(TIDAL_TOKEN_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            session.load_oauth_session(
                saved["token_type"], saved["access_token"],
                saved["refresh_token"], saved["expiry_time"],
            )
            if session.check_login():
                print("Logged into Tidal using saved session.")
                return session
        except Exception:
            pass  # fall through to a fresh login

    print("Opening a Tidal login link, please open it and approve access:", flush=True)
    try:
        session.login_oauth_simple()
    except TimeoutError:
        print("ERROR: Tidal login timed out. Run the script again to retry.")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Tidal login failed ({e}).")
        sys.exit(1)

    with open(TIDAL_TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "token_type": session.token_type,
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            "expiry_time": session.expiry_time.isoformat() if hasattr(session.expiry_time, "isoformat") else session.expiry_time,
        }, f)

    return session


def text_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


_BRACKETED_RE = re.compile(r'[\(\[][^)\]]*[\)\]]')
_TRAILING_FEAT_RE = re.compile(r'\s+(feat|ft)\.?\s+.*$', re.IGNORECASE)
_DASH_SUFFIX_RE = re.compile(r'\s+-\s+.+$')


def title_variants(title: str) -> list:
    """Returns several normalized forms of a title - original, with
    brackets/feat-clauses stripped, and with a trailing ' - Subtitle'
    also stripped. Which stripping helps depends on which side of a
    dash/bracket the actually-matching text sits on (e.g. YouTube's
    '因為愛情 - Because Of Love' needs the suffix stripped to match a bare
    Chinese title, but 'Sky (Edit)' vs '海闊天空 - Sky (Edit)' needs the
    OPPOSITE - stripping the suffix there throws away the one part that
    matches). Rather than guess which applies, comparison tries every
    variant and keeps the best score."""
    variants = {title.strip()}
    no_brackets = _BRACKETED_RE.sub('', title)
    no_brackets = _TRAILING_FEAT_RE.sub('', no_brackets).strip()
    if no_brackets:
        variants.add(no_brackets)
    no_dash = _DASH_SUFFIX_RE.sub('', no_brackets or title).strip()
    if no_dash:
        variants.add(no_dash)
    return list(variants)


def best_title_similarity(a: str, b: str) -> float:
    return max(
        text_similarity(va, vb)
        for va in title_variants(a)
        for vb in title_variants(b)
    )


# Generic words that describe the *category* of a bracketed tag but don't
# identify a specific version - stripped before comparing, so what's left
# is the actual distinguishing name/descriptor (e.g. "Krono" out of
# "Krono Remix", or "Accapella" out of "JJ Flores & Steve Smooth
# Accapella").
_GENERIC_VERSION_WORDS = {
    "feat", "ft", "remix", "mix", "edit", "version", "radio", "extended",
    "original", "the", "and", "with", "of", "vs", "a",
}


def version_tags(title: str) -> set:
    """Extracts the significant/identifying words found inside this
    title's brackets - e.g. {'krono'} from 'Dancin (Krono Remix)'. Used
    to catch a specific failure mode that plain text-similarity misses:
    two titles that reduce to the same bare song name after stripping
    brackets (needed to match benign subtitles/credits) can still be
    genuinely different recordings - a named remix, an acapella, a
    specific live version - and that's exactly the info this recovers."""
    tags = set()
    for m in re.finditer(r'[\(\[]([^)\]]*)[\)\]]', title):
        words = re.findall(r"[A-Za-z']+", m.group(1).lower())
        tags.update(w for w in words if w not in _GENERIC_VERSION_WORDS and len(w) > 2)
    return tags


def has_version_conflict(target_title: str, candidate_title: str) -> bool:
    """True if both titles name a specific version/remix/edit and those
    names don't overlap at all - e.g. target wants 'Krono Remix' and the
    candidate is a 'JJ Flores & Steve Smooth Accapella'. Duration alone
    can miss this if the two versions happen to run a similar length."""
    target_tags = version_tags(target_title)
    candidate_tags = version_tags(candidate_title)
    if not target_tags or not candidate_tags:
        return False  # can't prove a conflict when one side is unlabeled
    return target_tags.isdisjoint(candidate_tags)


# Candidates whose title contains any of these are never the actual song
# itself, regardless of how well duration/title happen to line up -
# dance practice videos, lyric videos, workout mixes etc are a different
# category of content that occasionally coincides in length by chance.
_EXCLUDE_TITLE_RE = re.compile(
    r'\b(dance practice|lyrics?( video)?|reaction|workout|tutorial|karaoke|'
    r'instrumental|piano cover|cover\)|\(cover|8d audio|1 hour|hour loop|'
    r'sped up|nightcore|a\s*cappella|a\s*capella)\b',
    re.IGNORECASE,
)


def _search(yt: "YTMusic", query: str, filter_type: str, retries: int) -> list:
    for attempt in range(retries):
        try:
            return yt.search(query, filter=filter_type, limit=15) or []
        except Exception as e:
            wait = min(2 * (attempt + 1), 10)
            print(f"    (search error: {e}, retrying in {wait}s)", flush=True)
            time.sleep(wait)
    return []


def yt_search_best_match(yt: "YTMusic", title: str, artist: str, target_duration: int | None,
                          retries: int = 3) -> tuple:
    """Returns (video_id, confidence) where confidence is 'high', 'low'
    (matched but worth a manual glance), or None (video_id is also None)
    if nothing cleared the bar. Never guesses past MAX_DURATION_DELTA.

    Searches both YouTube Music's curated 'songs' catalog (cleaner, but
    often thin for brand-new or niche releases) and the broader 'videos'
    catalog (noisier, but usually has the real official upload even when
    the structured catalog doesn't yet), then scores every candidate from
    both pools together - a mediocre 'songs' hit no longer blocks a much
    better 'videos' one from being considered."""
    query = f"{artist} {title}".strip()

    candidates = []  # (result_dict, source_filter)
    for filter_type in ("songs", "videos"):
        for r in _search(yt, query, filter_type, retries):
            candidates.append((r, filter_type))

    best = None
    best_source = None
    best_score = -1.0
    best_duration_delta = None
    best_title_sim = None

    for r, source in candidates:
        video_id = r.get("videoId")
        raw_title = r.get("title", "")
        if not video_id or _EXCLUDE_TITLE_RE.search(raw_title):
            continue

        duration_seconds = r.get("duration_seconds")
        if duration_seconds is None:
            duration_seconds = _parse_duration_string(r.get("duration"))

        if target_duration is not None and duration_seconds is not None:
            duration_delta = abs(duration_seconds - target_duration)
            if duration_delta > MAX_DURATION_DELTA:
                continue  # wrong length - almost certainly the wrong version
        else:
            duration_delta = MAX_DURATION_DELTA  # unknown duration, treat as borderline

        title_sim = best_title_similarity(title, raw_title)
        artist_names = " ".join((a.get("name", "") if isinstance(a, dict) else str(a)) for a in (r.get("artists", []) or []))
        artist_sim = text_similarity(artist, artist_names)

        if title_sim < TITLE_SIMILARITY_FLOOR:
            continue  # title text is too different to trust regardless of duration

        if has_version_conflict(title, raw_title):
            continue  # e.g. target wants "Krono Remix", this is a named
            # different remix/acapella/edit - stripping brackets to match
            # the bare song name made them look similar, but they're not

        # A curated 'songs' catalog entry is inherently more trustworthy
        # than a raw 'videos' hit at the same score, so give it a small
        # edge rather than letting result order alone decide ties.
        score = (MAX_DURATION_DELTA - duration_delta) + title_sim * 6 + artist_sim * 4
        if source == "songs":
            score += 0.5
        if score > best_score:
            best_score = score
            best = video_id
            best_source = source
            best_duration_delta = duration_delta
            best_title_sim = title_sim

    if best is None:
        return None, None

    high_confidence = (
        best_source == "songs"
        and best_duration_delta <= TIGHT_DURATION_DELTA
        and best_title_sim >= 0.6
    )
    return best, ("high" if high_confidence else "low")


def _parse_duration_string(value) -> int | None:
    """Parses YouTube Music's 'M:SS' / 'H:MM:SS' duration strings."""
    if not value or not isinstance(value, str):
        return None
    parts = value.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def find_tidal_playlist(session: "tidalapi.Session", name: str):
    for playlist in session.user.playlists():
        if playlist.name.strip().lower() == name.strip().lower():
            return playlist
    return None


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", str(name or "")).strip()
    return cleaned or "unnamed"


def resolve_one_playlist(tidal_session, yt: "YTMusic", tidal_name: str, local_name: str, cache: dict) -> None:
    print(f"\n--- {tidal_name} ---")

    tidal_playlist = find_tidal_playlist(tidal_session, tidal_name)
    if tidal_playlist is None:
        print(f"  Could not find a Tidal playlist named '{tidal_name}'. "
              f"Check the spelling in playlists.json against Tidal.")
        return

    tidal_tracks = tidal_playlist.tracks()
    print(f"  {len(tidal_tracks)} tracks on Tidal")

    resolved_urls = []
    unmatched = []
    cache_hits = 0
    for i, track in enumerate(tidal_tracks, 1):
        isrc = getattr(track, "isrc", None)
        artist_name = track.artist.name if getattr(track, "artist", None) else ""
        duration = getattr(track, "duration", None)
        key = cache_key(isrc, track.name, artist_name)

        cached = cache.get(key)
        if cached is not None and isinstance(cached, dict):
            cache_hits += 1
            if cached.get("video_id"):
                resolved_urls.append(f"https://www.youtube.com/watch?v={cached['video_id']}")
            else:
                unmatched.append(f"{track.name} - {artist_name}")
            continue

        print(f"  [{i}/{len(tidal_tracks)}] {track.name} - {artist_name}", flush=True)
        video_id, confidence = yt_search_best_match(yt, track.name, artist_name, duration)

        cache[key] = {"video_id": video_id, "confidence": confidence}
        save_cache(cache)  # save incrementally so progress survives an interruption

        if video_id:
            resolved_urls.append(f"https://www.youtube.com/watch?v={video_id}")
            if confidence == "low":
                print(f"  (low-confidence match, worth double-checking) {track.name} - {artist_name}")
        else:
            unmatched.append(f"{track.name} - {artist_name}")

    if cache_hits:
        print(f"  ({cache_hits} track(s) already resolved from a previous run, skipped)")

    RESOLVED_DIR.mkdir(exist_ok=True)
    out_file = RESOLVED_DIR / f"{safe_filename(local_name)}.txt"
    out_file.write_text("\n".join(resolved_urls) + "\n" if resolved_urls else "", encoding="utf-8")

    print(f"  Matched {len(resolved_urls)}/{len(tidal_tracks)} tracks -> {out_file.name}")

    if unmatched:
        print(f"  Could not match {len(unmatched)} track(s) with confidence:")
        for t in unmatched:
            print(f"    - {t}")


def main() -> None:
    config = load_config()
    playlists = config.get("playlists", [])
    if not isinstance(playlists, list) or not all(isinstance(p, dict) for p in playlists):
        print(f'ERROR: {CONFIG_FILE.name}\'s "playlists" list should contain objects ({{...}}), not plain values.')
        sys.exit(1)
    if not playlists:
        print("No playlists configured in playlists.json.")
        return

    tidal_session = tidal_login()
    yt = YTMusic(requests_session=TimeoutSession())  # anonymous - no Google account needed, search-only
    cache = load_cache()

    for entry in playlists:
        tidal_name = str(entry.get("tidal_name") or entry.get("name") or "").strip()
        local_name = str(entry.get("name") or tidal_name).strip()
        if not tidal_name:
            print(f"  Skipping invalid playlist entry in {CONFIG_FILE.name}: missing 'tidal_name'.")
            continue
        resolve_one_playlist(tidal_session, yt, tidal_name, local_name, cache)

    print("\nTidal resolve done. Now run sync_playlists.py (or sync_all) to download and sync to USB.")


if __name__ == "__main__":
    main()
