"""Small Windows helpers via ctypes/winreg (stdlib only). Every function has a
harmless fallback so the rest of the app can be tested on other systems."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from . import APP_NAME

log = logging.getLogger(__name__)
IS_WIN = sys.platform == "win32"


def set_dpi_aware() -> None:
    """Crisp text and correct pixel positions on scaled (125%/150%) displays."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def tray_icon_size() -> int:
    """Pixel size Windows draws tray icons at on the taskbar's monitor
    (16 at 100% scaling, 20 at 125%, 24 at 150%, 32 at 200%)."""
    if not IS_WIN:
        return 32
    u = ctypes.windll.user32
    try:
        hwnd = u.FindWindowW("Shell_TrayWnd", None)
        dpi = u.GetDpiForWindow(hwnd) if hwnd else 0
        if not dpi:
            dpi = u.GetDpiForSystem()
        size = u.GetSystemMetricsForDpi(49, dpi)  # SM_CXSMICON
        if size:
            return int(size)
    except Exception:
        pass
    return int(u.GetSystemMetrics(49) or 16)


def work_area() -> Optional[tuple[int, int, int, int]]:
    """Desktop area not covered by the taskbar: (left, top, right, bottom)."""
    if not IS_WIN:
        return None
    from ctypes import wintypes
    rect = wintypes.RECT()
    if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
        return rect.left, rect.top, rect.right, rect.bottom
    return None


_mutex = None


def acquire_single_instance() -> bool:
    """False if UsageTray is already running."""
    global _mutex
    if not IS_WIN:
        return True
    k32 = ctypes.windll.kernel32
    _mutex = k32.CreateMutexW(None, False, f"Local\\{APP_NAME}-single-instance")
    return k32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def on_battery() -> bool:
    if not IS_WIN:
        return False

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

    s = SYSTEM_POWER_STATUS()
    try:
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return s.ACLineStatus == 0
    except Exception:
        pass
    return False


def _personalize(value: str, default: int = 0) -> int:
    if not IS_WIN:
        return default
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return int(winreg.QueryValueEx(k, value)[0])
    except OSError:
        return default


def taskbar_is_light() -> bool:
    return _personalize("SystemUsesLightTheme", 0) == 1


def apps_are_light() -> bool:
    return _personalize("AppsUseLightTheme", 0) == 1


def round_corners(tk_window) -> None:
    """Windows 11 rounded corners + shadow for a borderless Tk window."""
    if not IS_WIN:
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(tk_window.winfo_id())
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


# ------------------------------------------------------------ autostart
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):  # packaged .exe
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    if pyw.exists():
        exe = pyw
    main = Path(__file__).resolve().parents[1] / "main.py"
    return f'"{exe}" "{main}"'


def autostart_enabled() -> bool:
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if not IS_WIN:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass
    log.info("Start with Windows: %s", enabled)


def open_file(path: Path) -> None:
    if IS_WIN:
        os.startfile(str(path))  # noqa: S606 - opens in the user's default editor
