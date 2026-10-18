"""Live Windows hotkey check — reproduces the real user action exactly.

Registers the real global hotkey (Ctrl+Shift+E), selects text in a FRESH
Notepad document, then presses the hotkey WHILE KEEPING Ctrl+Shift HELD for
150 ms afterwards (humans do not release instantly — this is what used to
break the capture: the still-held Shift turned the simulated Ctrl+C into
Ctrl+Shift+C, which browsers bind to "Inspect Element").

Safety guards:
* never types into an existing/restored Notepad document (requires a fresh
  "Untitled" document, opening one with Ctrl+N if needed),
* aborts without typing if focus cannot be kept on the target window,
* only closes windows carrying our test marker.

Run manually:  python scripts/live_check_hotkey.py
Exits 0 on success, 1 on failure. NOTE: uses Ctrl+Shift+E — quit running
QuickPrompt instances first, or they will react too.
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _live_common import (  # noqa: E402
    find_new_window,
    terminate_marker_windows,
    visible_windows,
    window_title,
)

TEST_TEXT = "QuickPrompt hotkey check 98765"
COMBO = "Ctrl+Shift+E"
MARKERS = (TEST_TEXT, "QuickPrompt hotkey check")


def main() -> int:
    import subprocess

    from pynput.keyboard import Controller, Key

    from shortcuts.global_hotkey import GlobalHotkeyManager
    from windows_integration import clipboard
    from windows_integration.capture import SelectionCapturer
    from windows_integration.window_info import focus_window, get_foreground_window

    if not clipboard.is_available():
        print("SKIP: clipboard access denied in this session.")
        return 1

    results = {"capture": None, "error": None, "fired": 0}

    def on_hotkey() -> None:
        # Mimic the app: capture starts on a worker thread immediately.
        results["fired"] += 1

        def worker():
            try:
                results["capture"] = SelectionCapturer().capture(timeout=3.0)
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        threading.Thread(target=worker, daemon=True).start()

    manager = GlobalHotkeyManager(on_hotkey)
    canonical = manager.register(COMBO)
    print(f"[0] registered global hotkey: {canonical}")

    proc = None
    try:
        # ---- deterministic fresh Notepad document ------------------------
        before = visible_windows()
        proc = subprocess.Popen(["notepad.exe"])
        hwnd = find_new_window(before, predicate=lambda t: "Notepad" in t,
                               timeout=10)
        if hwnd is None:
            print("FAIL: no new Notepad window appeared")
            return 1

        def focused_on_notepad() -> bool:
            focus_window(hwnd)
            time.sleep(0.25)
            fg = get_foreground_window()
            return bool(fg and "notepad" in fg.process_name.lower()
                        and fg.hwnd == hwnd)

        def ensure_focused(attempts=4) -> bool:
            for _ in range(attempts):
                if focused_on_notepad():
                    return True
            return False

        kb = Controller()

        # Session restore may open a previously used document — never type
        # into it. Force a fresh Untitled tab instead.
        if "Untitled" not in window_title(hwnd):
            if not ensure_focused():
                print("SKIP: cannot focus the new Notepad window.")
                return 1
            with kb.pressed(Key.ctrl):
                kb.press("n")
                kb.release("n")
            time.sleep(0.8)
        if "Untitled" not in window_title(hwnd):
            print(f"SKIP: no fresh Untitled document available "
                  f"(title: {window_title(hwnd)!r}) — aborting without typing.")
            return 1

        if not ensure_focused():
            print("SKIP/FAIL: cannot keep Notepad focused (desktop in active "
                  "use) — aborting without typing.")
            return 1

        for ch in TEST_TEXT:  # slow typing: fast synthetic typing drops keys
            kb.type(ch)
            time.sleep(0.012)
        time.sleep(0.3)
        title = window_title(hwnd)
        if TEST_TEXT[:25] not in title:
            print(f"FAIL: typing did not land in Notepad (title: {title!r})")
            return 1
        print(f"    typing verified via title: {title!r}")

        with kb.pressed(Key.ctrl):
            kb.press("a")
            kb.release("a")
        time.sleep(0.2)

        # ---- THE real action: hotkey with modifiers still held ----------
        print(f"[1] pressing the REAL hotkey ({COMBO}) — modifiers held +150 ms")
        kb.press(Key.ctrl)
        kb.press(Key.shift)
        time.sleep(0.03)
        kb.press("e")
        kb.release("e")
        time.sleep(0.15)  # <-- the critical window that used to break capture
        kb.release(Key.shift)
        kb.release(Key.ctrl)

        deadline = time.time() + 7
        while (time.time() < deadline
               and results["capture"] is None
               and results["error"] is None):
            time.sleep(0.05)

        print(f"    hotkey fired: {results['fired']}x")
        if results["error"] is not None:
            print(f"    FAIL: capture raised: {results['error']}")
            return 1
        if results["capture"] is None:
            print("    FAIL: the hotkey never fired")
            return 1

        cap = results["capture"]
        print(f"    captured: {cap.text!r}")
        print(f"    window:   {cap.window.process_name} / {cap.window.title!r}")

        ok_text = cap.text.strip() == TEST_TEXT
        ok_window = "notepad" in (cap.window.process_name or "").lower()
        print(f"    exact text: {ok_text}   correct window: {ok_window}")

        passed = ok_text and ok_window and results["fired"] >= 1
        print()
        print("RESULT:", "PASS" if passed else "FAIL")
        return 0 if passed else 1
    finally:
        manager.unregister()
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        # Also close any surviving window that carries our test marker
        # (multi-process Notepads can outlive the spawned process).
        terminate_marker_windows(MARKERS)


if __name__ == "__main__":
    sys.exit(main())
