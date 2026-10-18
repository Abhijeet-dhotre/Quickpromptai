"""Selection-capture tests with scripted fakes (no real keystrokes)."""
import pytest

from core.errors import EmptySelectionError
from core.models import WindowInfo
from windows_integration import clipboard as real_clipboard
from windows_integration.capture import SelectionCapturer


class FakeClipboard:
    """Mimics the clipboard module's semantics."""

    def __init__(self, text="user's old clipboard", had_text=True):
        self.text = text if had_text else None
        self.sequence = 10
        self.had_text = had_text
        self.restored_texts = []

    def get_sequence(self):
        return self.sequence

    def get_text(self):
        return self.text

    def snapshot(self):
        return real_clipboard.ClipboardSnapshot(
            text=self.text, had_text=self.text is not None, sequence=self.sequence
        )

    def restore(self, snap, expected_sequence=None):
        if not snap.had_text:
            return False
        if expected_sequence is not None and self.sequence != expected_sequence:
            return False
        self.text = snap.text
        self.sequence += 1
        self.restored_texts.append(snap.text)
        return True

    # -- helpers to simulate the target application --------------------
    def app_copies(self, text):
        self.text = text
        self.sequence += 1


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(seconds, 0.001)


class FakeKeys:
    """Simulates the copy keystroke landing in the clipboard (or not)."""

    def __init__(self, clipboard, selection="  selected by user  ",
                 primary_works=True, alternate_works=True):
        self.clipboard = clipboard
        self.selection = selection
        self.primary_works = primary_works
        self.alternate_works = alternate_works
        self.copy_count = 0
        self.alt_count = 0

    def send_copy(self):
        self.copy_count += 1
        if self.selection is not None and self.primary_works:
            self.clipboard.app_copies(self.selection)

    def send_copy_alternate(self):
        self.alt_count += 1
        if self.selection is not None and self.alternate_works:
            self.clipboard.app_copies(self.selection)


def make(capture_selection="selected text", had_text=True,
         wm_copy_selection="__unsupported__", elevated=False, **key_kwargs):
    """Build a capturer wired to fakes.

    ``wm_copy_selection``:
      * ``"__unsupported__"`` (default) — the focused control ignores WM_COPY
      * ``""`` — WM_COPY delivered but nothing was copied
      * any string — WM_COPY puts that text on the (fake) clipboard
    """
    clip = FakeClipboard(had_text=had_text)
    clock = FakeClock()
    keys = FakeKeys(clip, selection=capture_selection, **key_kwargs)
    counters = {"modifiers_waited": 0, "wm_copy_calls": 0}

    def modifiers_wait(**_kwargs):
        counters["modifiers_waited"] += 1
        return True

    def wm_copy_sender():
        counters["wm_copy_calls"] += 1
        if wm_copy_selection == "__unsupported__":
            return False
        if wm_copy_selection:
            clip.app_copies(wm_copy_selection)
        return True

    capturer = SelectionCapturer(
        key_sender=keys,
        window_provider=lambda: WindowInfo(hwnd=1, pid=42, title="Editor",
                                           process_name="editor.exe"),
        clipboard_module=clip,
        sleep=clock.sleep,
        clock=clock.monotonic,
        poll_interval=0.05,
        default_timeout=1.0,
        modifiers_wait=modifiers_wait,
        wm_copy_sender=wm_copy_sender,
        elevated_check=lambda _pid: elevated,
    )
    capturer.counters = counters
    return capturer, clip, keys, clock


def test_capture_success_restores_clipboard():
    capturer, clip, keys, _ = make("selected by user")
    result = capturer.capture()
    assert result.text == "selected by user"
    assert result.window.pid == 42
    # Old clipboard content must be restored after the read.
    assert clip.restored_texts == ["user's old clipboard"]
    assert clip.text == "user's old clipboard"


def test_capture_empty_selection_raises():
    capturer, clip, keys, _ = make(capture_selection=None)  # Ctrl+C does nothing
    with pytest.raises(EmptySelectionError):
        capturer.capture()
    assert keys.copy_count == 1
    # Clipboard must be untouched (sequence never changed).
    assert clip.text == "user's old clipboard"
    assert clip.restored_texts == []


def test_capture_from_textless_clipboard_still_captures():
    capturer, clip, keys, _ = make("fresh selection", had_text=False)
    result = capturer.capture()
    assert result.text == "fresh selection"
    # Nothing restorable (original clipboard had no text) — no restore attempt.
    assert clip.restored_texts == []


