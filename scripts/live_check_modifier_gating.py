"""Live gating check: capture must wait for the hotkey's modifiers to lift.

Reproduces the exact real-world failure mode without touching the user's
focus, selection, or real clipboard:

1. Inject Ctrl+Shift DOWN (exactly what a human still holds after pressing
   Ctrl+Shift+E) and verify GetAsyncKeyState sees them.
2. Start the real capture flow (real ``wait_modifiers_released``, real time).
3. Release the modifiers only after 150 ms — a simulated slow human.
4. Assert the copy keystroke was sent ONLY AFTER the release, and that the
   capture succeeded with the exact text.

Run manually:  python scripts/live_check_modifier_gating.py
Exits 0 on success, 1 on failure. Only bare modifier keys are ever injected
(no text, no shortcuts), so nothing visible happens in any application.
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_TEXT = "gated capture text 4242"
HOLD_SECONDS = 0.15


def main() -> int:
    from pynput.keyboard import Controller, Key

    from core.models import WindowInfo
    from windows_integration import clipboard as clipboard_mod
    from windows_integration.capture import SelectionCapturer
    from windows_integration.keys import modifiers_held

    class FakeClipboard:
        """Isolates the check from the user's real clipboard."""

        def __init__(self):
            self.text = "user clipboard"
            self.sequence = 10

        def get_sequence(self):
            return self.sequence

        def get_text(self):
            return self.text

        def snapshot(self):
            return clipboard_mod.ClipboardSnapshot(
                text=self.text, had_text=True, sequence=self.sequence
            )

        def restore(self, snap, expected_sequence=None):
            if expected_sequence is not None and self.sequence != expected_sequence:
                return False
            self.text = snap.text
            self.sequence += 1
            return True

        def app_copies(self, text):
            self.text = text
            self.sequence += 1

    clip = FakeClipboard()
    events = {"send_time": None, "clean_when_sent": None}

    class GatingKeys:
        def send_copy(self):
            events["send_time"] = time.monotonic()
            events["clean_when_sent"] = not modifiers_held()  # THE assertion
            clip.app_copies(TEST_TEXT)  # the "target app" copies — only now

        def send_copy_alternate(self):
            raise AssertionError("Ctrl+Insert must not be needed here")

    capturer = SelectionCapturer(
        key_sender=GatingKeys(),
        window_provider=lambda: WindowInfo(hwnd=1, pid=42, title="Fake",
                                           process_name="fake.exe"),
        clipboard_module=clip,
        wm_copy_sender=lambda: False,       # keep it deterministic
        elevated_check=lambda _pid: False,
        default_timeout=2.0,
        # modifiers_wait / sleep / clock remain the REAL implementations
    )

    kb = Controller()
    print("[1] injecting Ctrl+Shift DOWN (a human still holding the hotkey)")
    kb.press(Key.ctrl)
    kb.press(Key.shift)
    time.sleep(0.03)
    try:
        if not modifiers_held():
            print("FAIL: GetAsyncKeyState does not see the injected modifiers "
                  "(cannot validate in this session)")
            return 1
        print("    modifiers visible to GetAsyncKeyState: yes")

        def release_after_hold():
            time.sleep(HOLD_SECONDS)
            kb.release(Key.shift)
            kb.release(Key.ctrl)

        threading.Thread(target=release_after_hold, daemon=True).start()

        print("[2] starting the real capture flow while modifiers are held")
        t0 = time.monotonic()
        error: Exception | None = None
        capture = None
        try:
            capture = capturer.capture(timeout=2.0)
        except Exception as exc:  # noqa: BLE001
            error = exc
        elapsed = time.monotonic() - t0
    finally:
        try:  # never leave keys stuck down, whatever happened
            kb.release(Key.shift)
            kb.release(Key.ctrl)
        except Exception:
            pass

    if error is not None:
        print(f"FAIL: capture raised: {error}")
        return 1

    print(f"    captured {capture.text!r} after {elapsed:.2f}s")
    print(f"    copy keystroke sent only after release: "
          f"{events['clean_when_sent']}")

    ok = (
        capture.text.strip() == TEST_TEXT
        and events["clean_when_sent"] is True
        and elapsed >= 0.10  # it really waited instead of firing immediately
    )
    print()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
