"""Real Windows clipboard tests (backup/restore semantics).

These tests touch the actual clipboard and restore it afterwards.
They self-skip in sessions where Windows denies clipboard access
(e.g. non-interactive service sessions); the capture/replace logic is
covered independently by the fake-based tests.
"""
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows only",
)

from windows_integration import clipboard  # noqa: E402


def _clipboard_usable() -> bool:
    try:
        return clipboard.is_available()
    except Exception:
        return False


requires_clipboard = pytest.mark.skipif(
    not _clipboard_usable(),
    reason="clipboard access denied in this session (run from a normal "
           "interactive desktop session)",
)


@pytest.fixture
def restore_user_clipboard():
    """Restore whatever the user had before each test."""
    snap = clipboard.snapshot()
    yield snap
    if snap.had_text:
        clipboard.restore(snap)
    else:
        # Remove our test text; cannot restore non-text content we never saw.
        try:
            clipboard.set_text("")
        except clipboard.ClipboardError:  # pragma: no cover
            pass


@requires_clipboard
def test_set_get_roundtrip(restore_user_clipboard):
    clipboard.set_text("QuickPrompt-clipboard-test-✓")
    assert clipboard.get_text() == "QuickPrompt-clipboard-test-✓"
    assert clipboard.get_sequence() != restore_user_clipboard.sequence


@requires_clipboard
def test_snapshot_captures_text_and_sequence(restore_user_clipboard):
    clipboard.set_text("snapshot-source")
    snap = clipboard.snapshot()
    assert snap.had_text is True
    assert snap.text == "snapshot-source"
    assert snap.sequence == clipboard.get_sequence()


@requires_clipboard
def test_restore_puts_old_text_back(restore_user_clipboard):
    clipboard.set_text("original-text")
    snap = clipboard.snapshot()
    clipboard.set_text("copied-selection")
    assert clipboard.get_text() == "copied-selection"
    assert clipboard.restore(snap, expected_sequence=clipboard.get_sequence()) is True
    assert clipboard.get_text() == "original-text"


@requires_clipboard
def test_restore_skipped_when_clipboard_moved_on(restore_user_clipboard):
    clipboard.set_text("mine")
    snap = clipboard.snapshot()
    clipboard.set_text("after-snapshot")
    stale_sequence = clipboard.get_sequence() - 1
    # Someone else wrote to the clipboard in the meantime -> do not clobber.
    assert clipboard.restore(snap, expected_sequence=stale_sequence) is False
    assert clipboard.get_text() == "after-snapshot"


@requires_clipboard
def test_restore_of_textless_snapshot_returns_false(restore_user_clipboard):
    clipboard.set_text("")
    snap = clipboard.snapshot()
    # Simulate a clipboard holding only non-text content.
    snap = clipboard.ClipboardSnapshot(text=None, had_text=False, sequence=999)
    assert clipboard.restore(snap) is False


@requires_clipboard
def test_sequence_changes_on_every_write(restore_user_clipboard):
    clipboard.set_text("a")
    first = clipboard.get_sequence()
    clipboard.set_text("a")  # identical content, still a new write
    assert clipboard.get_sequence() != first
