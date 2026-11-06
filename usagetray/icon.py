"""Draws the tray icon and builds the hover text.

Icon layout (one icon per provider), drawn at the exact pixel size Windows
uses for tray icons so the numbers stay sharp:

    ┃ 22%    <- top: 5-hour usage
    ┃ !6%    <- bottom: weekly usage ("!" = under 50% used with <= 2 days left)
    ^ stripe colour says which tool (Claude = clay, Codex = blue)

Number colour shows how much is LEFT: green (plenty) -> yellow -> amber -> red
(nearly gone). Grey numbers = stale data. Amber stripe tip = not live.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from .model import ERROR, LOADING, OK, Snapshot, Window, fmt_ago, fmt_duration, now_utc

PROVIDER_NAME = {"claude": "Claude", "codex": "Codex"}
PROVIDER_COLOR = {"claude": (217, 119, 87), "codex": (122, 162, 247)}

# Colour stops by "used %" for dark and light taskbars (light ones are darker
# so they stay readable on a pale background).
_DARK = dict(green=(76, 207, 138), yellow=(214, 214, 74), amber=(255, 176, 46), red=(255, 92, 84),
             grey=(165, 165, 165))
_LIGHT = dict(green=(16, 124, 65), yellow=(130, 120, 0), amber=(178, 98, 0), red=(196, 30, 30),
              grey=(110, 110, 110))
AMBER_DOT = (255, 176, 46)

# Kept for the panel (always dark-friendly palette unless told otherwise)
GREEN, AMBER, RED, GREY = _DARK["green"], _DARK["amber"], _DARK["red"], _DARK["grey"]


def _mix(a, b, t: float):
    t = max(0.0, min(1.0, t))
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def level_color(pct: float, warn: float = 80, crit: float = 95, light: bool = False):
    """Smooth colour for a usage %: lots left = green, little left = red."""
    p = _LIGHT if light else _DARK
    calm = max(0.0, warn - 30)            # e.g. 50%: start leaving green
    if pct <= calm:
        return p["green"]
    if pct <= warn - 10:                  # 50 -> 70: green -> yellow
        return _mix(p["green"], p["yellow"], (pct - calm) / max(1, warn - 10 - calm))
    if pct <= warn:                       # 70 -> 80: yellow -> amber
        return _mix(p["yellow"], p["amber"], (pct - (warn - 10)) / 10)
    if pct < crit:                        # 80 -> 95: amber -> red
        return _mix(p["amber"], p["red"], (pct - warn) / max(1, crit - warn))
    return p["red"]


# ------------------------------------------------------------------ fonts
_FONT_CANDIDATES = [
    ("bahnschrift.ttf", "SemiBold Condensed"),   # Windows 10/11: narrow, made for small digits
    ("seguisb.ttf", None),                       # Segoe UI Semibold
    ("arialbd.ttf", None),
    ("DejaVuSansCondensed-Bold.ttf", None),      # Linux (tests/previews)
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", None),
]


@lru_cache(maxsize=64)
def _font(px: int):
    for name, variation in _FONT_CANDIDATES:
        try:
            f = ImageFont.truetype(name, px)
            if variation:
                try:
                    f.set_variation_by_name(variation)
                except Exception:
                    pass
            return f
        except OSError:
            continue
    return ImageFont.load_default(px)


PCT_SCALE = 0.62   # the "%" is drawn smaller and raised, like a superscript
BANG = (255, 214, 10)   # "!" colour (dark taskbar)
BANG_LIGHT = (176, 110, 0)


def _digit_box(f):
    """(top, bottom) of digit ink relative to the baseline (negative = above)."""
    l, t, r, b = f.getbbox("0123456789", anchor="ls")
    return t, b


def _row_width(parts, px: int) -> float:
    big, small = _font(px), _font(max(5, round(px * PCT_SCALE)))
    w = 0.0
    for txt, kind in parts:
        w += (small if kind == "pct" else big).getlength(txt)
    return w


def _fit_px(rows, max_w: float, max_h: float) -> int:
    """Largest font size where every row fits the width and digits fit the height."""
    px = int(max_h * 1.6)
    while px > 5:
        t, b = _digit_box(_font(px))
        if (b - t) <= max_h and all(_row_width(r, px) <= max_w for r in rows):
            return px
        px -= 1
    return 5


# ------------------------------------------------------------------ icon
def render_icon(snap: Snapshot, warn: float = 80, crit: float = 95,
                light_taskbar: bool = False, size: int = 16,
                at: Optional[datetime] = None,
                underuse_pct: float = 50, underuse_days: float = 2) -> Image.Image:
    at = at or now_utc()
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pal = _LIGHT if light_taskbar else _DARK

    usable = snap.status not in (ERROR, LOADING) and bool(snap.windows)
    stale = snap.status != OK

    # identity stripe
    sw = max(2, round(S / 8))
    stripe = PROVIDER_COLOR.get(snap.provider, pal["grey"])
    d.rounded_rectangle([0, 0, sw - 1, S - 1], radius=max(1, sw // 2),
                        fill=stripe + ((255,) if usable else (110,)))

    gap = max(1, round(S / 16))
    x0 = sw + gap
    avail_w = S - x0
    row_h = S / 2
    pad = max(0.5, S / 32)

    # Each row is a list of (text, kind) parts: kind = "bang" | "num" | "pct"
    rows, colors = [], []
    for i, w in enumerate((snap.primary(), snap.weekly())):
        if not usable or w is None:
            rows.append([("–", "num")])
            colors.append(pal["grey"])
            continue
        pct = w.effective_pct(at)
        parts = [(str(int(round(pct))), "num"), ("%", "pct")]
        if i == 1 and w.underused(underuse_pct, underuse_days, at):
            parts.insert(0, ("!", "bang"))
        rows.append(parts)
        colors.append(pal["grey"] if stale else level_color(pct, warn, crit, light_taskbar))

    px = _fit_px(rows, avail_w, row_h - 2 * pad)
    big, small = _font(px), _font(max(5, round(px * PCT_SCALE)))
    dt, db = _digit_box(big)
    st, _ = _digit_box(small)
    for i, parts in enumerate(rows):
        width = _row_width(parts, px)
        x = x0 + round((avail_w - width) / 2)
        # baseline so the digits sit centred in their row, on whole pixels
        base = round(i * row_h + (row_h - (db - dt)) / 2 - dt)
        for txt, kind in parts:
            if kind == "pct":
                # raised: top of "%" lines up with the top of the digits
                d.text((x, base + dt - st), txt, font=small, fill=colors[i] + (255,), anchor="ls")
                x += small.getlength(txt)
            else:
                color = (BANG_LIGHT if light_taskbar else BANG) if kind == "bang" else colors[i]
                d.text((x, base), txt, font=big, fill=color + (255,), anchor="ls")
                x += big.getlength(txt)

    if snap.warning and usable:
        # "not live": the bottom of the stripe turns amber (doesn't cover digits)
        h = max(3, round(S * 0.4))
        d.rounded_rectangle([0, S - h, sw - 1, S - 1], radius=max(1, sw // 2),
                            fill=AMBER_DOT + (255,))
    return img


# ------------------------------------------------------------------ tooltip
def _line(w: Optional[Window], at: datetime) -> str:
    if w is None:
        return ""
    pct = w.effective_pct(at)
    s = f"{w.label}: {pct:.0f}% used"
    if w.resets_at is None:
        return s
    if w.has_reset(at):
        return s + " (just reset)"
    return s + f", resets in {fmt_duration((w.resets_at - at).total_seconds())}"


def tooltip(snap: Snapshot, at: Optional[datetime] = None,
            underuse_pct: float = 50, underuse_days: float = 2) -> str:
    """Hover text. Windows cuts tray tooltips at 127 characters."""
    at = at or now_utc()
    name = PROVIDER_NAME.get(snap.provider, snap.provider)
    if snap.plan:
        name += f" ({snap.plan.capitalize()})"
    if snap.status == LOADING:
        return f"{name}: loading..."
    if not snap.windows:
        return f"{name}: {snap.detail or 'no data'}"[:127]

    lines = [name, _line(snap.primary(), at), _line(snap.weekly(), at)]
    wk = snap.weekly()
    if wk and snap.status == OK and wk.underused(underuse_pct, underuse_days, at):
        lines.append(f"(!) Under {underuse_pct:.0f}% used with {fmt_duration(wk.time_left_s(at))} left")
    if snap.warning:
        lines.append("(!) Not live, click for details")
    elif snap.status != OK:
        lines.append(f"(!) {snap.detail or 'stale'}")
    if snap.as_of and snap.checked_at and (snap.checked_at - snap.as_of).total_seconds() > 90:
        lines.append(f"as of {fmt_ago(snap.as_of, at)}")
    text = "\n".join(line for line in lines if line).replace("…", "...")
    while len(text) > 127 and len(lines) > 3:  # drop extras after the numbers
        lines.pop()
        text = "\n".join(line for line in lines if line)
    return text[:127]
