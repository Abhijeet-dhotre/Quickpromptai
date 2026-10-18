"""Live Windows integration check: Notepad round-trip.

Verifies, on a real interactive desktop:
1. Selection capture (Ctrl+C simulation + clipboard restore)
2. Safe replacement (window refocus + Ctrl+V + clipboard restore)

Run manually:  python scripts/live_check_notepad.py
Exits 0 on success, 1 on failure. Opens and closes Notepad automatically.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _live_common import terminate_marker_windows  # noqa: E402

TEST_TEXT = "QuickPrompt live capture check 12345"
REPLACED = "REPLACED-BY-QUICKPROMPT-CHECK"
MARKERS = (TEST_TEXT, REPLACED, "QuickPrompt live capture check")


def main() -> int:
    from pynput.keyboard import Controller, Key
    from pywinauto import Desktop

    from windows_integration import clipboard
    from windows_integration.capture import SelectionCapturer
    from windows_integration.replace import TextReplacer

    if not clipboard.is_available():
        print("SKIP: clipboard access denied in this session.")
        return 1

    print("[1] launching Notepad and focusing it deterministically…")
    desk = Desktop(backend="win32")

    def notepad_windows():
        try:
            return desk.windows(title_re=".*Notepad.*", top_level_only=True,
                                visible_only=True)
        except Exception:
            return []

    known = {w.handle for w in notepad_windows()}
    proc = subprocess.Popen(["notepad.exe"])
    win = None
    deadline = time.time() + 10
    while time.time() < deadline and win is None:
        for w in notepad_windows():
            if w.handle not in known:
                win = w
                break
        time.sleep(0.3)
    if win is None:  # fall back: any visible Notepad window
        wins = notepad_windows()
        win = wins[0] if wins else None
    if win is None:
        print("    FAIL: no Notepad window appeared")
        proc.kill()
        return 1
    win = desk.window(handle=win.handle)  # re-wrap: gives us child_window()
    hwnd = win.handle

    from windows_integration.window_info import focus_window, get_foreground_window

    def ensure_notepad_focused(attempts=4) -> bool:
        """Focus Notepad and verify focus stuck — never type into other apps."""
        for _ in range(attempts):
            focus_window(hwnd)
            time.sleep(0.25)
            fg = get_foreground_window()
            if fg and "notepad" in fg.process_name.lower():
                return True
        return False

    if not ensure_notepad_focused():
        print("    SKIP/FAIL: could not keep Notepad focused "
              "(desktop appears to be in active use) — aborting without typing.")
        proc.kill()
        return 1

    kb = Controller()
    for ch in TEST_TEXT:  # type slowly: fast synthetic typing drops keys
        kb.type(ch)
        time.sleep(0.012)
    time.sleep(0.3)
    with kb.pressed(Key.ctrl):
        kb.press("a")
        kb.release("a")
    time.sleep(0.2)

    # Sanity check: Win11 Notepad mirrors its content in the window title,
    # which is more reliable than hunting for a classic Edit control.
    title = win.window_text()
    if TEST_TEXT[:25] not in title:
        print(f"    FAIL: typing did not land in Notepad (title: {title!r})")
        proc.kill()
        return 1
    print(f"    typing verified via Notepad title: {title!r}")

    user_clipboard_before = clipboard.snapshot()

    print("[2] capturing selection (real Ctrl+C keystroke)…")
    if not ensure_notepad_focused():
        print("    FAIL: focus lost before capture — aborting.")
        proc.kill()
        return 1
    with kb.pressed(Key.ctrl):
        kb.press("a")
        kb.release("a")
    time.sleep(0.2)
    try:
        capture = SelectionCapturer().capture(timeout=3.0)
    except Exception as exc:
        proc.kill()
        print(f"    FAIL: {exc}")
        return 1
    ok_capture = capture.text.strip() == TEST_TEXT
    print(f"    captured:        {capture.text!r}")
    print(f"    recorded window: {capture.window.process_name} / {capture.window.title!r}")
    clip_ok_capture = (
        not user_clipboard_before.had_text
        or clipboard.get_text() == user_clipboard_before.text
    )
    print(f"    clipboard restored after capture: {clip_ok_capture}")

    print("[3] replacing selection (refocus original window + Ctrl+V)…")
    result = TextReplacer().replace(capture, REPLACED)
    print(f"    replace ok={result.ok}")

    print("[4] verifying Notepad content…")
    time.sleep(0.8)
    title_after = win.window_text()
    ok_replace = REPLACED in title_after
    print(f"    notepad title after replace: {title_after!r}")
    print(f"    notepad contains replaced text: {ok_replace}")

    clip_ok_replace = (
        not user_clipboard_before.had_text
        or clipboard.get_text() == user_clipboard_before.text
    )
    print(f"    clipboard restored after replace: {clip_ok_replace}")

    proc.kill()
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    # Multi-process Notepads can outlive the spawned process — close any
    # surviving window that carries our test marker (and only those).
    terminate_marker_windows(MARKERS)

    passed = ok_capture and ok_replace and clip_ok_capture and clip_ok_replace
    print()
    print("RESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
