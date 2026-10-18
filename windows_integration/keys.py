"""Physical keyboard-state helpers (Win32 GetAsyncKeyState).

Used to wait for the user to release the hotkey's modifier keys before any
keystroke is simulated. Without this, the still-held physical Ctrl/Shift
combine with the injected Ctrl+C and the target app receives the wrong
combination (e.g. Ctrl+Shift+C = "Inspect Element" in browsers).
"""
from __future__ import annotations

import time

from ctypes import c_int, c_short, windll

_USER32 = windll.user32
_USER32.GetAsyncKeyState.argtypes = [c_int]
_USER32.GetAsyncKeyState.restype = c_short

# Virtual-key codes of every possible hotkey modifier.
MODIFIER_VIRTUAL_KEYS = {
    0x10,  # VK_SHIFT  (either side reports this via GetAsyncKeyState)
    0x11,  # VK_CONTROL
    0x12,  # VK_MENU   (Alt)
    0x5B,  # VK_LWIN
    0x5C,  # VK_RWIN
}


def is_key_down(vk: int) -> bool:
    """True while the given virtual key is physically held down."""
    return bool(_USER32.GetAsyncKeyState(vk) & 0x8000)


def modifiers_held() -> bool:
    """True while any modifier key (Ctrl/Shift/Alt/Win) is held down."""
    return any(is_key_down(vk) for vk in MODIFIER_VIRTUAL_KEYS)


def wait_modifiers_released(
    timeout: float = 1.5,
    poll: float = 0.02,
    sleep=time.sleep,
    clock=time.monotonic,
    still_down=None,
) -> bool:
    """Block until no modifier key is held (or the timeout expires).

    Returns True when the keyboard is clean. ``still_down`` is injectable
    for tests (defaults to the real GetAsyncKeyState check).
    """
    check = still_down or modifiers_held
    deadline = clock() + timeout
    while clock() < deadline:
        if not check():
            return True
        sleep(poll)
    return not check()
