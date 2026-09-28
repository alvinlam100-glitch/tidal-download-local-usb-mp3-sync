@echo off
cd /d "%~dp0"
set PATH=%PATH%;%USERPROFILE%\.deno\bin
echo Step 1/2: resolving Tidal playlists to YouTube tracks...
python tidal_resolve.py
if errorlevel 1 (
    echo.
    echo Tidal resolve step failed, stopping before touching your library.
    pause
    exit /b 1
)
echo.
echo Step 2/2: downloading from YouTube, then mirroring if configured...
python sync_playlists.py
echo.
echo All done. Press any key to close this window.
pause >nul
