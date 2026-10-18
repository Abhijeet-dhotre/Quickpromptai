"""Type dictated text straight into the window the user was writing in.

Dictation has a different shape from the enhance workflow. Enhance *reads* a
selection and shows a result, so it is fine for our popup to take focus.
Dictation is the opposite: the user presses the shortcut in Notepad/Word/VS
Code, talks, and expects the words to appear **at the caret they left behind**
- not inside QuickPrompt. Any window we raise in the meantime therefore breaks
the feature.

Safety rules (mirroring :mod:`windows_integration.replace`):
- The target window is remembered *before* the microphone opens, while the
  user's app is still in front, and re-focused before anything is typed.
- The foreground identity is verified after re-focusing: it must still belong
  to the process we recorded. If the user switched apps while talking, pasting
  into some unrelated window would be worse than doing nothing.
- The clipboard is snapshotted before the paste and restored afterwards (skipped
  if somebody else used the clipboard in the meantime).
- Hotkey modifiers are waited out before the keystroke, otherwise the still
  held physical Ctrl/Shift turns Ctrl+V into Ctrl+Shift+V.
"""
from __future__ import annotations

import time

from core.errors import QuickPromptError, WindowGoneError
from core.models import WindowInfo

from . import clipboard
from .capture import KeySender
from .keys import wait_modifiers_released
from .window_info import focus_window, get_foreground_window, same_process


class CaretPaster:
    """Pastes a transcript at the caret of a remembered window.

    All collaborators are injectable so the behaviour can be tested without a
    real desktop session.
    """

    def __init__(
        self,
        key_sender=None,
        window_provider=get_foreground_window,
        clipboard_module=clipboard,
        sleep=time.sleep,
        focus_fn=focus_window,
        modifiers_wait=wait_modifiers_released,
    ):
        self._keys = key_sender
        self._window_provider = window_provider
        self._clipboard = clipboard_module
        self._sleep = sleep
        self._focus_fn = focus_fn
        self._modifiers_wait = modifiers_wait

    @property
    def keys(self) -> KeySender:
        if self._keys is None:
            self._keys = KeySender()
        return self._keys

    # ------------------------------------------------------------------
    def capture_target(self) -> WindowInfo | None:
        """Remember the window the user is typing in (call before the mic)."""

        try:
            return self._window_provider()
        except Exception:
            return None

    def paste_into(self, target: WindowInfo | None, text: str) -> None:
        """Type ``text`` at the caret of ``target``.

        Raises :class:`WindowGoneError` when the target can no longer be typed
        into safely, or :class:`QuickPromptError` when the keystroke itself
        could not be delivered. Callers are expected to fall back to showing
        the transcript in-app so the words are never lost.
        """
        text = text or ""
        if not text.strip():
            raise QuickPromptError("There was nothing to type.")
        if target is None:
            raise WindowGoneError(
                "The window you started dictation in could not be found, so "
                "the text was not typed."
            )

        # The user is still holding Ctrl+Shift when the shortcut fires; typing
        # before those are released would produce Ctrl+Shift+V in the target.
        try:
            self._modifiers_wait(timeout=1.0, sleep=self._sleep)
        except Exception:
            pass

        self._focus_fn(target.hwnd)

        current = self._window_provider()
        if current is None:
            raise WindowGoneError(
                "No active window was detected, so the text was not typed."
            )
        if not same_process(target, current):
            raise WindowGoneError(
                "You switched to another window while dictating, so the text "
                "was not typed there."
            )

        snap = self._clipboard.snapshot()
        self._clipboard.set_text(text)
        our_sequence = self._clipboard.get_sequence()

        self._sleep(0.05)  # let the clipboard settle before the target reads it
        try:
            self.keys.send_paste()
        except Exception as exc:  # noqa: BLE001 - keyboard hook failures
            self._clipboard.restore(snap, expected_sequence=our_sequence)
            raise QuickPromptError(
                f"Could not type the transcript ({exc.__class__.__name__})."
            ) from exc
        self._sleep(0.15)  # give slow paste targets time to read the clipboard

        self._clipboard.restore(snap, expected_sequence=our_sequence)