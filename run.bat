@echo off
rem Starts UsageTray in the background (no console window).
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\UsageTray\venv"
if not exist "%VENV%\Scripts\pythonw.exe" (
    echo Run setup.bat first.
    pause
    exit /b 1
)
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0main.py"
