"""Selected-text capture with a layered, research-backed strategy.

Capture cascade (all steps restore the user's clipboard afterwards):

0. **Wait for hotkey modifiers to be released.** The user's physical Ctrl /
   Shift are still down for ~100 ms after pressing Ctrl+Shift+E. Sending the
   copy keystroke during that window makes the target receive Ctrl+Shift+C
   (in browsers: "Inspect Element") instead of Ctrl+C. Established hotkey
   tools wait for release first — we do too.
1. **WM_COPY message** to the focused control (GetGUIThreadInfo). Zero
   keystrokes, zero side effects; works in classic Win32 Edit/RichEdit
   controls (Notepad, dialogs, many native apps).
2. **Ctrl+C keystroke** — the universal fallback.
3. **Ctrl+Insert keystroke** — for apps where Ctrl+C means something else
   (some editors/terminals).

Documented limitations: secure input fields, elevated (admin) target windows
from a non-elevated QuickPrompt process (detected and reported precisely),
remote-desktop sessions, legacy consoles, and custom-drawn editors.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from core.errors import EmptySelectionError, QuickPromptError
from core.models import CaptureResult

from . import clipboard
from .elevation import is_process_elevated
from .keys import wait_modifiers_released
from .window_info import get_foreground_window, same_process

# ----------------------------------------------------------------------
# WM_COPY support (layer 1)
# ----------------------------------------------------------------------
_WM_COPY = 0x0301


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


_user32 = ctypes.windll.user32
_user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(_GUITHREADINFO)]
_user32.GetGUIThreadInfo.restype = wintypes.BOOL
_user32.SendNotifyMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
_user32.SendNotifyMessageW.restype = wintypes.BOOL


def focused_control_of_foreground() -> int:
    """hwnd of the focused control inside the foreground thread (or 0)."""
    info = _GUITHREADINFO()
    info.cbSize = ctypes.sizeof(info)
    if not _user32.GetGUIThreadInfo(0, ctypes.byref(info)):
        return 0
    return int(info.hwndFocus or 0)


def send_wm_copy() -> bool:
    """Ask the focused control to copy its selection to the clipboard.

    Returns True when the message was delivered to some control. Whether a
    selection actually existed is judged by the caller via the clipboard
    sequence number.
    """
    hwnd = focused_control_of_foreground()
    if not hwnd:
        return False
    try:
        return bool(_user32.SendNotifyMessageW(hwnd, _WM_COPY, 0, 0))
    except Exception:
        return False


# ----------------------------------------------------------------------
# Keystroke senders
# ----------------------------------------------------------------------
class KeySender:
    """Sends synthetic copy/paste keystrokes to the focused window."""

    def __init__(self):
        from pynput.keyboard import Controller, Key  # lazy import

        self._kb = Controller()
        self._key = Key

    def _combo(self, key) -> None:
        with self._kb.pressed(self._key.ctrl):
            self._kb.press(key)
            self._kb.release(key)

    def send_copy(self) -> None:
        """Primary copy shortcut (works in most applications)."""
        self._combo("c")

    def send_copy_alternate(self) -> None:
        """Ctrl+Insert — a Windows-native copy alternative."""
        self._combo(self._key.insert)

    def send_paste(self) -> None:
        self._combo("v")


# ----------------------------------------------------------------------
# Capturer
# ----------------------------------------------------------------------
class SelectionCapturer:
    """Captures the current text selection without destroying the clipboard."""

    def __init__(
        self,
        key_sender=None,
        window_provider=get_foreground_window,
        clipboard_module=clipboard,
        sleep=time.sleep,
        clock=time.monotonic,
        poll_interval: float = 0.05,
        default_timeout: float = 2.0,
        modifiers_wait=wait_modifiers_released,
        wm_copy_sender=send_wm_copy,
        elevated_check=is_process_elevated,
    ):
        self._keys = key_sender
        self._window_provider = window_provider
        self._clipboard = clipboard_module
        self._sleep = sleep
        self._clock = clock
        self.poll_interval = poll_interval
        self.default_timeout = default_timeout
        self._modifiers_wait = modifiers_wait
        self._wm_copy_sender = wm_copy_sender
        self._elevated_check = elevated_check

    @property
    def keys(self) -> KeySender:
        if self._keys is None:
            self._keys = KeySender()
        return self._keys

    # ------------------------------------------------------------------
    def _stable_foreground_window(self):
        """Read the foreground window twice to ride out focus transitions."""
        window = self._window_provider()
        self._sleep(0.03)
        settled = self._window_provider()
        if settled is not None and window is not None and not same_process(window, settled):
            return settled
        return settled or window

    def _poll_for_change(self, before: int, deadline: float) -> tuple[str | None, int]:
        """Wait until the clipboard sequence changes; return (text, sequence)."""
        read_sequence = before
        while self._clock() < deadline:
            self._sleep(self.poll_interval)
            current = self._clipboard.get_sequence()
            if current != before:
                read_sequence = current
                text = self._clipboard.get_text()
                if text:
                    return text, read_sequence
        return None, read_sequence

    def _empty_selection_error(self, window) -> EmptySelectionError:
        """Build a precise empty-selection error (with admin-app hint)."""
        message = (
            "No text selection was found. Select some text in the active "
            "window and press the shortcut again."
        )
        try:
            if window is not None and self._elevated_check(window.pid):
                message = (
                    "The active window runs as administrator, so its text "
                    "cannot be captured. Either run QuickPrompt AI as "
                    "administrator too, or copy the text manually (Ctrl+C) "
                    "and use the Enhance tab."
                )
        except Exception:
            pass
        return EmptySelectionError(message)

    # ------------------------------------------------------------------
    def capture(self, timeout: float | None = None) -> CaptureResult:
        timeout = self.default_timeout if timeout is None else timeout

        # --- 0) wait until the user released the hotkey modifiers -----
        # Without this the still-held physical Ctrl/Shift corrupt the
        # simulated keystroke (Ctrl+Shift+C instead of Ctrl+C).
        self._modifiers_wait(timeout=1.5, sleep=self._sleep, clock=self._clock)

        window = self._stable_foreground_window()
        if window is None:
            raise QuickPromptError("No active window was detected. Try again.")

        snap = self._clipboard.snapshot()
        before = snap.sequence
        deadline_total = self._clock() + timeout

        text: str | None = None
        read_sequence = before
        try:
            # --- 1) WM_COPY: zero-keystroke capture -------------------
            try:
                if self._wm_copy_sender():
                    text, read_sequence = self._poll_for_change(
                        before, self._clock() + 0.15
                    )
            except Exception:
                pass

            # --- 2) Ctrl+C --------------------------------------------
            if text is None:
                try:
                    self.keys.send_copy()
                except QuickPromptError:
                    raise
                except Exception as exc:
                    raise QuickPromptError(
                        f"Could not send the copy keystroke ({exc.__class__.__name__})."
                    )
                first_deadline = self._clock() + max(0.4, timeout * 0.4)
                text, read_sequence = self._poll_for_change(
                    before, min(first_deadline, deadline_total)
                )

            # --- 3) Ctrl+Insert fallback -------------------------------
            if text is None:
                try:
                    self.keys.send_copy_alternate()
                except Exception:
                    pass  # not fatal; fall through to the empty-selection error
                text, read_sequence = self._poll_for_change(before, deadline_total)

            if not text:
                raise self._empty_selection_error(window)
            return CaptureResult(text=text, window=window, captured_at=time.time())
        finally:
            # Always try to give the user their clipboard back.
            final_sequence = (
                read_sequence if text else self._clipboard.get_sequence()
            )
            if final_sequence != before:
                self._clipboard.restore(snap, expected_sequence=final_sequence)
