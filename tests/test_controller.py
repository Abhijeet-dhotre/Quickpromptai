"""End-to-end controller tests (capture → enhance → popup → replace) with fakes."""
import time

import pytest

from core.errors import EmptySelectionError, NetworkError, ShortcutError
from core.models import (
    CaptureResult,
    EnhanceMode,
    EnhanceResult,
    ReplaceResult,
    UsageInfo,
    WindowInfo,
)
from storage.history import HistoryStore
from storage.settings import SettingsStore
from ui.controller import AppController


def pump(app, seconds: float = 1.0) -> None:
    """Process Qt events until ``seconds`` elapse (lets workers finish)."""
    import time as _time

    deadline = _time.monotonic() + seconds
    while _time.monotonic() < deadline:
        app.processEvents()
        _time.sleep(0.01)


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------
class FakeHotkeys:
    def __init__(self, fail=False):
        self.fail = fail
        self.registered = None
        self.combo = None

    def register(self, combo):
        from shortcuts.global_hotkey import parse_combo

        canonical, _ = parse_combo(combo)
        if self.fail:
            raise ShortcutError("reserved by another app")
        self.registered = canonical
        self.combo = canonical
        return canonical

    def unregister(self):
        self.registered = None
        self.combo = None

    @property
    def is_registered(self):
        return self.registered is not None


class FakeCapturer:
    def __init__(self, text="selected text", error=None):
        self.text = text
        self.error = error

    def capture(self, timeout=None):
        if self.error is not None:
            raise self.error
        return CaptureResult(
            text=self.text,
            window=WindowInfo(hwnd=1, pid=7, title="Editor", process_name="editor.exe"),
        )


class FakeReplacer:
    def __init__(self, result=None, error=None, delay=0.0):
        self.result = result or ReplaceResult(ok=True, message="Pasted.")
        self.error = error
        self.delay = delay
        self.calls = []

    def replace(self, capture, text):
        if self.delay:
            time.sleep(self.delay)
        self.calls.append((capture.text, text))
        if self.error is not None:
            raise self.error
        return self.result


class FakeAdapter:
    def __init__(self, result=None, error=None, delay=0.0):
        self.result = result or EnhanceResult(
            text="ENHANCED", usage=UsageInfo(10, 5, 15), provider_id="openai",
            model="gpt-4o-mini",
        )
        self.error = error
        self.delay = delay
        self.calls = []

    def enhance(self, system_prompt, user_prompt):
        if self.delay:
            time.sleep(self.delay)
        self.calls.append((system_prompt, user_prompt))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def controller(tmp_path, monkeypatch, qapp):
    c = AppController(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        history_store=HistoryStore(tmp_path / "history.json"),
        capturer=FakeCapturer(),
        replacer=FakeReplacer(),
        hotkey_manager=FakeHotkeys(),
    )
    yield c
    c.shutdown()


def patch_adapter(monkeypatch, adapter):
    monkeypatch.setattr(
        "ui.controller.create_adapter_from_settings",
        lambda settings, api_key, session=None: adapter,
    )


# ----------------------------------------------------------------------
# Hotkey & settings
# ----------------------------------------------------------------------
def test_hotkey_thread_emits_signal(controller, qapp):
    received = []
    controller.hotkey_fired.connect(lambda: received.append(1))
    controller._on_hotkey_thread()  # as called by the pynput thread
    pump(qapp, 0.2)
    assert received == [1]


def test_apply_shortcut_persists(controller, qapp):
    changed = []
    controller.shortcut_changed.connect(changed.append)
    canonical = controller.apply_shortcut("ctrl+alt+h")
    pump(qapp, 0.1)
    assert canonical == "Ctrl+Alt+H"
    assert controller.current_shortcut() == "Ctrl+Alt+H"
    assert changed == ["Ctrl+Alt+H"]
    reloaded = SettingsStore(controller.settings_store.path).load()
    assert reloaded.shortcut == "Ctrl+Alt+H"


def test_apply_shortcut_rollback_on_failure(tmp_path, monkeypatch, qapp):
    c = AppController(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        history_store=HistoryStore(tmp_path / "history.json"),
        capturer=FakeCapturer(), replacer=FakeReplacer(),
        hotkey_manager=FakeHotkeys(),
    )
    try:
        c.apply_shortcut("Ctrl+Alt+J")
        failing = FakeHotkeys(fail=True)
        c._hotkeys = failing
        with pytest.raises(ShortcutError):
            c.apply_shortcut("Ctrl+Alt+K")
        # Previous settings survive.
        assert SettingsStore(c.settings_store.path).load().shortcut == "Ctrl+Alt+J"
    finally:
        c.shutdown()


def test_start_hotkeys_failure_emits_signal(tmp_path, qapp):
    failed = []
    c = AppController(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        history_store=HistoryStore(tmp_path / "history.json"),
        capturer=FakeCapturer(), replacer=FakeReplacer(),
        hotkey_manager=FakeHotkeys(fail=True),
    )
    c.shortcut_failed.connect(failed.append)
    try:
        assert c.start_hotkeys() is False
        pump(qapp, 0.1)
        assert failed and isinstance(failed[0], ShortcutError)
    finally:
        c.shutdown()


