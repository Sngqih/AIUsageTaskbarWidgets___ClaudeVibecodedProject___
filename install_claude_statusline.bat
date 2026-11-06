@echo off
rem Lets UsageTray get instant Claude updates via Claude Code's status line.
rem Safe: backs up settings.json and keeps any status line you already have.
rem Undo with remove_claude_statusline.bat
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\UsageTray\venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo Run setup.bat first.
    pause
    exit /b 1
)
"%VENV%\Scripts\python.exe" install_claude_statusline.py
echo.
pause
