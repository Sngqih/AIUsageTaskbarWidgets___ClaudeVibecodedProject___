"""Codex limits.

LIVE: `codex app-server` (the same local server the Codex VS Code extension
      uses). We start it in the background, send `account/rateLimits/read`,
      which asks OpenAI for the current limits, then shut it down. Uses your
      existing Codex login; needs internet.

FILE: every Codex turn appends a `token_count` event to
~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl that carries the account's
current rate limits:

    {"type": "event_msg", "timestamp": "...",
     "payload": {"type": "token_count", "info": {...},
                 "rate_limits": {"primary":   {"used_percent": 12.0, "window_minutes": 300,   "resets_at": 1765975828},
                                 "secondary": {"used_percent": 30.0, "window_minutes": 10080, "resets_at": 1766562628},
                                 "plan_type": "plus"}}}

We find the newest such event. Checking for changes is just a few file
stats, so it can run every few seconds without costing battery.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import queue
import shlex
import shutil
import subprocess
import threading
import time

from .. import __version__, winutil
from ..model import OK, Snapshot, Window, now_utc
from .base import HybridProvider

log = logging.getLogger(__name__)

DAYS_BACK = 8                 # how many dated folders to look through
MAX_FILES = 6                 # newest N session files to search for a limits event
CHUNK = 256 * 1024
MAX_SCAN_BYTES = 16 * 1024 * 1024  # per file, scanning backwards from the end


class CodexProvider(HybridProvider):
    name = "codex"
    live_label = "app-server check"

    def __init__(self, codex_home: Path, command: Optional[str] = None,
                 live_interval_s: float = 60, battery_multiplier: float = 2.0):
        super().__init__(live_interval_s, battery_multiplier)
        self.codex_home = codex_home
        self.command = command

    @property
    def sessions_dir(self) -> Path:
        return self.codex_home / "sessions"

    # ------------------------------------------------------------------- live
    def codex_command(self) -> Optional[list[str]]:
        if self.command:
            parts = shlex.split(self.command, posix=not winutil.IS_WIN)
            # Windows-style split keeps the quotes around "C:\path with spaces\codex.cmd"
            return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
        exe = shutil.which("codex")
        return [exe] if exe else None

    def _live(self) -> tuple[Optional[Snapshot], str]:
        cmd = self.codex_command()
        if not cmd:
            return None, "couldn't find the `codex` command (is Codex CLI installed and on PATH?)."
        result, err = app_server_rate_limits(cmd, self.codex_home)
        if result is None:
            return None, err
        rl = result.get("rateLimits") if isinstance(result.get("rateLimits"), dict) else result
        checked = now_utc()
        windows = parse_rate_limits(_snake(rl), checked)
        if not windows:
            return None, "Codex returned no plan limits (logged in with an API key instead of ChatGPT?)."
        plan = str(rl.get("planType") or rl.get("plan_type") or result.get("planType") or "")
        if plan.lower() == "unknown":
            plan = ""
        return Snapshot(self.name, OK, windows=windows, as_of=checked,
                        checked_at=checked, plan=plan), ""

    # ------------------------------------------------------------------- file
    def _recent_files(self) -> list[tuple[float, int, Path]]:
        """(mtime, size, path) of recent session files, newest first."""
        root = self.sessions_dir
        today = date.today()
        seen: set[Path] = set()
        found: list[tuple[float, int, Path]] = []
        # Local and UTC dates can differ (SG is UTC+8), so look one extra day.
        for i in range(-1, DAYS_BACK):
            d = today - timedelta(days=i)
            folder = root / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
            if folder in seen or not folder.is_dir():
                continue
            seen.add(folder)
            try:
                with os.scandir(folder) as it:
                    for e in it:
                        if e.name.endswith(".jsonl") and e.is_file():
                            st = e.stat()
                            found.append((st.st_mtime, st.st_size, Path(e.path)))
            except OSError:
                continue
        found.sort(key=lambda t: t[0], reverse=True)
        return found

    def _file_signature(self):
        if not self.codex_home.exists():
            return None
        return tuple((p, m, s) for m, s, p in self._recent_files()[:MAX_FILES])

    def _read_file(self) -> tuple[Optional[Snapshot], str]:
        if not self.codex_home.exists():
            return None, f"Codex folder not found ({self.codex_home})."
        files = self._recent_files()
        for _, _, path in files[:MAX_FILES]:
            event = _last_rate_limit_event(path)
            if event is None:
                continue
            ts, rl = event
            windows = parse_rate_limits(rl, ts)
            if not windows:
                continue
            plan = str(rl.get("plan_type") or "")
            if plan.lower() == "unknown":
                plan = ""
            return Snapshot(self.name, OK, windows=windows, as_of=ts,
                            checked_at=now_utc(), plan=plan), ""
        return None, ""


# ------------------------------------------------------------ app-server
CREATE_NO_WINDOW = 0x08000000
APP_SERVER_TIMEOUT_S = 25


def _snake(rl: dict) -> dict:
    """app-server uses camelCase (usedPercent, windowDurationMins, resetsAt)."""
    out = {}
    for slot in ("primary", "secondary"):
        w = rl.get(slot)
        if isinstance(w, dict):
            out[slot] = {
                "used_percent": w.get("usedPercent", w.get("used_percent")),
                "window_minutes": w.get("windowDurationMins", w.get("window_minutes")),
                "resets_at": w.get("resetsAt", w.get("resets_at")),
            }
    return out


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if proc.stdin:
            proc.stdin.close()  # app-server exits on end of input
    except OSError:
        pass
    try:
        proc.wait(timeout=3)
        if proc.stdout:
            proc.stdout.close()
        return
    except subprocess.TimeoutExpired:
        pass
    if winutil.IS_WIN:  # `codex` is often a .cmd shim; kill its children too
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    else:
        proc.kill()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    try:
        if proc.stdout:
            proc.stdout.close()
    except OSError:
        pass


def app_server_rate_limits(cmd: list[str], codex_home: Optional[Path] = None,
                           timeout: float = APP_SERVER_TIMEOUT_S) -> tuple[Optional[dict], str]:
    """Start `codex app-server`, do the JSON-RPC handshake, ask for
    account/rateLimits/read, then shut it down. Returns (result, error)."""
    env = dict(os.environ)
    if codex_home:
        env["CODEX_HOME"] = str(codex_home)
    try:
        proc = subprocess.Popen(
            [*cmd, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env,
            creationflags=CREATE_NO_WINDOW if winutil.IS_WIN else 0,
        )
    except OSError as e:
        return None, f"couldn't start Codex ({e})."

    lines: queue.Queue = queue.Queue()

    def reader():
        try:
            for raw in proc.stdout:
                lines.put(raw)
        except (OSError, ValueError):
            pass  # pipe closed during shutdown
        finally:
            lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.monotonic() + timeout

    def send(obj) -> None:
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def wait_for(msg_id: int):
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            raw = lines.get(timeout=left)
            if raw is None:
                raise EOFError
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == msg_id:
                return msg  # skip notifications / other ids

    try:
        send({"method": "initialize", "id": 0, "params": {"clientInfo": {
            "name": "usage_tray", "title": "UsageTray", "version": __version__}}})
        init = wait_for(0)
        if "error" in init:
            return None, f"Codex refused to start: {_err_text(init['error'])}"
        send({"method": "initialized"})
        for attempt, msg_id in enumerate((1, 2, 3)):
            send({"method": "account/rateLimits/read", "id": msg_id})
            resp = wait_for(msg_id)
            if "error" in resp:
                return None, f"Codex couldn't read limits: {_err_text(resp['error'])}"
            result = resp.get("result") or {}
            rl = result.get("rateLimits") if isinstance(result, dict) else None
            if isinstance(rl, dict) and (rl.get("primary") or rl.get("secondary")):
                return result, ""
            time.sleep(0.7)  # right after start-up the answer can be empty
        return None, "Codex returned no rate-limit data (logged in with an API key?)."
    except (TimeoutError, queue.Empty):
        return None, "Codex didn't answer in time (offline?)."
    except (EOFError, OSError, ValueError):
        return None, "Codex exited unexpectedly (try updating Codex CLI)."
    finally:
        _kill_tree(proc)


def _err_text(err) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err)[:160]
    return str(err)[:160]


# ---------------------------------------------------------------- parsing
def _iter_lines_reversed(path: Path):
    """Yield complete lines (bytes) from the end of a file backwards."""
    try:
        f = open(path, "rb")
    except OSError:
        return
    with f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        scanned = 0
        tail = b""
        while pos > 0 and scanned < MAX_SCAN_BYTES:
            step = min(CHUNK, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + tail
            scanned += step
            lines = buf.split(b"\n")
            tail = lines[0]  # may be partial, keep for next round
            for line in reversed(lines[1:]):
                if line.strip():
                    yield line
        if pos == 0 and tail.strip():
            yield tail


def _last_rate_limit_event(path: Path) -> Optional[tuple[datetime, dict]]:
    for line in _iter_lines_reversed(path):
        if b"rate_limits" not in line or b"token_count" not in line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue  # line still being written
        payload = obj.get("payload") or {}
        if payload.get("type") != "token_count":
            continue
        rl = payload.get("rate_limits")
        if not isinstance(rl, dict):
            continue
        ts = _parse_ts(obj.get("timestamp")) or datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc)
        return ts, rl
    return None


def _parse_ts(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _label(minutes: Optional[float], fallback: str) -> str:
    if not minutes:
        return fallback
    m = float(minutes)
    if abs(m - 10080) <= 60:
        return "Weekly"
    if m < 1440:
        return f"{round(m / 60)}-hour"
    return f"{round(m / 1440)}-day"


def parse_rate_limits(rl: dict, event_ts: datetime) -> list[Window]:
    """Handles both the current nested format and the older flat one
    (primary_used_percent, primary_resets_in_seconds, ...)."""
    out: list[Window] = []
    for slot, fallback in (("primary", "5-hour"), ("secondary", "Weekly")):
        w = rl.get(slot)
        if isinstance(w, dict):
            pct = w.get("used_percent")
            minutes = w.get("window_minutes")
            resets_at = w.get("resets_at")
            resets_in = w.get("resets_in_seconds")
        else:  # older flat layout
            pct = rl.get(f"{slot}_used_percent")
            minutes = rl.get(f"{slot}_window_minutes")
            resets_at = None
            resets_in = rl.get(f"{slot}_resets_in_seconds")
        if not isinstance(pct, (int, float)):
            continue
        reset_dt: Optional[datetime] = None
        if isinstance(resets_at, (int, float)):
            v = float(resets_at) / (1000.0 if resets_at > 1e12 else 1.0)
            reset_dt = datetime.fromtimestamp(v, tz=timezone.utc)
        elif isinstance(resets_at, str):
            reset_dt = _parse_ts(resets_at)
        elif isinstance(resets_in, (int, float)):
            reset_dt = event_ts + timedelta(seconds=float(resets_in))
        win_s = float(minutes) * 60 if isinstance(minutes, (int, float)) and minutes else \
            (5 * 3600.0 if slot == "primary" else 7 * 86400.0)
        out.append(Window(
            key=slot, label=_label(minutes, fallback), used_pct=float(pct),
            resets_at=reset_dt, primary=(slot == "primary"), window_s=win_s,
        ))
    return out