# ----------------------------------------------------------------------
# Capture → enhance → popup flow
# ----------------------------------------------------------------------
def test_popup_flow_success(controller, qapp, monkeypatch):
    patch_adapter(monkeypatch, FakeAdapter())
    captures, results, errors, stages = [], [], [], []
    controller.popup_capture_ready.connect(captures.append)
    controller.popup_result.connect(lambda cap, res: results.append((cap, res)))
    controller.popup_error.connect(lambda stage, err: errors.append((stage, err)))
    controller.popup_stage.connect(stages.append)

    controller.trigger_popup_enhance()
    pump(qapp, 2.0)

    assert errors == []
    assert len(captures) == 1 and captures[0].text == "selected text"
    assert len(results) == 1 and results[0][1].text == "ENHANCED"
    assert any("Capturing" in s for s in stages)
    assert any("Enhancing" in s for s in stages)
    # History recorded by default.
    assert len(controller.history_store.list()) == 1


def test_popup_flow_capture_failure(controller, qapp, monkeypatch):
    controller._capturer = FakeCapturer(error=EmptySelectionError())
    patch_adapter(monkeypatch, FakeAdapter())
    errors = []
    controller.popup_error.connect(lambda stage, err: errors.append((stage, err)))
    controller.trigger_popup_enhance()
    pump(qapp, 1.0)
    assert len(errors) == 1
    assert errors[0][0] == "capture"
    assert isinstance(errors[0][1], EmptySelectionError)


def test_popup_flow_enhance_failure(controller, qapp, monkeypatch):
    patch_adapter(monkeypatch, FakeAdapter(error=NetworkError("no internet")))
    errors = []
    controller.popup_error.connect(lambda stage, err: errors.append((stage, err)))
    controller.trigger_popup_enhance()
    pump(qapp, 1.5)
    assert len(errors) == 1
    assert errors[0][0] == "enhance"
    assert isinstance(errors[0][1], NetworkError)


def test_cancel_discards_stale_result(controller, qapp, monkeypatch):
    patch_adapter(monkeypatch, FakeAdapter(delay=0.4))
    results = []
    controller.popup_result.connect(lambda cap, res: results.append(res))
    controller.trigger_popup_enhance()
    controller.cancel_popup_operation()  # user closed the popup immediately
    pump(qapp, 1.5)
    assert results == []  # the stale response must not surface


def test_retry_uses_edited_text(controller, qapp, monkeypatch):
    adapter = FakeAdapter()
    patch_adapter(monkeypatch, adapter)
    results = []
    controller.popup_result.connect(lambda cap, res: results.append(res))
    controller.retry_popup_enhance("edited original text", EnhanceMode.QUICK_FIX)
    pump(qapp, 1.0)
    assert len(results) == 1
    assert "edited original text" in adapter.calls[0][1]


def test_history_disabled_records_nothing(controller, qapp, monkeypatch):
    from core.config import AppSettings

    settings = AppSettings.from_dict(controller.settings.to_dict())
    settings.history_enabled = False
    controller.save_settings(settings)
    patch_adapter(monkeypatch, FakeAdapter())
    controller.trigger_popup_enhance()
    pump(qapp, 1.5)
    assert controller.history_store.list() == []


# ----------------------------------------------------------------------
# Replace workflow
# ----------------------------------------------------------------------
def test_replace_after_capture(controller, qapp, monkeypatch):
    patch_adapter(monkeypatch, FakeAdapter())
    controller.trigger_popup_enhance()
    pump(qapp, 2.0)
    done = []
    controller.popup_replace_done.connect(done.append)
    controller.replace_selected_text("PASTE THIS")
    pump(qapp, 1.0)
    assert len(done) == 1 and done[0].ok is True
    replacer = controller._replacer
    assert replacer.calls[-1] == ("selected text", "PASTE THIS")
    # Capture consumed: a second replace without a new capture fails.
    failed = []
    controller.popup_replace_failed.connect(failed.append)
    controller.replace_selected_text("again")
    pump(qapp, 0.5)
    assert len(failed) == 1


def test_replace_without_capture_fails(controller, qapp):
    failed = []
    controller.popup_replace_failed.connect(failed.append)
    controller.replace_selected_text("text")
    pump(qapp, 0.5)
    assert len(failed) == 1
    assert failed[0].code == "window_gone"


# ----------------------------------------------------------------------
# Credentials (real Credential Manager with cleanup)
# ----------------------------------------------------------------------
def test_api_key_lifecycle(controller):
    import uuid

    from security.credstore import delete_secret, save_secret

    provider = f"testprov-{uuid.uuid4().hex[:8]}"
    try:
        save_secret(provider, "temp-key")
        assert controller.has_api_key(provider) is True
        assert controller.delete_api_key(provider) is True
        assert controller.has_api_key(provider) is False
    finally:
        delete_secret(provider)


def test_enhance_text_rejects_empty_input(controller):
    with pytest.raises(EmptySelectionError):
        controller.enhance_text("   ", EnhanceMode.QUICK_FIX)
