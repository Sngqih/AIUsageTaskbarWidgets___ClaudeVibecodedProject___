"""The details panel that pops up above the tray when you click an icon."""

from __future__ import annotations

import time
import tkinter as tk
from datetime import datetime
from typing import Callable, Optional

from . import __version__, winutil
from .icon import PROVIDER_COLOR, PROVIDER_NAME, level_color
from .model import LOADING, OK, Snapshot, fmt_ago, fmt_duration, now_utc  # noqa: F401

DARK = dict(bg="#202020", card="#2b2b2b", fg="#ffffff", muted="#a0a0a0",
            track="#3d3d3d", pace="#6a6a6a", warn="#f5b83d", btn="#383838", btn_hover="#454545", border="#3a3a3a")
LIGHT = dict(bg="#f3f3f3", card="#ffffff", fg="#1a1a1a", muted="#5f5f5f",
             track="#e8e8e8", pace="#bdbdbd", warn="#9a6700", btn="#e9e9e9", btn_hover="#dddddd", border="#d6d6d6")
STALE_BAR = "#8a8a8a"
FONT = "Segoe UI"


def _freshness(s: Snapshot) -> str:
    if s.as_of and s.checked_at and (s.checked_at - s.as_of).total_seconds() > 90:
        return "as of " + fmt_ago(s.as_of)
    return "updated " + fmt_ago(s.checked_at or s.as_of)


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


