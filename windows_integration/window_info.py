"""Foreground-window identification (used to protect the replace workflow)."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from core.models import WindowInfo

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _window_title(hwnd: int) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _process_name(pid: int) -> str:
    if not pid:
        return ""
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        ok = _kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        )
        return os.path.basename(buffer.value) if ok else ""
    finally:
        _kernel32.CloseHandle(handle)


def get_foreground_window() -> WindowInfo | None:
    """Identity of the currently active window (hwnd, pid, title, process)."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return WindowInfo(
        hwnd=int(hwnd),
        pid=int(pid.value),
        title=_window_title(int(hwnd)),
        process_name=_process_name(int(pid.value)),
    )


def same_process(a: WindowInfo | None, b: WindowInfo | None) -> bool:
    """True when both window identities belong to the same process.

    The process ID is authoritative; process name is only a fallback for the
    rare case where a PID could not be resolved.
    """
    if a is None or b is None:
        return False
    if a.pid and b.pid:
        return a.pid == b.pid
    if a.process_name and b.process_name:
        return a.process_name.lower() == b.process_name.lower()
    return False


def focus_window(hwnd: int, timeout: float = 0.8) -> bool:
    """Bring ``hwnd`` back to the foreground (best-effort, verified).

    Windows only honours SetForegroundWindow from the current foreground
    process — which is exactly our situation when the user clicks a button in
    our popup. ``SwitchToThisWindow`` is used as a fallback.
    """
    import time

    if not hwnd:
        return False
    try:
        if _user32.GetForegroundWindow() == hwnd:
            return True
        _user32.SetForegroundWindow(hwnd)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _user32.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.03)
        _user32.SwitchToThisWindow(hwnd, True)  # undocumented but reliable
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _user32.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.03)
    except Exception:
        return False
    return False
