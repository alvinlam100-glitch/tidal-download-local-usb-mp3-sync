<div align="center">

# tidal-download-local-usb-mp3-sync

**tidal-download-local-usb-mp3-sync** reads playlists from a Tidal account,
locates each track on YouTube, and downloads them as MP3 files into a local
music library. The library can be kept local or mirrored to any target that
reads plain MP3 files: a USB drive for a car head unit, an MP3 player, a
phone, or another folder.

[![License: MIT](https://img.shields.io/badge/license-MIT-44CC11?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square)](requirements.txt)

</div>

______________________________________________________________________

## Download

<div align="center">

### [**Download**](https://github.com/alvinlam100-glitch/tidal-download-local-usb-mp3-sync/archive/refs/heads/master.zip)

</div>

The link above downloads a single `.zip` file containing all required
files: every script, the setup and sync entry points, and this README.
Extract it, then continue at [Installation](#installation).

______________________________________________________________________

## Design rationale

Tidal access is read-only. The tool never downloads audio from Tidal and
never writes back to a Tidal account. This is deliberate: anyone using a
Tidal subscription for streaming should not need to grant a third-party
tool download or write access to that account. YouTube, via `yt-dlp`, is
used as the audio source instead. It requires no account, has no P2P
component, and is fully scriptable. Matching between a Tidal track and a
YouTube result uses duration comparison and text-similarity scoring, so a
mismatched result (live recording, remix, cover) is rejected rather than
downloaded on a guess.

## Components

| Script | Function |
|---|---|
| `tidal_resolve.py` | Logs into Tidal (read-only), reads each playlist listed in `playlists.json`, and searches YouTube Music for each track via `ytmusicapi`. Candidates are scored on duration and title/artist similarity; matches below threshold are rejected rather than guessed. Output is written to `resolved/<playlist name>.txt`. |
| `sync_playlists.py` | Downloads the tracks listed in each `resolved/*.txt` file via `yt-dlp`, renames them to `Song - Artist.mp3` with no video ID present, removes files for tracks no longer in the playlist, and maintains the result in a local `MusicLibrary/` folder. If a mirror target is configured, the library is also copied there. |
| `sync_all.bat` / `sync_all.sh` | Runs both steps in sequence. This is the entry point for routine use. |
| `setup.bat` / `setup.sh` | One-time setup. Checks for FFmpeg and Deno and offers to install them if missing, installs Python dependencies, and builds `playlists.json` interactively. |
| `fix_track.bat` / `fix_track.sh` | Corrects a track that matched incorrectly or did not match at all. See [Fixing a wrong or missing match](#fixing-a-wrong-or-missing-match). |

## Installation

No prior coding experience is required. The procedure:

1. Use the [Download](#download) link above and extract the `.zip`. No
   `git` knowledge is required.
2. Install [Python](https://www.python.org/downloads/) if it is not already
   present. The installer has a checkbox on its first screen labeled
   **"Add python.exe to PATH."** This must be checked, or the remaining
   steps will not function. This is the most common setup error.
3. Run `setup.bat` (Windows) or `./setup.sh` (Mac/Linux). This will:
   - Check for FFmpeg and Deno, both required, and offer to install them
     automatically where possible (`winget` on Windows, Homebrew on Mac).
     If automatic installation is not possible, direct instructions are
     provided instead.
   - Install remaining dependencies.
   - Prompt for the relevant Tidal playlist names and, optionally, a
     mirror target (a USB drive, MP3 player, or blank for local-only
     storage), then generate the configuration file. No manual file
     editing is required.
4. Run `sync_all.bat` / `./sync_all.sh`. A browser tab will open requesting
   Tidal login and approval of read-only access. Downloading then begins.

Subsequent runs require only `sync_all` to be executed again.

<details>
<summary>Prerequisites that cannot be fully automated, and why</summary>

- **Python itself.** It must already be running to execute `setup.py`, so
  it cannot install itself. Addressed by the installer referenced in step
  2 above.
- **FFmpeg and Deno.** `setup.bat`/`setup.sh` attempt automatic
  installation (step 3). If `winget` or `brew` is unavailable, or on
  Linux, a direct link and, where applicable, a package-manager command
  are provided instead.
- **A Tidal account** with the required playlists already created. This
  cannot be provided by the tool.

</details>

<details>
<summary>Manual setup, as an alternative to setup.bat/.sh</summary>

```bash
pip install -r requirements.txt
```
Then copy `playlists.example.json` to `playlists.json` and edit it:
- `tidal_name` for each playlist must match the exact name used in the
  Tidal account; `name` sets the local folder name.
- `usb_drive_path` specifies an additional mirror target, such as a USB
  drive letter or mount point, or an MP3 player path. Leave it as an empty
  string (`""`) to disable mirroring and keep the library local-only.

</details>

## Usage

Connect the mirror target, if one is configured, then run:

```bash
# Windows
sync_all.bat

# Mac/Linux
./sync_all.sh
```

The first run opens a browser link for Tidal login and approval of
read-only access. A token is then saved locally (`tidal_token.json`,
which must be excluded from version control, as it is a live credential),
so subsequent runs do not require re-authentication until the token
expires. YouTube Music searches are anonymous and require no Google
account.

## Features

- **New tracks** added to a Tidal playlist are picked up automatically on
  the next sync.
- **Removed tracks** are deleted automatically. Each downloaded file is
  tracked by its YouTube video ID in a per-playlist index, so cleanup only
  affects files the tool created.
- **Re-syncs are fast.** Each match is cached, by ISRC or, absent an ISRC,
  by title and artist, in `resolve_cache.json`, so only new tracks are
  re-resolved.
- **Filenames are clean:** `Song - Artist.mp3`, with no video IDs or
  YouTube channel-name artifacts.

Matching is not guaranteed to be perfect. Duration and text scoring filter
out most incorrect versions, but the "could not match" and "low-confidence
match" output printed after each run should be reviewed.

## Fixing a wrong or missing match

Two failure modes exist for an individual track: an incorrect match (wrong
language, live recording, remix), or no match at all (common for a track
too new to be present in YouTube Music's indexed catalog). In either case:

```bash
# Windows
fix_track.bat

# Mac/Linux
./fix_track.sh
```

The script prompts for the playlist and search term, or accepts them as
arguments directly: `python fix_track.py seoul "song name"`. It looks the
track up against the Tidal playlist itself, not the downloaded files, so
unmatched tracks are found as well. It reports the current status and
accepts a replacement YouTube link. Before accepting a replacement, it
checks the link's actual duration against Tidal's recorded track length
and flags a mismatch. No manual JSON editing is required.

After running the fix, a normal sync downloads the correction. Any
previously incorrect file is removed automatically, using the same
mechanism applied to a track removed from Tidal.

## Audio quality

YouTube's source audio typically tops out at approximately 128 to
160kbps. This is the practical ceiling regardless of output format, and
reflects what `yt-dlp` retrieves without misrepresentation.

## Account safety

Tidal access is limited to reading playlists and track metadata, equivalent
to browsing the app. No audio is downloaded through Tidal and no write
access to the Tidal library is used. YouTube access is fully anonymous, with
no login of any kind.

## Music sourcing and legal note

This tool uses YouTube as an audio source, consistent with the approach
used by comparable tools such as
[spotDL](https://github.com/spotDL/spotify-downloader).

> **Note**
> Users are responsible for their own actions and any resulting legal
> consequences. This project does not support unauthorized downloading of
> copyrighted material and assumes no responsibility for its use. It is
> intended for personal use with playlists the user already has legitimate
> access to. Redistribution of the resulting files is not supported.

## License

[MIT](LICENSE)
