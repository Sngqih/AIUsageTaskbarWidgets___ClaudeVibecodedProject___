"""Background workers that refresh each provider, plus the shared store."""

from __future__ import annotations

import json
import logging
import threading
from typing import Callable, Optional

from . import winutil
from .config import Config, app_dir
from .model import ERROR, LOADING, OK, STALE, Snapshot

log = logging.getLogger(__name__)


class Store:
    """Latest Snapshot per provider. Saved to disk so the icons show the last
    known values instantly after a reboot instead of 'loading'."""

    def __init__(self, providers: list[str]):
        self._lock = threading.Lock()
        self._path = app_dir() / "last.json"
        self._snaps: dict[str, Snapshot] = {p: Snapshot(p, LOADING) for p in providers}
        self._load_cache()

    def _load_cache(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for d in data:
                s = Snapshot.from_json(d)
                if s.provider in self._snaps and s.windows:
                    s.status, s.detail = STALE, "Starting up… showing last known values."
                    self._snaps[s.provider] = s
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Ignoring bad cache: %s", e)

    def get(self, provider: str) -> Snapshot:
        with self._lock:
            return self._snaps[provider]

    def all(self) -> list[Snapshot]:
        with self._lock:
            return list(self._snaps.values())

    def update(self, new: Snapshot) -> Snapshot:
        with self._lock:
            prev = self._snaps.get(new.provider)
            # A temporary problem (offline, expired token, rate limit): keep
            # showing the last good numbers, just marked stale.
            if new.status == STALE and not new.windows and prev and prev.windows:
                new.windows, new.as_of = prev.windows, prev.as_of
                new.plan = new.plan or prev.plan
            self._snaps[new.provider] = new
            snaps = list(self._snaps.values())
        try:
            self._path.write_text(json.dumps([s.to_json() for s in snaps]), encoding="utf-8")
        except OSError as e:
            log.debug("Cache write failed: %s", e)
        return new


class Worker(threading.Thread):
    """Calls provider.fetch() on a timer. refresh() wakes it immediately."""

    def __init__(self, provider, interval_s: float, cfg: Config, store: Store,
                 on_update: Callable[[Snapshot], None]):
        super().__init__(name=f"worker-{provider.name}", daemon=True)
        self.provider = provider
        self.interval_s = interval_s
        self.cfg = cfg
        self.store = store
        self.on_update = on_update
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._force = False

    def refresh(self) -> None:
        self._force = True
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _next_wait(self) -> float:
        wait = float(getattr(self.provider, "poll_s", None) or self.interval_s)
        if winutil.on_battery():
            wait *= self.cfg.battery_multiplier
        hint: Optional[float] = getattr(self.provider, "retry_after", None)
        if hint:
            wait = max(wait, hint)
        return wait

    def run(self) -> None:
        while not self._stop.is_set():
            force, self._force = self._force, False
            try:
                snap = self.provider.fetch(force=force)
                if snap is not None:
                    snap = self.store.update(snap)
                    if snap.status != OK:
                        log.info("%s: %s - %s", snap.provider, snap.status, snap.warning or snap.detail)
                    self.on_update(snap)
            except Exception:
                log.exception("%s fetch crashed", self.provider.name)
                self.on_update(self.store.update(Snapshot(
                    self.provider.name, ERROR, detail="Internal error, see log file.")))
            self._wake.wait(self._next_wait())
            self._wake.clear()