def test_capture_timeout_is_finite():
    capturer, clip, keys, clock = make(capture_selection=None)
    capturer.default_timeout = 0.3
    with pytest.raises(EmptySelectionError):
        capturer.capture()
    assert clock.t >= 0.3  # it really polled until the deadline


def test_capture_identical_text_still_detected_via_sequence():
    """Copying text identical to the clipboard must still be detected."""
    capturer, clip, keys, _ = make("same-as-before")
    clip.text = "same-as-before"  # clipboard already holds identical text
    result = capturer.capture()
    assert result.text == "same-as-before"


def test_capture_falls_back_to_ctrl_insert():
    """When Ctrl+C does not copy, Ctrl+Insert is tried automatically."""
    capturer, clip, keys, _ = make("alt-copied", primary_works=False,
                                   alternate_works=True)
    result = capturer.capture()
    assert result.text == "alt-copied"
    assert keys.copy_count == 1
    assert keys.alt_count == 1


def test_capture_never_sends_fallback_when_primary_works():
    capturer, clip, keys, _ = make("primary-copied")
    capturer.capture()
    assert keys.copy_count == 1
    assert keys.alt_count == 0  # fallback not needed


def test_capture_both_copy_combos_fail():
    capturer, clip, keys, _ = make("anything", primary_works=False,
                                   alternate_works=False)
    with pytest.raises(EmptySelectionError):
        capturer.capture()
    assert keys.copy_count == 1 and keys.alt_count == 1
    assert clip.restored_texts == []  # clipboard never changed


# ----------------------------------------------------------------------
# Layer 0: wait for the hotkey's physical modifiers to be released
# ----------------------------------------------------------------------
def test_capture_waits_for_modifiers_before_copying():
    """The Ctrl+Shift of the hotkey must be released before Ctrl+C is sent,
    otherwise the target app receives Ctrl+Shift+C (DevTools in browsers)."""
    capturer, clip, keys, _ = make("held-modifiers-text")
    capturer.capture()
    assert capturer.counters["modifiers_waited"] == 1


# ----------------------------------------------------------------------
# Layer 1: WM_COPY (zero keystrokes)
# ----------------------------------------------------------------------
def test_capture_prefers_wm_copy_and_sends_no_keystrokes():
    capturer, clip, keys, _ = make("ignored-by-keys",
                                   wm_copy_selection="from wm copy")
    result = capturer.capture()
    assert result.text == "from wm copy"
    assert keys.copy_count == 0 and keys.alt_count == 0  # no keystrokes at all
    assert clip.restored_texts == ["user's old clipboard"]  # clipboard restored


def test_capture_falls_through_when_wm_copy_has_no_selection():
    capturer, clip, keys, _ = make("from ctrl+c", wm_copy_selection="")
    result = capturer.capture()
    assert result.text == "from ctrl+c"
    assert capturer.counters["wm_copy_calls"] == 1  # layer 1 was attempted
    assert keys.copy_count == 1                      # then Ctrl+C


def test_capture_uses_wm_copy_before_keystrokes_even_when_both_work():
    capturer, clip, keys, _ = make("keystroke-text", wm_copy_selection="msg-text")
    assert capturer.capture().text == "msg-text"
    assert keys.copy_count == 0


def test_capture_default_control_ignores_wm_copy():
    """When the control doesn't handle WM_COPY, Ctrl+C is used normally."""
    capturer, clip, keys, _ = make("plain-key-copied")
    assert capturer.capture().text == "plain-key-copied"
    assert capturer.counters["wm_copy_calls"] == 1  # attempted
    assert keys.copy_count == 1                     # fallback used


# ----------------------------------------------------------------------
# Precise failure reporting (admin apps)
# ----------------------------------------------------------------------
def test_capture_reports_admin_app_precisely():
    capturer, clip, keys, _ = make(None, elevated=True)
    with pytest.raises(EmptySelectionError) as exc:
        capturer.capture()
    message = str(exc.value)
    assert "administrator" in message.lower()
    assert "enhance" in message.lower()  # actionable alternative given


def test_capture_generic_message_for_normal_apps():
    capturer, clip, keys, _ = make(None, elevated=False)
    with pytest.raises(EmptySelectionError) as exc:
        capturer.capture()
    assert "administrator" not in str(exc.value).lower()
