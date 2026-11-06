"""Claude Code status-line hook for UsageTray.

Claude Code runs this after each response and pipes session info (including
`rate_limits.five_hour` / `rate_limits.seven_day`) to it as JSON on stdin.
We save the limits to %APPDATA%\\UsageTray\\claude_status.json for the tray
widget, then print a status line. If you already had a status line, the
installer saved its command and we run that instead, so nothing changes for you.

It must never break Claude Code: every error is swallowed and it exits 0.
Uses only the standard library and finishes in well under a second.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / "UsageTray"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _epoch(v):
    """resets_at may be epoch seconds or an ISO string; normalise to seconds."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v:
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def _pct(w):
    for k in ("used_percentage", "used_percent", "utilization"):
        if isinstance(w.get(k), (int, float)):
            return float(w[k])
    return None


def merge(old: dict, new: dict) -> dict:
    """Combine readings from several Claude Code windows. Usage within one
    limit window only goes up, so for the same reset time keep the highest
    value; a later reset time means a new window and replaces the old one.
    This stops an idle session from overwriting fresher numbers."""
    out = dict(old)
    for key, w in new.items():
        if not isinstance(w, dict) or _pct(w) is None:
            continue
        nw = {"used_percentage": _pct(w), "resets_at": _epoch(w.get("resets_at", w.get("reset_at")))}
        ow = out.get(key)
        if not ow:
            out[key] = nw
            continue
        o_reset, n_reset = ow.get("resets_at"), nw["resets_at"]
        if o_reset is None or n_reset is None or n_reset > o_reset + 60:
            out[key] = nw
        elif abs(n_reset - o_reset) <= 60:
            ow["used_percentage"] = max(ow.get("used_percentage") or 0, nw["used_percentage"])
            ow["resets_at"] = n_reset
        # older window: keep what we have
    return out


def save(rate_limits: dict) -> None:
    path = app_dir() / "claude_status.json"
    try:
        old = json.loads(path.read_text(encoding="utf-8")).get("rate_limits", {})
    except (OSError, ValueError, AttributeError):
        old = {}
    data = {"captured_at": time.time(), "rate_limits": merge(old, rate_limits)}
    tmp = path.with_name(f"claude_status.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def default_line(rl: dict) -> str:
    parts = []
    for key, name in (("five_hour", "5h"), ("seven_day", "7d")):
        w = rl.get(key)
        if isinstance(w, dict) and _pct(w) is not None:
            parts.append(f"{name} {_pct(w):.0f}%")
    return " · ".join(parts)


def main() -> None:
    raw = b""
    try:
        raw = sys.stdin.buffer.read()
        data = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        data = {}

    rl = data.get("rate_limits") if isinstance(data, dict) else None
    if isinstance(rl, dict) and rl:
        try:
            save(rl)
        except Exception:
            pass

    # Chain to the user's previous status line, if the installer saved one.
    try:
        chain = json.loads((app_dir() / "statusline_chain.json").read_text(encoding="utf-8"))
        cmd = chain.get("command")
    except Exception:
        cmd = None
    if cmd:
        try:
            r = subprocess.run(cmd, shell=True, input=raw, capture_output=True, timeout=8)
            sys.stdout.buffer.write(r.stdout)
        except Exception:
            pass
    else:
        line = default_line(rl or {})
        if line:
            sys.stdout.write(line + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
