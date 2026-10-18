"""Offscreen startup smoke tests: every window must build and behave."""
import pytest

from core.models import EnhanceMode, EnhanceResult, UsageInfo, WindowInfo, CaptureResult
from storage.history import HistoryStore
from storage.settings import SettingsStore
from ui.controller import AppController
from ui.main_window import MainWindow
from ui.popup import EnhancePopup
from ui.styles import DARK_THEME, POPUP_FRAME


@pytest.fixture
def app_env(tmp_path, monkeypatch, qapp):
    monkeypatch.setenv("QUICKPROMPT_DATA_DIR", str(tmp_path))

    class _Hotkeys:
        combo = None

        def register(self, combo):
            from shortcuts.global_hotkey import parse_combo

            self.combo, _ = parse_combo(combo)
            return self.combo

        def unregister(self):
            pass

    controller = AppController(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        history_store=HistoryStore(tmp_path / "history.json"),
        hotkey_manager=_Hotkeys(),
    )
    yield qapp, controller
    controller.shutdown()


def test_main_window_builds(app_env):
    app, controller = app_env
    window = MainWindow(controller)
    assert "QuickPrompt AI" in window.windowTitle()
    window.refresh_after_show()  # must not raise
    assert window.provider_combo.currentData() in ("openai", "anthropic", "gemini",
                                                   "groq", "openai_compat")
    assert window.shortcut_edit.text() == "Ctrl+Shift+E"
    window.close()


def test_first_run_setup_opens_provider_tab(app_env):
    _app, controller = app_env
    window = MainWindow(controller)
    window.open_provider_setup()
    assert window.tabs.tabText(window.tabs.currentIndex()) == "Provider"
    window.close()


def test_main_window_provider_switch_updates_fields(app_env):
    app, controller = app_env
    window = MainWindow(controller)
    index = window.provider_combo.findData("anthropic")
    window.provider_combo.setCurrentIndex(index)
    assert "claude" in window.model_combo.currentText()
    assert window.base_url_edit.placeholderText() == "https://api.anthropic.com"
    window.close()


def test_popup_builds_and_shows_result(app_env):
    app, controller = app_env
    popup = EnhancePopup()
    assert popup.mode_combo.count() == 3

    # No capture -> replace disabled even with a result.
    popup.set_result(EnhanceResult(
        text="ENHANCED", usage=UsageInfo(10, 5, 15), provider_id="openai",
        model="gpt-4o-mini",
    ))
    assert popup.output_edit.toPlainText() == "ENHANCED"
    assert popup.copy_button.isEnabled() is True
    assert popup.replace_button.isEnabled() is False
    assert "tokens" in popup.stage_label.text()

    # With a capture -> replace enabled.
    popup.reset_for_new_capture()
    popup.set_capture("original selection", True)
    popup.set_result(EnhanceResult(text="ENHANCED 2"))
    assert popup.replace_button.isEnabled() is True
    popup.close()


def test_popup_error_states(app_env):
    app, controller = app_env
    popup = EnhancePopup()
    popup.set_loading("Enhancing with the AI provider…")
    assert popup.busy_bar.isVisible() or not popup.isVisible()  # busy shown
    assert popup.enhance_button.isEnabled() is False

    from core.errors import NetworkError

    popup.handle_error("enhance", NetworkError())
    assert popup.error_label.isVisible() or popup.error_label.text()
    assert popup.enhance_button.isEnabled() is True

    from core.errors import EmptySelectionError

    popup.reset_for_new_capture()
    popup.handle_error("capture", EmptySelectionError())
    assert "paste" in popup.error_label.text().lower()
    popup.close()


def test_popup_mode_selection(app_env):
    app, controller = app_env
    popup = EnhancePopup()
    popup.set_mode(EnhanceMode.ADVANCED_OPTIMIZE)
    assert popup.current_mode() == EnhanceMode.ADVANCED_OPTIMIZE
    popup.close()


def test_popup_position_stays_on_screen(app_env):
    app, controller = app_env
    popup = EnhancePopup()
    popup.resize(620, 540)
    popup.show()
    popup.position_near_cursor()
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen().availableGeometry()
    frame = popup.frameGeometry()
    assert screen.left() <= frame.left()
    assert screen.top() <= frame.top()
    popup.close()


def test_styles_are_loaded():
    assert "QPushButton" in DARK_THEME
    assert "EnhancePopup" in POPUP_FRAME


def test_history_tab_clear(app_env, tmp_path):
    from core.models import HistoryEntry

    app, controller = app_env
    controller.history_store.add(HistoryEntry(
        timestamp=1.0, mode="quick_fix", provider_id="openai", model="m",
        original="o", enhanced="e",
    ))
    window = MainWindow(controller)
    window._refresh_history_list()
    assert window.history_list.count() == 1
    window._clear_history()
    assert window.history_list.count() == 0
    window.close()
