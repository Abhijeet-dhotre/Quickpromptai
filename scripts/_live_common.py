"""Shared helpers for the live Windows integration scripts.

These operate on the real desktop: enumerate windows, find the window a
script just created, and force-close only windows whose title carries a
test marker (never the user's own windows).
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

_USER32 = ctypes.windll.user32
_KERNEL32 = ctypes.windll.kernel32

_USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_USER32.GetWindowTextW.restype = ctypes.c_int
_USER32.IsWindowVisible.argtypes = [wintypes.HWND]
_USER32.IsWindowVisible.restype = wintypes.BOOL
_USER32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
]
_KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_KERNEL32.OpenProcess.restype = wintypes.HANDLE
_KERNEL32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]

_PROCESS_TERMINATE = 0x0001


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    _USER32.GetWindowTextW(hwnd, buf, 1024)
    return buf.value


def visible_windows() -> dict[int, str]:
    """Map hwnd -> title for every visible top-level window."""
    out: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        try:
            if _USER32.IsWindowVisible(hwnd):
                out[int(hwnd)] = window_title(hwnd)
        except Exception:
            pass
        return True

    _USER32.EnumWindows(cb, 0)
    return out


def find_new_window(before: dict[int, str], predicate=None,
                    timeout: float = 10.0) -> int | None:
    """Wait for a top-level window that was not in ``before``."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for hwnd, title in visible_windows().items():
            if hwnd not in before and (predicate is None or predicate(title)):
                return hwnd
        time.sleep(0.3)
    return None


def terminate_marker_windows(markers) -> int:
    """Force-close visible windows whose title contains a test marker.

    Only test markers are matched, so the user's own windows are never
    touched. Returns the number of processes terminated.
    """
    killed = 0
    for hwnd, title in visible_windows().items():
        if not any(marker in title for marker in markers):
            continue
        pid = wintypes.DWORD()
        _USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            continue
        handle = _KERNEL32.OpenProcess(_PROCESS_TERMINATE, False, pid.value)
        if handle:
            if _KERNEL32.TerminateProcess(handle, 0):
                killed += 1
            _KERNEL32.CloseHandle(handle)
    return killed
