"""Claude limits.

LIVE: the endpoint Claude Code's /usage screen uses,
      GET https://api.anthropic.com/api/oauth/usage
      with the OAuth access token Claude Code stores in ~/.claude/.credentials.json.
      Account-wide, so it includes claude.ai and app usage too.
      We only READ the token; we never refresh it ourselves (refreshing rotates
      the refresh token and could sign Claude Code out).

FILE: `claude_statusline.py` (installed as Claude Code's status line) saves the
      rate limits Claude Code passes to it after each reply into
      %APPDATA%\\UsageTray\\claude_status.json, for instant updates while you work.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import __version__
from ..config import app_dir
from ..model import OK, Snapshot, Window, fmt_duration, now_utc
from .base import HybridProvider

log = logging.getLogger(__name__)

ENDPOINT = os.environ.get(
    "USAGETRAY_CLAUDE_ENDPOINT", "https://api.anthropic.com/api/oauth/usage"
)
BETA_HEADER = "oauth-2025-04-20"
TIMEOUT_S = 15

_LABELS = {
    "five_hour": "5-hour",
    "seven_day": "Weekly",
    "seven_day_opus": "Weekly · Opus",
    "seven_day_sonnet": "Weekly · Sonnet",
}
# Buckets that exist in the response but aren't useful to show.
_SKIP = {"extra_usage", "seven_day_oauth_apps"}
_ORDER = {"five_hour": 0, "seven_day": 1}


def _window_s(key: str):
    if key == "five_hour":
        return 5 * 3600.0
    if key.startswith("seven_day"):
        return 7 * 86400.0
    return None

LOGIN_HINT = "run `claude auth login` in a terminal to fix."


class ClaudeProvider(HybridProvider):
    name = "claude"
    live_label = "usage API"

    def __init__(self, config_dir: Path, status_file: Optional[Path] = None,
                 live_interval_s: float = 120, battery_multiplier: float = 2.0):
        super().__init__(live_interval_s, battery_multiplier)
        self.config_dir = config_dir
        self.status_file = status_file or (app_dir() / "claude_status.json")

    @property
    def credentials_path(self) -> Path:
        return self.config_dir / ".credentials.json"

    # ------------------------------------------------------------ credentials
    def read_credentials(self) -> tuple[Optional[str], dict]:
        p = self.credentials_path
        if not p.exists():
            return None, {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("Could not read Claude credentials: %s", e)
            return None, {}
        oauth = data.get("claudeAiOauth") or {}
        token = oauth.get("accessToken") or oauth.get("access_token")
        return token, oauth

    def _plan(self) -> str:
        return str(self.read_credentials()[1].get("subscriptionType") or "")

    def token_expires_in(self) -> Optional[float]:
        """Seconds until the saved access token expires (negative = expired)."""
        exp = self.read_credentials()[1].get("expiresAt")
        try:
            return float(exp) / 1000.0 - now_utc().timestamp() if exp else None
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------- live
    def _live(self) -> tuple[Optional[Snapshot], str]:
        token, oauth = self.read_credentials()
        plan = str(oauth.get("subscriptionType") or "")
        if not self.credentials_path.exists():
            return None, f"Claude Code isn't logged in on this PC; {LOGIN_HINT}"
        if not token:
            return None, f"Claude Code's saved login token is blank; {LOGIN_HINT}"

        req = urllib.request.Request(ENDPOINT, headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": BETA_HEADER,
            "Accept": "application/json",
            "User-Agent": f"usage-tray/{__version__}",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                left = self.token_expires_in()
                if left is not None and left < 0:
                    return None, (f"saved login token expired {fmt_duration(-left)} ago "
                                  f"(Claude Code didn't save its refreshed one); {LOGIN_HINT}")
                return None, f"Anthropic rejected the saved login token; {LOGIN_HINT}"
            if e.code == 429:
                ra = e.headers.get("Retry-After") if e.headers else None
                self._backoff_s = float(ra) if ra and ra.isdigit() else 600.0
                return None, "usage endpoint is rate-limiting; retrying in a few minutes."
            self._backoff_s = 300.0
            return None, f"usage endpoint returned HTTP {e.code}."
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError):
            return None, "offline (no internet)."
        except ValueError:
            return None, "unexpected response from the usage endpoint."

        windows = parse_usage(body)
        if not windows:
            return None, "usage response had no limit data (format may have changed)."
        checked = now_utc()
        return Snapshot(self.name, OK, windows=windows, as_of=checked,
                        checked_at=checked, plan=plan), ""

    # ------------------------------------------------------------------- file
    def hook_installed(self) -> bool:
        try:
            s = json.loads((self.config_dir / "settings.json").read_text(encoding="utf-8"))
            return "claude_statusline.py" in str((s.get("statusLine") or {}).get("command", ""))
        except (OSError, ValueError, AttributeError):
            return False

    def _file_signature(self):
        try:
            st = self.status_file.stat()
            return (st.st_mtime, st.st_size)
        except OSError:
            return None

    def _read_file(self) -> tuple[Optional[Snapshot], str]:
        try:
            data = json.loads(self.status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, ("" if self.hook_installed() else
                          "status line not installed (run install_claude_statusline.bat).")
        rl = data.get("rate_limits") if isinstance(data, dict) else None
        windows = parse_status_line(rl) if isinstance(rl, dict) else []
        if not windows:
            return None, ""
        checked = now_utc()
        as_of = _parse_time(data.get("captured_at")) or checked
        return Snapshot(self.name, OK, windows=windows, as_of=as_of, checked_at=checked), ""


# ------------------------------------------------------------------ parsing
def _parse_time(v) -> Optional[datetime]:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            v = float(v)
            if v > 1e12:  # milliseconds
                v /= 1000.0
            return datetime.fromtimestamp(v, tz=timezone.utc)
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def _label(key: str) -> str:
    if key in _LABELS:
        return _LABELS[key]
    if key.startswith("seven_day_"):
        return "Weekly · " + key[len("seven_day_"):].replace("_", " ").title()
    return key.replace("_", " ").capitalize()


def parse_usage(body: dict) -> list[Window]:
    """/api/oauth/usage JSON -> Windows. Unknown buckets are shown
    automatically so new model-specific limits appear without a code change."""
    out: list[Window] = []
    if not isinstance(body, dict):
        return out
    for key, val in body.items():
        if key in _SKIP or not isinstance(val, dict):
            continue
        util = val.get("utilization")
        if not isinstance(util, (int, float)):
            continue
        out.append(Window(key=key, label=_label(key), used_pct=float(util),
                          resets_at=_parse_time(val.get("resets_at")),
                          primary=(key == "five_hour"), window_s=_window_s(key)))
    out.sort(key=lambda w: (_ORDER.get(w.key, 2), w.label))
    return out


def parse_status_line(rl: dict) -> list[Window]:
    """rate_limits as passed to status-line scripts:
    {"five_hour": {"used_percentage": 23.5, "resets_at": 1738425600}, "seven_day": {...}}"""
    out: list[Window] = []
    for key, w in rl.items():
        if not isinstance(w, dict):
            continue
        pct = next((w[k] for k in ("used_percentage", "used_percent", "utilization")
                    if isinstance(w.get(k), (int, float))), None)
        if pct is None:
            continue
        out.append(Window(key=key, label=_label(key), used_pct=float(pct),
                          resets_at=_parse_time(w.get("resets_at", w.get("reset_at"))),
                          primary=(key == "five_hour"), window_s=_window_s(key)))
    out.sort(key=lambda x: (_ORDER.get(x.key, 2), x.label))
    return out
