"""Shared data model: every provider returns a Snapshot."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

# Snapshot.status values
OK = "ok"            # fresh data
STALE = "stale"      # showing last known data, source temporarily unavailable
ERROR = "error"      # no usable data at all
LOADING = "loading"  # nothing fetched yet


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Window:
    """One rate-limit window, e.g. the 5-hour or weekly limit."""

    key: str                       # stable id, e.g. "five_hour"
    label: str                     # human label, e.g. "5-hour"
    used_pct: float                # 0..100
    resets_at: Optional[datetime]  # UTC, may be unknown
    primary: bool = False          # the short (session) window
    window_s: Optional[float] = None  # window length in seconds (5h, 7 days), if known

    def effective_pct(self, at: Optional[datetime] = None) -> float:
        """Usage, treating a window whose reset time has passed as empty."""
        at = at or now_utc()
        if self.resets_at is not None and self.resets_at <= at:
            return 0.0
        return max(0.0, min(100.0, self.used_pct))

    def has_reset(self, at: Optional[datetime] = None) -> bool:
        at = at or now_utc()
        return self.resets_at is not None and self.resets_at <= at

    def is_weekly(self) -> bool:
        if self.window_s:
            return self.window_s >= 6 * 86400
        return self.key.startswith("seven_day") or self.key == "secondary"

    def time_left_s(self, at: Optional[datetime] = None) -> Optional[float]:
        if self.resets_at is None:
            return None
        return (self.resets_at - (at or now_utc())).total_seconds()

    def expected_pct(self, at: Optional[datetime] = None) -> Optional[float]:
        """Usage % you'd be at by now if you used the window evenly."""
        left = self.time_left_s(at)
        if not self.window_s or left is None or left <= 0:
            return None
        return max(0.0, min(100.0, (1 - left / self.window_s) * 100))

    def underused(self, below_pct: float, within_days: float, at: Optional[datetime] = None) -> bool:
        """Weekly window ending within `within_days` with less than `below_pct` used."""
        left = self.time_left_s(at)
        return (self.is_weekly() and left is not None and 0 < left <= within_days * 86400
                and self.effective_pct(at) < below_pct)


@dataclass
class Snapshot:
    provider: str                          # "claude" | "codex"
    status: str = LOADING
    windows: list[Window] = field(default_factory=list)
    as_of: Optional[datetime] = None       # when the numbers were true
    checked_at: Optional[datetime] = None  # when we last tried to read them
    detail: str = ""                       # short human message (errors, plan, source)
    plan: str = ""
    warning: str = ""                      # shown in amber: e.g. "not live" reasons

    def primary(self) -> Optional[Window]:
        for w in self.windows:
            if w.primary:
                return w
        return self.windows[0] if self.windows else None

    def weekly(self) -> Optional[Window]:
        """The main weekly (all-models) window."""
        for w in self.windows:
            if w.key in ("seven_day", "secondary"):
                return w
        rest = [w for w in self.windows if not w.primary]
        return rest[0] if rest else None

    # --- JSON cache helpers -------------------------------------------------
    def to_json(self) -> dict:
        d = asdict(self)
        for k in ("as_of", "checked_at"):
            d[k] = d[k].isoformat() if d[k] else None
        for w in d["windows"]:
            w["resets_at"] = w["resets_at"].isoformat() if w["resets_at"] else None
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Snapshot":
        def dt(v):
            return datetime.fromisoformat(v) if v else None

        windows = [
            Window(
                key=w["key"], label=w["label"], used_pct=float(w["used_pct"]),
                resets_at=dt(w.get("resets_at")), primary=bool(w.get("primary")),
                window_s=w.get("window_s"),
            )
            for w in d.get("windows", [])
        ]
        return cls(
            provider=d["provider"], status=d.get("status", STALE), windows=windows,
            as_of=dt(d.get("as_of")), checked_at=dt(d.get("checked_at")),
            detail=d.get("detail", ""), plan=d.get("plan", ""),
            warning=d.get("warning", ""),
        )


def fmt_duration(seconds: float) -> str:
    """3d 4h / 2h 13m / 7m / <1m"""
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return "<1m"


def fmt_ago(t: Optional[datetime], at: Optional[datetime] = None) -> str:
    if t is None:
        return "never"
    secs = ((at or now_utc()) - t).total_seconds()
    if secs < 45:
        return "just now"
    return fmt_duration(secs) + " ago"


_ORDER = {"five_hour": 0, "primary": 0, "seven_day": 1, "secondary": 1}


def merge_windows(*groups: list[Window]) -> list[Window]:
    """Combine readings of the same limits from different sources without
    ever undercounting. Usage inside one limit window only goes up, so for
    the same reset time the highest reading wins; a later reset time means
    a newer window and replaces the older one."""
    out: dict[str, Window] = {}
    for group in groups:
        for w in group:
            cur = out.get(w.key)
            if cur is None:
                out[w.key] = w
            elif cur.resets_at and w.resets_at and abs((w.resets_at - cur.resets_at).total_seconds()) > 60:
                if w.resets_at > cur.resets_at:
                    out[w.key] = w
            elif w.used_pct > cur.used_pct:
                out[w.key] = Window(w.key, w.label, w.used_pct, w.resets_at or cur.resets_at,
                                    w.primary, w.window_s or cur.window_s)
            elif not cur.window_s and w.window_s:
                cur.window_s = w.window_s
    return sorted(out.values(), key=lambda w: (_ORDER.get(w.key, 2), w.label))
