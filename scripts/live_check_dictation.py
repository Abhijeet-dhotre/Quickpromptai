"""Live Windows integration check: dictation types into the original window.

Verifies, on a real interactive desktop:
1. ``capture_target()`` records the window the user was typing in.
2. ``paste_into()`` re-focuses that window and types the transcript at the
   caret (the behaviour the dictation shortcut depends on).
3. The user's clipboard is handed back afterwards.
4. Switching to another window mid-dictation is refused instead of typing the
   words somewhere unintended.

Run manually:  python scripts/live_check_dictation.py
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

TRANSCRIPT = "QuickPrompt dictation check 98765"
PREFIX = "before"
MARKERS = (TRANSCRIPT, PREFIX)


def main() -> int:
    from pywinauto import Desktop

    from windows_integration import clipboard
    from windows_integration.caret import CaretPaster
    from windows_integration.window_info import focus_window, get_foreground_window

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
    if win is None:
        wins = notepad_windows()
        win = wins[0] if wins else None
    if win is None:
        print("    FAIL: no Notepad window appeared")
        proc.kill()
        return 1
    win = desk.window(handle=win.handle)
    hwnd = win.handle

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

    # Put the caret after some existing text so we also prove the transcript
    # lands *at the caret* rather than replacing the document.
    from pynput.keyboard import Controller

    kb = Controller()
    for ch in PREFIX:
        kb.type(ch)
        time.sleep(0.012)
    time.sleep(0.3)

    user_clipboard_before = clipboard.snapshot()

    print("[2] capturing the dictation target (what the user is typing in)…")
    if not ensure_notepad_focused():
        print("    FAIL: focus lost before capture — aborting.")
        proc.kill()
        return 1
    paster = CaretPaster()
    target = paster.capture_target()
    ok_target = target is not None and target.hwnd == hwnd
    print(f"    target: {target}")

    print("[3] typing the transcript into that window…")
    try:
        paster.paste_into(target, TRANSCRIPT)
    except Exception as exc:
        proc.kill()
        print(f"    FAIL: {exc}")
        return 1

    print("[4] verifying Notepad content…")
    time.sleep(0.8)
    title_after = win.window_text()
    # Win11 Notepad mirrors its content into the window title but truncates
    # it, so assert on the stem plus a distinctive leading slice rather than
    # the whole transcript.
    probe = (PREFIX + TRANSCRIPT[:12]).replace(" ", "")
    ok_paste = probe in title_after.replace(" ", "")
    print(f"    notepad title after paste: {title_after!r}")
    print(f"    transcript typed at the caret: {ok_paste}")

    clip_ok = (
        not user_clipboard_before.had_text
        or clipboard.get_text() == user_clipboard_before.text
    )
    print(f"    clipboard restored: {clip_ok}")

    print("[5] refusal path: focus moved elsewhere mid-dictation…")
    other = get_foreground_window()
    moved = other is not None and other.hwnd != hwnd
    # Force the mismatch either way: WindowInfo is frozen, so build a copy that
    # points at a process we are definitely not typing into.
    import dataclasses

    stale = dataclasses.replace(target, pid=target.pid + 100000)
    if not moved:
        focus_window(hwnd)
        time.sleep(0.2)
    refused = False
    try:
        paster.paste_into(stale, "SHOULD-NOT-BE-TYPED")
    except Exception as exc:
        refused = type(exc).__name__ == "WindowGoneError"
        print(f"    refused with: {exc}")
    time.sleep(0.3)
    leaked = "SHOULD-NOT-BE-TYPED" in win.window_text()
    print(f"    refusal raised: {refused}")
    print(f"    nothing leaked into Notepad: {not leaked}")

    proc.kill()
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    terminate_marker_windows(MARKERS)

    passed = all((ok_target, ok_paste, clip_ok, refused, not leaked))
    print()
    print("RESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())