"""Safe replacement of the original selection with the enhanced text.

Safety rules:
- Nothing is pasted unless the user pressed the Replace button.
- The original window is brought back to the foreground first (the user
  clicked a button in our popup, so our process holds focus). Only then is
  the foreground identity verified: it must belong to the same process that
  was captured; otherwise the replacement is refused with a clear error and
  the user is told to copy the result instead.
- A fresh clipboard snapshot is taken before pasting and restored afterwards
  (skipped if someone else used the clipboard in the meantime).
- The paste is best-effort and reported as unverified: the target app decides
  what actually happens, so the user should check the result.
"""
from __future__ import annotations

import time

from core.errors import ReplaceError, WindowGoneError
from core.models import CaptureResult, ReplaceResult

from . import clipboard
from .capture import KeySender
from .window_info import focus_window, get_foreground_window, same_process


class TextReplacer:
    def __init__(
        self,
        key_sender=None,
        window_provider=get_foreground_window,
        clipboard_module=clipboard,
        sleep=time.sleep,
        focus_fn=focus_window,
    ):
        self._keys = key_sender
        self._window_provider = window_provider
        self._clipboard = clipboard_module
        self._sleep = sleep
        self._focus_fn = focus_fn

    @property
    def keys(self) -> KeySender:
        if self._keys is None:
            self._keys = KeySender()
        return self._keys

    def replace(self, capture: CaptureResult, new_text: str) -> ReplaceResult:
        text = (new_text or "").strip()
        if not text:
            raise ReplaceError("The enhanced result is empty — nothing to paste.")

        # 1) Bring the original window back before anything else: our popup
        #    currently holds focus, and pasting must land in the target app.
        self._focus_fn(capture.window.hwnd)

        # 2) Verify the foreground window still belongs to the captured app.
        current = self._window_provider()
        if current is None:
            raise WindowGoneError(
                "No active window was detected, so the selection was not "
                "replaced. Copy the result and paste it manually instead."
            )
        if not same_process(capture.window, current):
            raise WindowGoneError()

        # 3) Swap clipboard, paste, restore.
        snap = self._clipboard.snapshot()
        self._clipboard.set_text(text)
        our_sequence = self._clipboard.get_sequence()

        self._sleep(0.05)  # let the clipboard settle before the target reads it
        try:
            self.keys.send_paste()
        except Exception as exc:
            self._clipboard.restore(snap, expected_sequence=our_sequence)
            raise ReplaceError(
                f"Could not send the paste keystroke ({exc.__class__.__name__})."
            )
        self._sleep(0.15)  # give slow paste targets time to read the clipboard

        restored = self._clipboard.restore(snap, expected_sequence=our_sequence)
        note = (
            "" if restored
            else " Your previous clipboard content could not be restored."
        )
        return ReplaceResult(
            ok=True,
            verified=False,
            message=(
                "Pasted into the active window." + note +
                " Verify the result in the target application."
            ),
        )
