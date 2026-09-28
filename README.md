<div align="center">

# tidal-mp3-sync

**tidal-mp3-sync** reads playlists from your Tidal account, finds each track
on YouTube, and downloads them as clean MP3s into a local music library. Keep
it purely local, or mirror it anywhere that reads plain MP3 files — a USB
stick for a car head unit, an MP3 player, a phone, another folder entirely.

[![License: MIT](https://img.shields.io/badge/license-MIT-44CC11?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square)](requirements.txt)

</div>

______________________________________________________________________

## Why it's built this way

If you listen on Tidal and don't want a downloading tool anywhere near that
account, this only ever *reads* your Tidal playlists — read-only, no
downloading, no writing back to Tidal. YouTube via `yt-dlp` is used as the
audio source: free, doesn't touch any real streaming account, isn't P2P, and
is fully scriptable. Matching uses strict duration + text-similarity scoring
so a wrong version (live take, remix, cover) gets rejected rather than
silently downloaded.

## How it works

| Script | What it does |
|---|---|
| `tidal_resolve.py` | Logs into Tidal (read-only), reads each playlist in `playlists.json`, and searches YouTube Music for each track via `ytmusicapi`. Scores candidates on duration and title/artist similarity, rejecting anything too far off rather than guessing. Writes `resolved/<playlist name>.txt`. |
| `sync_playlists.py` | Downloads the tracks in each `resolved/*.txt` via `yt-dlp`, renames them to a clean `Song - Artist.mp3` with no video ID visible, deletes anything no longer in the playlist, and keeps it all in a local `MusicLibrary/` folder. If you've set a mirror target, also copies it there (a USB stick, MP3 player, or any other folder). |
| `sync_all.bat` / `sync_all.sh` | Runs both steps in order. This is the one you double-click day to day. |
| `setup.bat` / `setup.sh` | One-time setup: checks for FFmpeg/Deno and offers to install them automatically if missing, installs Python dependencies, and interactively builds `playlists.json` for you. |
| `fix_track.bat` / `fix_track.sh` | Fixes a specific track that matched wrong, or never matched at all - see [Fixing a wrong or missing match](#fixing-a-wrong-or-missing-match) below. |

## Installation

**No coding experience needed.** Here's the whole path from zero:

1. On this repo's GitHub page, click the green **Code** button → **Download ZIP**,
   then extract it somewhere (no `git` knowledge needed).
2. Install [Python](https://www.python.org/downloads/) if you don't have it.
   **Important:** the installer has a checkbox at the bottom of the first
   screen that says **"Add python.exe to PATH"** — tick it, or nothing
   else in these steps will work. This is the single most common thing
   beginners miss.
3. Run `setup.bat` (Windows) or `./setup.sh` (Mac/Linux) — just
   double-click it. It will:
   - Check for FFmpeg and Deno (two other required tools), and **offer
     to install them for you automatically** if it finds a way to
     (`winget` on Windows, Homebrew on Mac) — you just type `y` and wait.
     If it can't install one automatically, it tells you exactly what to
     click instead.
   - Install everything else this needs.
   - Ask you plain-English questions (your Tidal playlist names, and
     optionally where to mirror the library — a USB stick, MP3 player, or
     just leave it blank to keep it local-only) and build your config for
     you. No file editing required.
4. Run `sync_all.bat` / `./sync_all.sh` — a browser tab opens asking you
   to log into Tidal and approve read-only access (click a link, click
   approve, done). Then it downloads everything.

That's it. Every run after the first is just double-clicking `sync_all`
again.

<details>
<summary>Prerequisites this can't fully automate, and why</summary>

- **Python itself** — has to already be running to execute `setup.py` in
  the first place, so it can't install itself. One installer, see step 2
  above.
- **FFmpeg and Deno** — `setup.bat`/`setup.sh` will try to install both
  automatically for you (see step 3). If that fails (no `winget`/`brew`
  available, or you're on Linux), you'll get a direct link and, on Linux,
  a package-manager command to run.
- **A Tidal account** with the playlists you want already built on it —
  nobody else can do this part for you.

</details>

<details>
<summary>Or set it up entirely by hand instead of using setup.bat/.sh</summary>

```bash
pip install -r requirements.txt
```
Then copy `playlists.example.json` to `playlists.json` and edit it:
- `tidal_name` for each playlist must match the exact name in your Tidal
  account; `name` is the local folder name.
- `usb_drive_path` is where to additionally mirror the library — a USB
  stick's drive letter/mount point, an MP3 player, any folder. Leave it as
  an empty string (`""`) to skip mirroring and keep the library local-only.

</details>

## Usage

Plug in your mirror target if you've configured one, then:

```bash
# Windows
sync_all.bat

# Mac/Linux
./sync_all.sh
```

First run opens a browser link to log into Tidal and approve read-only
access; after that it saves a token locally (`tidal_token.json` — keep this
out of version control, it's a live credential) so you won't be asked again
until it expires. YouTube Music searches are anonymous, no Google account
needed.

## Features

- **Adds new tracks** you've added to a Tidal playlist automatically on the
  next sync.
- **Removes deleted tracks** automatically too — each downloaded file is
  tracked by its YouTube video ID in a small per-playlist index, so cleanup
  only ever touches files it put there.
- **Fast re-syncs** — every match is cached (by ISRC, or by title+artist if
  a track has no ISRC) in `resolve_cache.json`,
  so only genuinely new tracks get re-resolved.
- **Clean filenames** — `Song - Artist.mp3`, no video IDs, no YouTube
  channel-name artifacts.

Not handled: perfect matching. Duration + text scoring filters out most
wrong versions, but check the "could not match" / "low-confidence match"
lines the script prints after a run.

## Fixing a wrong or missing match

Two things can go wrong with an individual track: it matched the wrong
version (wrong language, a live take, a remix), or it never matched at all
(common for a track that's brand new and not yet in YouTube Music's clean
catalog - very new releases especially). Either way:

```bash
# Windows
fix_track.bat

# Mac/Linux
./fix_track.sh
```

It'll ask which playlist and what to search for (or pass them directly:
`python fix_track.py seoul "song name"`). It looks the track up on your
actual Tidal playlist - not just your downloaded files - so it finds
never-matched tracks too, shows you its current status, and lets you paste
a replacement YouTube link. Before accepting it, it automatically checks
the link's real length against Tidal's actual track length and warns you
if they don't match - the same check worth doing by hand, done for you.
Nothing to edit by hand, no JSON files to understand.

After running it, do a normal sync to download the fix - any old wrong
file gets removed automatically, the same way a track removed from Tidal
would be.

## Audio quality

YouTube's actual source audio typically tops out around 128–160kbps — that's
the real ceiling regardless of what format you convert to afterward, and is
what `yt-dlp` pulls honestly.

## Account safety

The Tidal side only reads playlists and track info, the same access as
browsing the app — it never downloads audio or touches your Tidal library.
The YouTube side runs fully anonymously, no login of any kind.

## Music sourcing and legal note

This tool uses YouTube as an audio source, the same approach used by
similar tools like [spotDL](https://github.com/spotDL/spotify-downloader).

> **Note**
> Users are responsible for their own actions and any legal consequences.
> This project does not support unauthorized downloading of copyrighted
> material and takes no responsibility for how it's used. It's built for
> personal use with playlists you already have legitimate access to — don't
> redistribute the files it produces.

## License

[MIT](LICENSE)
