# HANDOFF - tidal-mp3-sync GUI + deep bug fix

Branch: `claude/ytmusic-rip-main-sg08aq`. Keep this file current; if work
stops (out of budget), the next session starts here.

## User's requirements (verbatim intent)
1. A **real GUI** (not terminal-looking) packaged as a **Windows .exe**
   (not "some python thing").
2. GUI buttons: **debug / dry-run sync**, **manual add new song**,
   **manual download** (paste a link, download one song).
3. **"Unsure" handling**: low-confidence / unmatched tracks are listed in
   the GUI; user pastes a link and it fixes the match (same as fix_track.py).
4. **Deep bug fix.** DONE ONLY AFTER **TWO CONSECUTIVE CLEAN SWEEPS**
   (two full audit passes in a row that find zero new issues). Any fix
   resets the counter to 0.
5. Remember ALL fixes (log below). Write a handoff before budget runs out.
6. Tell the user which features are good (see "Feature ideas").

## Status
- Sweep counter: 0 / 2 clean in a row
- Phase: planning done, no code changed yet

## Environment notes
- Dev box is Linux; cannot build a Windows .exe locally. Plan: GitHub
  Actions workflow on `windows-latest` running PyInstaller, uploading the
  .exe as an artifact / release asset.
- A frozen .exe has no `yt-dlp` on PATH: GUI/core must call yt-dlp through
  its Python API (or ship yt-dlp.exe + ffmpeg + deno next to the exe, with a
  first-run dependency downloader).
- Core modules call `sys.exit()` and `print()` directly; GUI needs them
  refactored into importable functions that raise exceptions and report
  progress via a callback/log sink.

## Bugs found by reading the code (NOT yet fixed unless marked)
| # | Where | Problem | Status |
|---|-------|---------|--------|
| 1 | tidal_resolve.py `_search`/`yt_search_best_match` | If all search retries fail (network down), `_search` returns `[]`, which is indistinguishable from "no results"; the track is then cached as `video_id: None` **permanently** and never retried. | open |
| 2 | tidal_resolve.py `safe_filename` vs sync_playlists.py `safe_filename` | Two different implementations. Names like `CON`, or ones ending in `.`/space, give different filenames in the two scripts, so sync says "no resolved tracks yet". | open |
| 3 | sync_playlists.py `mirror_to_usb` | `.mirror_marker` lives in the target root but isn't in staging, so `/MIR` / `rsync --delete` deletes it each run, then it is rewritten only if the mirror succeeds. A failed/interrupted mirror leaves the drive with real files and no marker, so the next run refuses ("unfamiliar folder"). Fix: exclude marker from deletion. | open |
| 4 | sync_playlists.py mirror | Internal files (`.track_index.json`, `.ytdlp_archive.txt`, leftover `.info.json`) get copied to the USB. Exclude them. | open |
| 5 | sync_playlists.py `clean_freshly_downloaded_files` | Filename artist uses `uploader`, so YouTube Music auto channels give `Song - Artist - Topic.mp3`. Prefer `artist`/`track` metadata, strip ` - Topic`. (verify with real yt-dlp output) | open, verify |
| 6 | sync_playlists.py fallback rename loop | Doesn't skip `.part/.ytdl/.temp` like the info.json loop does. | open (minor) |
| 7 | sync_playlists.py output template | No filename length limit; long titles can exceed Windows MAX_PATH / 255 chars and fail. | open |
| 8 | fix_track.py | Writes `resolved/*.txt` non-atomically and without taking the sync/resolve locks, so it can race a running sync. | open |
| 9 | tidal_resolve.py `tidal_login` | Saved `expiry_time` is stored as ISO string; tidalapi may expect a datetime. Verify against installed tidalapi; may force fresh login every run. | open, verify |
| 10 | tidal_resolve.py `find_tidal_playlist` / `tracks()` | Only own-created playlists (`user.playlists()`); verify `tracks()` paginates fully (>100 tracks) in installed tidalapi. | open, verify |
| 11 | both lock files | PID is written but never checked; a stale lock after a crash needs manual deletion. GUI should auto-detect dead PID. | open |
| 12 | tidal_resolve.py output | Odd message `"track(s) - 404 (New Era)"` for unmatched tracks. | open (cosmetic) |
| 13 | Manual songs (new feature) | Deletion-sync removes anything not in `resolved/<playlist>.txt`. Manually added/downloaded songs must live in their own folder/index that deletion-sync never touches. | design note |

## Fix log (append as done: what, where, commit)
_(empty)_

## Sweep log
_(empty)_ Format: `Sweep N - date - files reviewed - new issues found - counter`

## Plan
1. Refactor core into an importable package: `core/` (config, resolve,
   download, mirror, locks, library index) with no `sys.exit`/`print`.
2. Fix bugs above in the core; add unit tests (matcher, filenames, index,
   deletion breaker, mirror marker).
3. Build GUI (CustomTkinter): Dashboard (Sync, Dry-run/Debug), Review
   ("Needs review" list with paste-link fix), Add Song, Download from Link,
   Settings, Log panel.
4. GitHub Actions workflow: PyInstaller `--onefile --windowed` on windows-latest.
5. Audit sweeps until 2 clean in a row. Update README.

## Feature ideas (for the user)
- Needs-review queue (low-confidence + unmatched) with paste-fix.
- Dry-run preview showing exactly what would download/delete/mirror.
- Embed ID3 tags + cover art (car head units show title/artist properly).
- Per-track progress bar, cancel button, auto-detect USB drive.
- Manual songs folder protected from deletion-sync.
- Pre-flight check for ffmpeg/deno/yt-dlp with one-click install.
