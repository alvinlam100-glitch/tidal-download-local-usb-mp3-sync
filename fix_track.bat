@echo off
cd /d "%~dp0"
set PATH=%PATH%;%USERPROFILE%\.deno\bin
python fix_track.py %*
echo.
pause
