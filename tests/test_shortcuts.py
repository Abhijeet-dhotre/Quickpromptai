"""Shortcut parsing, validation, registration and cleanup tests."""
import sys
import uuid

import pytest

from core.errors import ShortcutError
from shortcuts.global_hotkey import GlobalHotkeyManager, parse_combo


@pytest.mark.parametrize("combo,canonical,pynput", [
    ("Ctrl+Shift+E", "Ctrl+Shift+E", "<ctrl>+<shift>+e"),
    ("ctrl+shift+e", "Ctrl+Shift+E", "<ctrl>+<shift>+e"),
    ("Alt+F4", "Alt+F4", "<alt>+<f4>"),
    ("Ctrl+Alt+T", "Ctrl+Alt+T", "<ctrl>+<alt>+t"),
    ("Win+Shift+S", "Win+Shift+S", "<cmd>+<shift>+s"),
    ("Ctrl+Space", "Ctrl+Space", "<ctrl>+<space>"),
    ("Shift+Ctrl+Down", "Ctrl+Shift+Down", "<ctrl>+<shift>+<down>"),
    ("Ctrl+2", "Ctrl+2", "<ctrl>+2"),
    ("Ctrl+PageUp", "Ctrl+PgUp", "<ctrl>+<page up>"),
    ("Control+Enter", "Ctrl+Enter", "<ctrl>+<enter>"),
])
def test_parse_valid_combos(combo, canonical, pynput):
    assert parse_combo(combo) == (canonical, pynput)


@pytest.mark.parametrize("combo", [
    "", "   ", "E", "Ctrl+", "Ctrl+Shift",
    "Ctrl+Shift+NotAKey", "Ctrl+Alt+Del+Extra", "F5", "Ctrl+Alt+PageCurl",
])
def test_parse_invalid_combos(combo):
    with pytest.raises(ShortcutError):
        parse_combo(combo)


def test_parse_accepts_shift_only_modifier():
    # Technically valid (though rarely useful) — parser must not choke on it.
    assert parse_combo("Shift+E") == ("Shift+E", "<shift>+e")


def test_duplicate_modifiers_deduplicated():
    canonical, pynput = parse_combo("Ctrl+Ctrl+Shift+E")
    assert canonical == "Ctrl+Shift+E"
    assert pynput == "<ctrl>+<shift>+e"


@pytest.mark.skipif(sys.platform != "win32", reason="uses real pynput hooks")
def test_register_and_unregister_real_hotkey():
    fired = []
    manager = GlobalHotkeyManager(lambda: fired.append(1))
    combo = f"Ctrl+Alt+Shift+F{9}"  # unlikely to be taken
    try:
        canonical = manager.register(combo)
        assert canonical == "Ctrl+Alt+Shift+F9"
        assert manager.is_registered is True
        assert manager.combo == "Ctrl+Alt+Shift+F9"
    finally:
        manager.unregister()
    assert manager.is_registered is False
    assert manager.combo is None


@pytest.mark.skipif(sys.platform != "win32", reason="uses real pynput hooks")
def test_reregister_replaces_previous():
    manager = GlobalHotkeyManager(lambda: None)
    try:
        manager.register("Ctrl+Alt+Shift+F8")
        manager.register("Ctrl+Alt+Shift+F7")
        assert manager.combo == "Ctrl+Alt+Shift+F7"
    finally:
        manager.unregister()


@pytest.mark.skipif(sys.platform != "win32", reason="uses real pynput hooks")
def test_callback_exception_does_not_crash_listener():
    def boom():
        raise RuntimeError("listener must survive")

    manager = GlobalHotkeyManager(boom)
    try:
        manager.register("Ctrl+Alt+Shift+F6")
        manager._on_hotkey()  # simulate the listener invoking the callback
    finally:
        manager.unregister()
    assert manager.is_registered is False


def test_invalid_combo_registration_raises_cleanly():
    manager = GlobalHotkeyManager(lambda: None)
    with pytest.raises(ShortcutError):
        manager.register("NotACombo")
    assert manager.is_registered is False
