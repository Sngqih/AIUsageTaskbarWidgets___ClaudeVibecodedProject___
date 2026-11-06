@echo off
rem One-time setup: creates UsageTray's private Python environment, installs
rem the two dependencies, then checks that Claude and Codex data can be read.
rem The environment lives in %LOCALAPPDATA%\UsageTray\venv (outside this folder),
rem so OneDrive doesn't sync it and git never sees it.
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\UsageTray\venv"

if exist "%VENV%\Scripts\python.exe" goto have_venv
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -m venv "%VENV%"
goto check_venv
:use_python
python -m venv "%VENV%"
:check_venv
if not exist "%VENV%\Scripts\python.exe" (
    echo.
    echo Could not create the environment. Install Python 3.10+ from python.org
    echo ^(tick "Add python.exe to PATH" in the installer^) and run this again.
    pause
    exit /b 1
)
:have_venv

"%VENV%\Scripts\python.exe" -m pip install --upgrade pip --quiet
"%VENV%\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo Installing dependencies failed. Check your internet connection.
    pause
    exit /b 1
)

echo.
echo ==== Setup done. Checking your data sources... ====
"%VENV%\Scripts\python.exe" probe.py
echo.
echo If both sections show numbers, double-click run.bat to start UsageTray.
pause
