@echo off
rem Optional: package UsageTray into a single dist\UsageTray.exe
rem (no Python needed to run it, and it shows as "UsageTray" in Windows settings).
rem Tagging a release on GitHub (e.g. v0.5.0) builds this automatically too.
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\UsageTray\venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo Run setup.bat first.
    pause
    exit /b 1
)
"%VENV%\Scripts\python.exe" -m pip install pyinstaller --quiet
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --noconsole ^
    --name UsageTray --icon assets\usagetray.ico ^
    --hidden-import pystray._win32 ^
    main.py
echo.
echo Built: dist\UsageTray.exe
echo If you had "Start with Windows" on, untick and re-tick it from the .exe
echo so Windows launches the .exe instead of the Python script.
pause
