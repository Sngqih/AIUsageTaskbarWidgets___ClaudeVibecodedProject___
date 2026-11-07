"""Shared logic: every provider has a LIVE source (asks the server, needs
internet, runs every minute or two) and a FILE source (written locally by
Claude Code / Codex after each reply, checked every few seconds for free).

The two are merged so the widget is always at least as accurate as the
best reading it has; it never lets an older or partial reading lower a value.
If the live source stops working, the snapshot carries a warning, and once the
data is more than STALE_AFTER old it is marked stale (grey icon).
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Optional

from .. import winutil
from ..model import ERROR, OK, STALE, Snapshot, merge_windows, now_utc

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=10)


class HybridProvider:
    name = "base"
    live_label = "live check"

    def __init__(self, live_interval_s: float, battery_multiplier: float = 2.0):
        self.live_interval_s = live_interval_s
        self.battery_multiplier = battery_multiplier
        self.poll_s: Optional[float] = None   # worker loop interval (set by the app)
        self.retry_after: Optional[float] = None
        self._next_live = 0.0
        self._backoff_s: Optional[float] = None
        self._live_snap: Optional[Snapshot] = None
        self._live_err: Optional[str] = None  # None = never tried, "" = last try OK
        self._file_snap: Optional[Snapshot] = None
        self._file_err = ""
        self._file_sig: object = object()
        self._login_sig: object = object()

    # ---- implemented by subclasses ---------------------------------------
    def _live(self) -> tuple[Optional[Snapshot], str]:
        raise NotImplementedError

    def _login_signature(self):
        """Something that changes when the saved login changes (e.g. the
        credentials file's mtime). A change triggers a live check right
        away, skipping any wait, so a fresh login is picked up in seconds."""
        return None

    def _file_signature(self):
        raise NotImplementedError

    def _read_file(self) -> tuple[Optional[Snapshot], str]:
        raise NotImplementedError

    def _plan(self) -> str:
        return ""

    # ---- main entry ------------------------------------------------------
    def fetch(self, force: bool = False) -> Optional[Snapshot]:
        """A new merged Snapshot, or None if nothing changed."""
        now = time.monotonic()
        changed = False

        sig = self._file_signature()
        if force or sig != self._file_sig:
            self._file_sig = sig
            self._file_snap, self._file_err = self._read_file()
            changed = True

        login_changed = False
        login_sig = self._login_signature()
        if login_sig != self._login_sig:
            login_changed = self._live_err not in (None, "")
            if login_changed:
                log.info("%s login changed; checking again now", self.name)
            self._login_sig = login_sig

        if force or login_changed or now >= self._next_live:
            mult = self.battery_multiplier if winutil.on_battery() else 1.0
            self._backoff_s = None
            snap, err = self._live()
            wait = self.live_interval_s * mult
            if self._backoff_s:
                wait = max(wait, self._backoff_s)
            self._next_live = time.monotonic() + wait
            if snap is not None:
                if self._live_err:
                    log.info("%s %s working again", self.name, self.live_label)
                self._live_snap, self._live_err = snap, ""
            else:
                if err != self._live_err:
                    log.info("%s %s failed: %s", self.name, self.live_label, err)
                self._live_err = err
            changed = True

        return self._compose() if changed else None

    def live_ok(self) -> bool:
        return self._live_err == "" and self._live_snap is not None

    def _compose(self) -> Snapshot:
        checked = now_utc()
        plan = (self._live_snap.plan if self._live_snap else "") or \
               (self._file_snap.plan if self._file_snap else "") or self._plan()
        sources = [s for s in (self._live_snap, self._file_snap) if s and s.windows]
        if not sources:
            detail = self._live_err or self._file_err or "Waiting for first reading…"
            return Snapshot(self.name, ERROR, checked_at=checked, plan=plan, detail=detail)

        windows = merge_windows(*[s.windows for s in sources])
        as_of = max(s.as_of for s in sources if s.as_of)
        snap = Snapshot(self.name, OK, windows=windows, as_of=as_of,
                        checked_at=checked, plan=plan)
        if self.live_ok():
            mins = round(self.live_interval_s / 60)
            snap.detail = f"Live: checked every {mins} min, plus instant updates after each reply."
        else:
            snap.warning = f"Not live: {self._live_err or 'live check pending'}"
            if checked - as_of > STALE_AFTER:
                snap.status = STALE
        return snap
