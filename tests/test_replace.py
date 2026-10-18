"""Safe text-replacement tests with scripted fakes."""
import pytest

from core.errors import ReplaceError, WindowGoneError
from core.models import CaptureResult, WindowInfo
from windows_integration import clipboard as real_clipboard
from windows_integration.replace import TextReplacer


class FakeClipboard:
    def __init__(self, text="previous clipboard"):
        self.text = text
        self.sequence = 5
        self.had_text = True

    def get_sequence(self):
        return self.sequence

    def get_text(self):
        return self.text

    def snapshot(self):
        return real_clipboard.ClipboardSnapshot(
            text=self.text, had_text=self.text is not None, sequence=self.sequence
        )

    def set_text(self, text):
        self.text = text
        self.sequence += 1

    def restore(self, snap, expected_sequence=None):
        if not snap.had_text:
            return False
        if expected_sequence is not None and self.sequence != expected_sequence:
            return False
        self.text = snap.text
        self.sequence += 1
        return True


class FakeKeys:
    def __init__(self):
        self.pastes = 0

    def send_paste(self):
        self.pastes += 1


def make(current_pid=42, current_process="editor.exe", focus_works=True):
    clip = FakeClipboard()
    keys = FakeKeys()
    current = WindowInfo(hwnd=9, pid=current_pid, title="Editor",
                         process_name=current_process)
    focused = []

    def fake_focus(hwnd):
        focused.append(hwnd)
        return focus_works

    replacer = TextReplacer(
        key_sender=keys,
        window_provider=lambda: current,
        clipboard_module=clip,
        sleep=lambda _s: None,
        focus_fn=fake_focus,
    )
    capture = CaptureResult(
        text="original selection",
        window=WindowInfo(hwnd=1, pid=42, title="Editor", process_name="editor.exe"),
    )
    return replacer, clip, keys, capture, focused


def test_replace_happy_path():
    replacer, clip, keys, capture, focused = make()
    result = replacer.replace(capture, "  enhanced prompt  ")
    assert result.ok is True
    assert result.verified is False  # best-effort by design
    assert keys.pastes == 1
    # Clipboard must be restored to its pre-paste content.
    assert clip.text == "previous clipboard"
    assert "verify" in result.message.lower()


def test_replace_focuses_original_window_first():
    """The captured window must be refocused before the foreground check —
    the user just clicked a button in our own popup, which holds focus."""
    replacer, clip, keys, capture, focused = make()
    replacer.replace(capture, "enhanced")
    assert focused == [capture.window.hwnd]  # focus restored BEFORE pasting


def test_replace_refuses_when_window_changed():
    replacer, clip, keys, capture, focused = make(current_pid=999)
    with pytest.raises(WindowGoneError):
        replacer.replace(capture, "enhanced")
    assert keys.pastes == 0        # nothing pasted into the wrong app
    assert clip.text == "previous clipboard"  # clipboard untouched


def test_replace_refuses_when_focus_cannot_be_restored():
    """If the original window is gone, focusing fails and nothing is pasted."""
    clip = FakeClipboard()
    keys = FakeKeys()
    replacer = TextReplacer(
        key_sender=keys,
        window_provider=lambda: None,  # no foreground window at all
        clipboard_module=clip,
        sleep=lambda _s: None,
        focus_fn=lambda hwnd: False,
    )
    capture = CaptureResult(text="orig", window=WindowInfo(hwnd=5, pid=42))
    with pytest.raises(WindowGoneError):
        replacer.replace(capture, "enhanced")
    assert keys.pastes == 0
    assert clip.text == "previous clipboard"


def test_replace_refuses_when_no_foreground_window():
    clip = FakeClipboard()
    keys = FakeKeys()
    replacer = TextReplacer(
        key_sender=keys, window_provider=lambda: None,
        clipboard_module=clip, sleep=lambda _s: None,
        focus_fn=lambda hwnd: True,
    )
    capture = CaptureResult(text="orig", window=WindowInfo(pid=42))
    with pytest.raises(WindowGoneError):
        replacer.replace(capture, "enhanced")
    assert keys.pastes == 0


def test_replace_rejects_empty_result():
    replacer, clip, keys, capture, focused = make()
    with pytest.raises(ReplaceError):
        replacer.replace(capture, "   ")
    assert keys.pastes == 0
    assert clip.text == "previous clipboard"
    assert focused == []  # no focus juggling for a no-op


def test_replace_falls_back_on_process_name_when_pid_unknown():
    """Same process name is accepted when PID could not be resolved."""
    clip = FakeClipboard()
    keys = FakeKeys()
    current = WindowInfo(hwnd=0, pid=0, title="Mystery", process_name="Editor.exe")
    replacer = TextReplacer(
        key_sender=keys, window_provider=lambda: current,
        clipboard_module=clip, sleep=lambda _s: None,
        focus_fn=lambda hwnd: True,
    )
    capture = CaptureResult(
        text="orig",
        window=WindowInfo(hwnd=0, pid=0, title="mystery", process_name="editor.exe"),
    )
    result = replacer.replace(capture, "enhanced")
    assert result.ok is True
