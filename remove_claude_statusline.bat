@echo off
rem Removes UsageTray's status line from Claude Code (restores your old one if any).
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\UsageTray\venv"
"%VENV%\Scripts\python.exe" install_claude_statusline.py --remove
echo.
pause