class Flyout:
    WIDTH = 340  # at 100% scaling

    def __init__(self, root: tk.Tk, get_snapshots: Callable[[], list[Snapshot]],
                 on_refresh: Callable[[], None], on_settings: Callable[[], None],
                 warn: float, crit: float, underuse_pct: float = 50, underuse_days: float = 2):
        self.root = root
        self.get_snapshots = get_snapshots
        self.on_refresh = on_refresh
        self.on_settings = on_settings
        self.warn, self.crit = warn, crit
        self.underuse_pct, self.underuse_days = underuse_pct, underuse_days
        self.win: Optional[tk.Toplevel] = None
        self._tick_job = None
        self._hidden_at = 0.0
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)

    def px(self, v: float) -> int:
        return int(round(v * self.scale))

    # --------------------------------------------------------------- public
    @property
    def visible(self) -> bool:
        return self.win is not None

    def toggle(self) -> None:
        # Clicking the tray icon steals focus first, which already closed the
        # panel a moment ago; treat that click as "close", not "reopen".
        if not self.visible and time.monotonic() - self._hidden_at < 0.5:
            return
        if self.visible:
            self.hide()
        else:
            self.show()

    def show(self) -> None:
        self.hide()
        self.t = LIGHT if winutil.apps_are_light() else DARK
        w = tk.Toplevel(self.root)
        self.win = w
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=self.t["border"])
        self.body = tk.Frame(w, bg=self.t["bg"], padx=self.px(14), pady=self.px(12))
        self.body.pack(fill="both", expand=True, padx=1, pady=1)
        self._build()
        self._place()
        w.deiconify()
        winutil.round_corners(w)
        w.lift()
        w.focus_force()
        w.bind("<Escape>", lambda e: self.hide())
        w.bind("<FocusOut>", lambda e: self.root.after(150, self._maybe_hide))
        self._tick()

    def hide(self) -> None:
        if self._tick_job:
            self.root.after_cancel(self._tick_job)
            self._tick_job = None
        if self.win is not None:
            self.win.destroy()
            self.win = None
            self._hidden_at = time.monotonic()

    def data_changed(self) -> None:
        """Called by the app when new numbers arrive."""
        if self.visible:
            self._rebuild()

    # ------------------------------------------------------------- internal
    def _maybe_hide(self) -> None:
        if self.win is not None and self.win.focus_get() is None:
            self.hide()

    def _place(self) -> None:
        w = self.win
        w.update_idletasks()
        ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
        area = winutil.work_area() or (0, 0, w.winfo_screenwidth(), w.winfo_screenheight() - 48)
        left, top, right, bottom = area
        mx, my = w.winfo_pointerxy()
        gap = self.px(12)
        x = min(max(mx - ww // 2, left + gap), right - ww - gap)
        # Taskbar at the top of the screen -> open downwards; otherwise upwards.
        y = top + gap if my < top + 10 else bottom - wh - gap
        w.geometry(f"+{x}+{y}")

    def _rebuild(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self._build()
        self._place()

    def _label(self, parent, text, size=9, bold=False, color=None, **kw):
        return tk.Label(parent, text=text, bg=kw.pop("bg", parent["bg"]),
                        fg=color or self.t["fg"],
                        font=(FONT, size, "bold" if bold else "normal"), **kw)

    def _build(self) -> None:
        t = self.t
        self._live: list[tuple[tk.Label, Callable[[], str]]] = []
        snaps = self.get_snapshots()

        head = tk.Frame(self.body, bg=t["bg"])
        head.pack(fill="x", pady=(0, self.px(8)))
        self._label(head, "AI usage limits", 11, bold=True).pack(side="left")

        for snap in snaps:
            self._card(snap)

        foot = tk.Frame(self.body, bg=t["bg"])
        foot.pack(fill="x", pady=(self.px(6), 0))
        self._button(foot, "↻  Refresh", self._refresh).pack(side="left")
        self._button(foot, "Settings", self.on_settings).pack(side="left", padx=(self.px(6), 0))
        self._label(foot, f"v{__version__}", 8, color=t["muted"]).pack(side="right")

    def _button(self, parent, text, cmd):
        t = self.t
        b = tk.Label(parent, text=text, bg=t["btn"], fg=t["fg"], font=(FONT, 9),
                     padx=self.px(10), pady=self.px(3), cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(bg=t["btn_hover"]))
        b.bind("<Leave>", lambda e: b.configure(bg=t["btn"]))
        return b

    def _refresh(self) -> None:
        self.on_refresh()

    def _card(self, snap: Snapshot) -> None:
        t = self.t
        card = tk.Frame(self.body, bg=t["card"], padx=self.px(12), pady=self.px(10))
        card.pack(fill="x", pady=(0, self.px(8)))

        top = tk.Frame(card, bg=t["card"])
        top.pack(fill="x")
        dot = tk.Canvas(top, width=self.px(10), height=self.px(10), bg=t["card"], highlightthickness=0)
        dot.create_oval(0, 0, self.px(9), self.px(9), fill=_hex(PROVIDER_COLOR[snap.provider]), outline="")
        dot.pack(side="left", padx=(0, self.px(6)))
        name = PROVIDER_NAME.get(snap.provider, snap.provider)
        self._label(top, name, 10, bold=True).pack(side="left")
        if snap.plan:
            self._label(top, snap.plan.upper(), 7, color=t["muted"]).pack(side="left", padx=(self.px(6), 0))
        lbl = self._label(top, "", 8, color=t["muted"])
        lbl.pack(side="right")
        self._live.append((lbl, lambda s=snap: _freshness(s)))

        if snap.status == LOADING:
            self._label(card, "Loading…", 9, color=t["muted"]).pack(anchor="w", pady=(self.px(6), 0))
        for win in snap.windows:
            row = tk.Frame(card, bg=t["card"])
            row.pack(fill="x", pady=(self.px(8), 0))
            line = tk.Frame(row, bg=t["card"])
            line.pack(fill="x")
            self._label(line, win.label, 9).pack(side="left")
            pct_lbl = self._label(line, "", 9, bold=True,
                                  color=STALE_BAR if snap.status != OK else _hex(level_color(
                                      win.effective_pct(), self.warn, self.crit, self.t is LIGHT)))
            pct_lbl.pack(side="right")
            self._live.append((pct_lbl, lambda w=win: f"{w.effective_pct():.0f}% used · {100 - w.effective_pct():.0f}% left"))

            bar = tk.Canvas(row, height=self.px(6), bg=t["card"], highlightthickness=0,
                            width=self.px(self.WIDTH - 52))
            bar.pack(fill="x", pady=(self.px(3), self.px(2)))
            self._draw_bar(bar, win, snap.status != OK)

            reset = self._label(row, "", 8, color=t["muted"])
            reset.pack(anchor="w")
            self._live.append((reset, lambda w=win: self._reset_text(w)))
            if win.is_weekly() and snap.status == OK and \
                    win.underused(self.underuse_pct, self.underuse_days):
                self._label(row, f"! Under {self.underuse_pct:.0f}% used with "
                                 f"{fmt_duration(win.time_left_s())} left", 8, bold=True,
                            color=t["warn"]).pack(anchor="w", pady=(self.px(2), 0))

        if snap.warning:
            self._label(card, "⚠ " + snap.warning, 8, color=t["warn"],
                        wraplength=self.px(self.WIDTH - 52), justify="left"
                        ).pack(anchor="w", pady=(self.px(8), 0))
        if snap.detail and (snap.status != OK or not snap.windows):
            self._label(card, snap.detail, 8, color=t["warn"] if snap.status != OK else t["muted"],
                        wraplength=self.px(self.WIDTH - 52), justify="left"
                        ).pack(anchor="w", pady=(self.px(8), 0))
        elif snap.detail:
            self._label(card, snap.detail, 8, color=t["muted"]).pack(anchor="w", pady=(self.px(6), 0))

    def _draw_bar(self, c: tk.Canvas, win, stale: bool) -> None:
        c.update_idletasks()
        w = int(c["width"])
        h = self.px(6)
        r = h / 2
        c.create_rectangle(r, 0, w - r, h, fill=self.t["track"], outline="")
        c.create_oval(0, 0, h, h, fill=self.t["track"], outline="")
        c.create_oval(w - h, 0, w, h, fill=self.t["track"], outline="")
        exp = win.expected_pct() if win.is_weekly() else None
        if exp and exp > 0:
            # grey bar = where you'd be by now using the week evenly
            ew = max(h, w * exp / 100.0)
            c.create_oval(0, 0, h, h, fill=self.t["pace"], outline="")
            c.create_rectangle(r, 0, ew - r, h, fill=self.t["pace"], outline="")
            c.create_oval(ew - h, 0, ew, h, fill=self.t["pace"], outline="")
        pct = win.effective_pct()
        if pct <= 0:
            return
        color = STALE_BAR if stale else _hex(level_color(pct, self.warn, self.crit, self.t is LIGHT))
        fw = max(h, w * pct / 100.0)
        c.create_oval(0, 0, h, h, fill=color, outline="")
        c.create_rectangle(r, 0, fw - r, h, fill=color, outline="")
        c.create_oval(fw - h, 0, fw, h, fill=color, outline="")

    @staticmethod
    def _reset_text(w) -> str:
        if w.resets_at is None:
            return "Reset time unknown"
        now = now_utc()
        if w.has_reset(now):
            return "Reset since last reading"
        local: datetime = w.resets_at.astimezone()
        when = local.strftime("%a %H:%M") if (w.resets_at - now).days >= 1 else local.strftime("%H:%M")
        text = f"Resets in {fmt_duration((w.resets_at - now).total_seconds())} · {when}"
        exp = w.expected_pct(now) if w.is_weekly() else None
        if exp is not None:
            text += f" · even pace {exp:.0f}%"
        return text

    def _tick(self) -> None:
        if self.win is None:
            return
        for lbl, fn in self._live:
            try:
                lbl.configure(text=fn())
            except tk.TclError:
                pass
        self._tick_job = self.root.after(1000, self._tick)
