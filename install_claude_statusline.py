"""Adds (or removes) UsageTray's status-line hook in Claude Code's settings.

    python install_claude_statusline.py            install
    python install_claude_statusline.py --remove   undo

- Backs up settings.json first (settings.json.usagetray-backup).
- If you already have a status line, it's kept: the hook runs it for you.
"""

import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "claude_statusline.py"
MARK = "claude_statusline.py"


def claude_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".claude"


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / "UsageTray"
    p.mkdir(parents=True, exist_ok=True)
    return p


def python_exe() -> Path:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":  # status lines need a real stdout
        exe = exe.with_name("python.exe")
    return exe


def fwd(p: Path) -> str:
    """Forward slashes work in both cmd and the Git Bash Claude Code uses."""
    return str(p).replace("\\", "/")


def load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError as e:
        sys.exit(f"Couldn't parse {path} ({e}). Fix or remove it, then run this again.")


def write_settings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".usagetray.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def install() -> None:
    settings_path = claude_dir() / "settings.json"
    settings = load_settings(settings_path)
    chain_file = app_dir() / "statusline_chain.json"

    current = settings.get("statusLine")
    if isinstance(current, dict) and MARK in str(current.get("command", "")):
        print("Already installed; refreshing the path.")
    elif isinstance(current, dict) and current.get("command"):
        chain_file.write_text(json.dumps({"command": current["command"]}), encoding="utf-8")
        print(f"You already had a status line; it will keep showing:\n  {current['command']}")
    else:
        chain_file.unlink(missing_ok=True)

    if settings_path.exists():
        backup = settings_path.with_name("settings.json.usagetray-backup")
        if not backup.exists():
            shutil.copy2(settings_path, backup)
            print(f"Backup saved: {backup}")

    settings["statusLine"] = {
        "type": "command",
        "command": f'"{fwd(python_exe())}" "{fwd(HOOK)}"',
        "padding": 0,
    }
    write_settings(settings_path, settings)
    print(f"\nInstalled in {settings_path}")
    print("Open (or restart) Claude Code and send one message; the tray widget picks it up.")


def remove() -> None:
    settings_path = claude_dir() / "settings.json"
    settings = load_settings(settings_path)
    chain_file = app_dir() / "statusline_chain.json"
    current = settings.get("statusLine")
    if not (isinstance(current, dict) and MARK in str(current.get("command", ""))):
        print("UsageTray's status line isn't installed; nothing to do.")
        return
    try:
        prev = json.loads(chain_file.read_text(encoding="utf-8")).get("command")
    except (OSError, ValueError):
        prev = None
    if prev:
        settings["statusLine"] = {"type": "command", "command": prev}
        print(f"Restored your previous status line: {prev}")
    else:
        settings.pop("statusLine", None)
        print("Removed the status line.")
    write_settings(settings_path, settings)
    chain_file.unlink(missing_ok=True)


if __name__ == "__main__":
    remove() if "--remove" in sys.argv else install()
