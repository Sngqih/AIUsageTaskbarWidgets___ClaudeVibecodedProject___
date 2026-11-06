"""Wires everything together. Tk owns the main thread; workers and tray
icons run on background threads and talk to the UI through a queue."""

from __future__ import annotations

import logging
import queue
import time
import tkinter as tk
from logging.handlers import RotatingFileHandler

from . import __version__, winutil
from .config import Config, app_dir
from .flyout import Flyout
from .icon import PROVIDER_NAME, render_icon, tooltip
from .model import OK, Snapshot, fmt_duration, now_utc
from .poller import Store, Worker
from .providers import ClaudeProvider, CodexProvider

log = logging.getLogger("usagetray")

TICK_MS = 30_000  # re-draw icons so countdowns and passed resets stay right


def setup_logging() -> None:
    h = RotatingFileHandler(app_dir() / "usage-tray.log", maxBytes=512_000,
                            backupCount=1, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)


class App:
    def __init__(self):
        self.cfg = Config.load()
        self.q: queue.Queue = queue.Queue()
        self.light_taskbar = winutil.taskbar_is_light()

        c = self.cfg
        providers = []
        if c.show_claude:
            providers.append(ClaudeProvider(c.claude_dir(), live_interval_s=c.claude_live_interval_s,
                                            battery_multiplier=c.battery_multiplier))
        if c.show_codex:
            providers.append(CodexProvider(c.codex_dir(), command=c.codex_command,
                                           live_interval_s=c.codex_live_interval_s,
                                           battery_multiplier=c.battery_multiplier))
        for p in providers:
            p.poll_s = c.file_check_interval_s
        names = [p.name for p in providers]

        self.store = Store(names)
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("UsageTray")

        self.flyout = Flyout(self.root, self.store.all, self.refresh_all,
                             self.open_settings, self.cfg.warn_pct, self.cfg.crit_pct,
                             self.cfg.underuse_pct, self.cfg.underuse_days)

        from .tray import Tray  # imported late: pystray needs a desktop session
        self.trays: dict[str, Tray] = {}
        for name in names:
            snap = self.store.get(name)
            t = Tray(name, tooltip(snap, underuse_pct=self.cfg.underuse_pct,
                                   underuse_days=self.cfg.underuse_days), self._image(snap), self._post)
            t.start()
            self.trays[name] = t

        self.workers = [Worker(p, c.file_check_interval_s, c, self.store, self._on_update)
                        for p in providers]
        self._notified: dict[tuple, float] = {}
        self._last_wall = time.time()

    # ------------------------------------------------------ thread bridge
    def _post(self, action: str, provider: str) -> None:
        self.q.put((action, provider))

    def _on_update(self, snap: Snapshot) -> None:
        self.q.put(("update", snap))

    def _drain(self) -> None:
        try:
            while True:
                action, arg = self.q.get_nowait()
                self._handle(action, arg)
        except queue.Empty:
            pass
        self.root.after(200, self._drain)

    def _handle(self, action: str, arg) -> None:
        if action == "update":
            self._redraw(arg.provider)
            self._maybe_notify(arg)
            self.flyout.data_changed()
        elif action == "show":
            self.flyout.toggle()
        elif action == "refresh":
            self.refresh_all()
        elif action == "settings":
            self.open_settings()
        elif action == "log":
            winutil.open_file(app_dir() / "usage-tray.log")
        elif action == "menus":
            for t in self.trays.values():
                t.refresh_menu()
        elif action == "quit":
            self.quit()

    # ------------------------------------------------------------ actions
    def refresh_all(self) -> None:
        for w in self.workers:
            w.refresh()

    def open_settings(self) -> None:
        self.flyout.hide()
        if not (app_dir() / "config.json").exists():
            self.cfg.save()
        winutil.open_file(app_dir() / "config.json")

    def quit(self) -> None:
        log.info("Quitting")
        self.flyout.hide()
        for w in self.workers:
            w.stop()
        for t in self.trays.values():
            t.stop()
        self.root.after(300, self.root.destroy)

    # ------------------------------------------------------------ drawing
    def _image(self, snap: Snapshot):
        return render_icon(snap, self.cfg.warn_pct, self.cfg.crit_pct, self.light_taskbar,
                           size=winutil.tray_icon_size(), underuse_pct=self.cfg.underuse_pct,
                           underuse_days=self.cfg.underuse_days)

    def _redraw(self, provider: str) -> None:
        snap = self.store.get(provider)
        self.trays[provider].set(tooltip(snap, underuse_pct=self.cfg.underuse_pct,
                                         underuse_days=self.cfg.underuse_days), self._image(snap))

    def _tick(self) -> None:
        now = time.time()
        # A big jump in wall-clock time means the laptop just woke from sleep:
        # fetch straight away instead of waiting for the next timer.
        if now - self._last_wall > TICK_MS / 1000 * 3:
            log.info("Resumed from sleep, refreshing")
            self.refresh_all()
        self._last_wall = now
        self.light_taskbar = winutil.taskbar_is_light()
        for p in self.trays:
            self._redraw(p)
        self.root.after(TICK_MS, self._tick)

    def _maybe_notify(self, snap: Snapshot) -> None:
        if not self.cfg.notify or snap.status != OK or not snap.windows:
            return
        now = now_utc()
        for w in snap.windows:
            pct = w.effective_pct(now)
            level = (self.cfg.crit_pct if pct >= self.cfg.crit_pct
                     else self.cfg.warn_pct if pct >= self.cfg.warn_pct else 0)
            if not level:
                continue
            # One alert per level per window per reset period.
            key = (snap.provider, w.key, w.resets_at.isoformat() if w.resets_at else "")
            if self._notified.get(key, 0) >= level:
                continue
            self._notified[key] = level
            name = PROVIDER_NAME.get(snap.provider, snap.provider)
            msg = f"{w.label} limit at {pct:.0f}%."
            if w.resets_at:
                msg += f" Resets in {fmt_duration((w.resets_at - now).total_seconds())}."
            self.trays[snap.provider].notify(f"{name} usage", msg)

    # ---------------------------------------------------------------- run
    def run(self) -> None:
        log.info("UsageTray %s started", __version__)
        for w in self.workers:
            w.start()
        self.root.after(200, self._drain)
        self.root.after(TICK_MS, self._tick)
        self.root.mainloop()


def main() -> int:
    winutil.set_dpi_aware()
    setup_logging()
    if not winutil.acquire_single_instance():
        log.info("Already running; exiting")
        return 0
    try:
        App().run()
    except Exception:
        log.exception("Fatal error")
        raise
    return 0
