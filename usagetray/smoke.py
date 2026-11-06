"""`UsageTray.exe --smoke-test`: checks a packaged build has everything it
needs (tray backend, tkinter, fonts, icon drawing) without showing anything.
Exit code 0 = OK. Used by the release workflow before publishing the .exe."""

from __future__ import annotations

import sys
from datetime import timedelta


def run() -> int:
    try:
        import tkinter  # the details panel

        import pystray  # picks the platform backend at import time

        from . import app  # whole app imports cleanly
        from . import winutil
        from .icon import render_icon, tooltip
        from .model import OK, Snapshot, Window, now_utc
        from .tray import _icon_class

        n = now_utc()
        snap = Snapshot("claude", OK, [
            Window("five_hour", "5-hour", 22, n + timedelta(hours=2), True, 5 * 3600),
            Window("seven_day", "Weekly", 6, n + timedelta(days=1), False, 7 * 86400),
        ], n, n)
        img = render_icon(snap, size=winutil.tray_icon_size())
        assert img.getbbox() is not None
        assert "5-hour" in tooltip(snap)
        assert _icon_class() is not None and pystray.Icon is not None
        assert tkinter.Tk is not None and app.main is not None
        return 0
    except Exception as e:  # never raise: a windowed .exe would show an error dialog
        try:
            sys.stderr.write(f"smoke test failed: {e!r}\n")
        except Exception:
            pass
        return 1
