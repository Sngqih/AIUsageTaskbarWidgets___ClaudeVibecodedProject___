"""One system-tray icon per provider (pystray, each on its own thread).

On Windows a tray icon's hidden window belongs to the thread that created it,
so each Icon is created *and* run inside its own thread.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from typing import Callable

import pystray
from PIL import Image

from . import winutil

log = logging.getLogger(__name__)


def _icon_class():
    """On Windows, pystray loads icons at the *large* icon size and lets the
    taskbar shrink them, which blurs small text. This subclass loads the
    image at exactly its own pixel size (we render it at the tray size)."""
    base = pystray.Icon
    if not winutil.IS_WIN:
        return base
    try:
        from pystray._util import win32
    except Exception:
        return base

    class ExactSizeIcon(base):
        def _assert_icon_handle(self):
            if self._icon_handle:
                return
            try:
                w, h = self.icon.size
                fd, path = tempfile.mkstemp(".ico")
                os.close(fd)
                try:
                    self.icon.save(path, format="ICO", sizes=[(w, h)])
                    handle = win32.LoadImage(None, path, win32.IMAGE_ICON, w, h,
                                             win32.LR_LOADFROMFILE)
                finally:
                    os.unlink(path)
                if handle:
                    self._icon_handle = handle
                    return
            except Exception:
                log.debug("Exact-size icon load failed; using default", exc_info=True)
            super()._assert_icon_handle()

    return ExactSizeIcon


class Tray:
    def __init__(self, provider: str, title: str, image: Image.Image,
                 post: Callable[[str, str], None]):
        """post(action, provider) hands menu clicks to the UI thread."""
        self.provider = provider
        self.post = post
        self._initial = (title, image)
        self.icon: pystray.Icon | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"tray-{provider}", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(5)

    def _menu(self) -> pystray.Menu:
        P = self.provider
        return pystray.Menu(
            pystray.MenuItem("Show details", lambda: self.post("show", P), default=True),
            pystray.MenuItem("Refresh now", lambda: self.post("refresh", P)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start with Windows", self._toggle_autostart,
                             checked=lambda item: winutil.autostart_enabled()),
            pystray.MenuItem("Open settings file", lambda: self.post("settings", P)),
            pystray.MenuItem("Open log file", lambda: self.post("log", P)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit UsageTray", lambda: self.post("quit", P)),
        )

    def _toggle_autostart(self, icon, item) -> None:
        try:
            winutil.set_autostart(not winutil.autostart_enabled())
        except OSError:
            log.exception("Could not change autostart")
        self.post("menus", self.provider)

    def _run(self) -> None:
        title, image = self._initial
        self.icon = _icon_class()(f"UsageTray-{self.provider}", image, title, self._menu())

        def setup(icon):
            icon.visible = True
            self._ready.set()

        try:
            self.icon.run(setup=setup)
        except Exception:
            log.exception("Tray icon for %s stopped", self.provider)
            self._ready.set()

    # Called from the UI thread -----------------------------------------
    def set(self, title: str, image: Image.Image) -> None:
        if self.icon is None:
            return
        try:
            self.icon.icon = image
            self.icon.title = title
        except Exception:
            log.exception("Icon update failed")

    def refresh_menu(self) -> None:
        if self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def notify(self, title: str, message: str) -> None:
        if self.icon is not None:
            try:
                self.icon.notify(message, title)
            except Exception:
                log.debug("Notification failed", exc_info=True)

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
