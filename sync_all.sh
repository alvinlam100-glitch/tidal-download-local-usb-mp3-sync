#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -d "$HOME/.deno/bin" ] && export PATH="$PATH:$HOME/.deno/bin"
echo "Step 1/2: resolving Tidal playlists to YouTube tracks..."
python3 tidal_resolve.py || { echo "Tidal resolve step failed, stopping before touching your library."; exit 1; }
echo ""
echo "Step 2/2: downloading from YouTube, then mirroring if configured..."
python3 sync_playlists.py
