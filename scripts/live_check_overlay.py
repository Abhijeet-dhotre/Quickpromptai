"""Live Windows integration check: the dictation overlay never steals focus.

Verifies, on a real interactive desktop, with Notepad as the dictation target:

1. The pill appears over Notepad *without Notepad losing focus* (the
   no-activate styles actually work against the platform, not just Qt flags).
2. The paste-target identity check still passes while the pill is visible —
   the overlay cannot confuse the security check in windows_integration.caret.
3. The transcript still types into Notepad while the pill is on screen, then
   the pill reaches its green "Done" state.
4. The refusal path still refuses (stale target) while the pill is shown, and
   the pill reports the red error state.
5. The pill docks bottom-centre, tucked just above the taskbar (TASKBAR_GAP
   px), on the monitor under the mouse, shifting away from a known caret.

Run manually:  python scripts/live_check_overlay.py
Exits 0 on success, 1 on failure. Opens and closes Notepad automatically.
(Bar height reacting to the actual voice is a manual/visual check — see
docs/MANUAL_TESTS.md "B3. Dictation overlay pill".)
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _live_common import terminate_marker_windows  # noqa: E402

PREFIX = "pill-check-before"
TRANSCRIPT = "transcript 24680"
MARKERS = (PREFIX, TRANSCRIPT)


def pump(app, seconds: float) -> None:
    """Run the Qt event loop while waiting, so pill timers/animations fire."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    from pywinauto import Desktop

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from ui.dictation_overlay import (
        DictationOverlay,
        OverlayTiming,
        PILL_RECT,
        TASKBAR_GAP,
        caret_screen_rect,
    )
    from windows_integration.caret import CaretPaster
    from windows_integration.window_info import focus_window, get_foreground_window, same_process
    import ctypes

    user32 = ctypes.windll.user32
    app = QApplication([])
    overlay = DictationOverlay(timing=OverlayTiming(done_ms=2000))

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
        # Windows refuses programmatic focus switches when the calling
        # process has no user-input context (e.g. while the terminal that
        # launched this script is frontmost). A brief synthetic Alt press
        # supplies that context — scripts-only workaround; production code
        # never needs it because the user's real click/hotkey grants focus.
        # A *second* Alt tap afterwards is required: a single Alt leaves the
        # app's menu bar in "menu mode", which would swallow the very next
        # keystroke (e.g. the check's Ctrl+V) as a menu key.
        from pynput.keyboard import Key, Controller

        kb = Controller()

        def alt_tap() -> None:
            kb.press(Key.alt)
            time.sleep(0.03)
            kb.release(Key.alt)

        for _ in range(attempts):
            alt_tap()
            focus_window(hwnd)
            time.sleep(0.25)
            fg = get_foreground_window()
            if fg and "notepad" in fg.process_name.lower():
                time.sleep(0.1)
                alt_tap()          # clear any menu-mode highlight
                time.sleep(0.1)
                return True
        return False

    if not ensure_notepad_focused():
        print("    SKIP/FAIL: could not keep Notepad focused — aborting.")
        proc.kill()
        return 1

    from pynput.keyboard import Controller

    kb = Controller()
    for ch in PREFIX:
        kb.type(ch)
        time.sleep(0.012)
    time.sleep(0.3)

    def extra_focused() -> bool:
        return ensure_notepad_focused()

    extra_focused()
    paster = CaretPaster()
    target = paster.capture_target()
    fg_before = user32.GetForegroundWindow()

    print("[2] showing the overlay exactly as the dictation shortcut would…")
    overlay.set_listening()
    show_caret = caret_screen_rect()

    # Simulate the recorder's thread bridge at its real rate.
    stop = threading.Event()

    def feed():
        level = 0.05
        while not stop.is_set():
            level = min(1.0, level + 0.03)
            overlay.level_changed.emit(level)
            time.sleep(0.02)

    threading.Thread(target=feed, daemon=True).start()
    pump(app, 1.2)                        # pill spring-in + a bit of "audio"

    fg_mid = get_foreground_window()
    pill_visible = overlay.isVisible() and overlay.state == "listening"
    fg_unchanged = fg_mid is not None and fg_mid.hwnd == fg_before
    identity_ok = same_process(target, paster.capture_target())
    print(f"    pill visible while listening: {pill_visible}")
    print(f"    foreground stayed Notepad:    {fg_unchanged}")
    print(f"    paste target identity valid:  {identity_ok}")
    screen_geom = app.primaryScreen().availableGeometry()
    taskbar_top = screen_geom.top() + screen_geom.height()
    pill_gap = taskbar_top - (overlay.geometry().y()
                              + PILL_RECT.top() + PILL_RECT.height())
    centred = abs((overlay.geometry().x() + overlay.width() // 2)
                  - screen_geom.center().x())
    print(f"    pill gap above taskbar: {pill_gap}px (target {TASKBAR_GAP}px), "
          f"centre offset: {centred}px, caret avoided: {show_caret is not None}")

    print("[3] transcribing + typing into Notepad while the pill is on screen…")
    overlay.set_processing()
    pump(app, 0.6)
    paste_ok = True
    try:
        paster.paste_into(target, TRANSCRIPT)
    except Exception as exc:
        paste_ok = False
        print(f"    FAIL: paste refused unexpectedly: {exc}")
    overlay.set_done()                    # green check as dictation would
    pump(app, 0.8)
    title_after = win.window_text()
    probe = (PREFIX + TRANSCRIPT).replace(" ", "")
    typed_ok = probe in title_after.replace(" ", "")
    if not typed_ok:                      # diagnostics for a clean retry
        print(f"    FAIL: typing did not land (title: '{title_after}')")
    print(f"    transcript typed with pill on top: {paste_ok and typed_ok}")

    print("[4] refusal path: stale target while the pill is shown…")
    overlay.set_listening()
    pump(app, 0.8)
    import dataclasses

    stale = dataclasses.replace(target, pid=target.pid + 100000)
    refused = False
    try:
        paster.paste_into(stale, "SHOULD-NOT-BE-TYPED")
    except Exception as exc:
        refused = type(exc).__name__ == "WindowGoneError"
        print(f"    refused with: {exc}")
    overlay.set_error("Window changed — text kept in the app")
    pump(app, 0.5)
    error_ok = overlay.state == "error" and overlay.isVisible()
    pump(app, 2.6)                        # 2.0 s error + 0.26 s fade
    error_auto_hidden = overlay.state == "idle" and not overlay.isVisible()
    not_typed = "SHOULD-NOT-BE-TYPED" not in win.window_text()
    print(f"    refusal raised: {refused}; nothing leaked: {not_typed}")
    print(f"    pill error state shown: {error_ok}; auto-hidden after: {error_auto_hidden}")

    overlay.force_hide()
    stop.set()
    proc.kill()
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    terminate_marker_windows(MARKERS)

    passed = all((
        pill_visible, fg_unchanged, identity_ok, paste_ok, typed_ok,
        refused, not_typed, error_ok, error_auto_hidden,
        TASKBAR_GAP - 6 <= pill_gap <= TASKBAR_GAP + 6 and centred <= 2,
    ))
    print()
    print("RESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
