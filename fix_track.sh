#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -d "$HOME/.deno/bin" ] && export PATH="$PATH:$HOME/.deno/bin"
python3 fix_track.py "$@"
