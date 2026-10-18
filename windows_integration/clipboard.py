"""Windows clipboard access via ctypes (no extra dependencies).

Design goals:
- Never lose the user's clipboard content: every capture flow snapshots the
  clipboard first and restores it afterwards.
- Use ``GetClipboardSequenceNumber`` to detect whether the clipboard actually
  changed (robust even when the copied text happens to be identical).
- Only CF_UNICODETEXT is handled. Non-text clipboard contents (images, files)
  cannot be preserved across a capture — this limitation is documented.
"""
from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass
from ctypes import wintypes

from core.errors import ClipboardError

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
_user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.restype = wintypes.HGLOBAL


@dataclass
class ClipboardSnapshot:
    """Point-in-time state of the clipboard's text content."""

    text: str | None = None
    had_text: bool = False
    sequence: int = 0


def _open(retries: int = 8, delay: float = 0.02) -> bool:
    for _ in range(max(1, retries)):
        if _user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def get_sequence() -> int:
    """Current clipboard sequence number (changes on every clipboard write)."""
    return int(_user32.GetClipboardSequenceNumber())


def is_available() -> bool:
    """True when this process can open the clipboard (access check only)."""
    if _open(retries=1):
        _user32.CloseClipboard()
        return True
    return False


def get_text() -> str | None:
    """Read CF_UNICODETEXT, or None when the clipboard holds no text."""
    if not _user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
        return None
    if not _open():
        raise ClipboardError("The clipboard is locked by another application.")
    try:
        handle = _user32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return None
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def set_text(text: str) -> None:
    """Replace the clipboard's text content."""
    data = (text + "\0").encode("utf-16-le")
    if not _open():
        raise ClipboardError("The clipboard is locked by another application.")
    allocated = False
    try:
        _user32.EmptyClipboard()
        handle = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
        if not handle:
            raise ClipboardError("Windows refused to allocate clipboard memory.")
        allocated = True
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            raise ClipboardError("Could not access clipboard memory.")
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            _kernel32.GlobalUnlock(handle)
        if not _user32.SetClipboardData(_CF_UNICODETEXT, handle):
            raise ClipboardError(
                f"SetClipboardData failed (error {ctypes.GetLastError()})."
            )
        allocated = False  # the system now owns the handle
    finally:
        if allocated:
            _kernel32.GlobalFree(handle)
        _user32.CloseClipboard()


def snapshot() -> ClipboardSnapshot:
    """Capture the current text state and sequence number."""
    seq = get_sequence()
    try:
        text = get_text()
    except ClipboardError:
        text = None
    return ClipboardSnapshot(text=text, had_text=text is not None, sequence=seq)


def restore(snap: ClipboardSnapshot, expected_sequence: int | None = None) -> bool:
    """Restore a snapshot.

    When ``expected_sequence`` is given and the clipboard has changed since it
    was captured (someone else copied something in the meantime), the restore
    is skipped to avoid clobbering the user's newer clipboard content.

    Returns True when the text content was restored. Snapshots taken from a
    text-less clipboard cannot be restored (non-text formats are not
    preserved) and return False.
    """
    if snap is None or not snap.had_text:
        return False
    if expected_sequence is not None and get_sequence() != expected_sequence:
        return False
    try:
        set_text(snap.text or "")
    except ClipboardError:
        return False
    return True
