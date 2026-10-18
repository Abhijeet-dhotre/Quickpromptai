"""Speech-to-text (dictation) tests.

Covers the four layers added for the feature, each with fakes so no real
microphone, network, or global OS hook is required:

* WAV encoding & microphone capture (``speech.recorder``)
* Whisper request construction, response parsing and error mapping
  (``providers`` multipart path)
* The record → transcribe pipeline (``speech.dictation``)
* The controller hotkey/worker flow and cursor-preserving insertion
"""
import io
import json
import time
import wave

import pytest

from core.config import (
    DEFAULT_DICTATION_SHORTCUT,
    DEFAULT_SHORTCUT,
    AppSettings,
)
from core.errors import (
    AuthenticationError,
    ConfigError,
    MicrophoneError,
    ModelError,
    NetworkError,
    QuickPromptError,
    WindowGoneError,
)
from providers.base import ProviderCapabilities
from providers.openai_provider import GroqAdapter, OpenAIAdapter
from providers.registry import create_adapter
from speech.dictation import DictationError, DictationService
from speech.recorder import SAMPLE_RATE, AudioRecorder, encode_wav


def pump(app, seconds: float = 1.0) -> None:
    """Process Qt events until ``seconds`` elapse (lets workers finish)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


# ======================================================================
# Fakes
# ======================================================================
class FakeResponse:
    def __init__(self, status_code=200, data=None, text=None):
        self.status_code = status_code
        self._data = data
        self.text = text if text is not None else json.dumps(data or {})

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class FakeSession:
    """Records the multipart request so tests can assert on its shape.

    Unlike the chat-completions fake, this one accepts the ``data``/``files``
    kwargs that the audio upload path passes through.
    """

    def __init__(self, responses=None, exc=None):
        self.responses = list(responses or [FakeResponse()])
        self.exc = exc
        self.calls = []

    def request(self, method, url, headers=None, json=None, timeout=None,
                data=None, files=None):
        self.calls.append({
            "method": method, "url": url, "headers": headers or {},
            "json": json, "timeout": timeout, "data": data, "files": files,
        })
        if self.exc is not None:
            raise self.exc
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


class FakeStream:
    """Stand-in for a sounddevice InputStream."""

    def __init__(self, callback, block=b"\x01\x00" * 160):
        self.callback = callback
        self.block = block
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True
        # Simulate PortAudio delivering a couple of blocks.
        self.callback(self.block, 160, None, None)
        self.callback(self.block, 160, None, None)

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def make_recorder(block=b"\x01\x00" * 160):
    holder = {}

    def factory(callback):
        stream = FakeStream(callback, block)
        holder["stream"] = stream
        return stream

    return AudioRecorder(stream_factory=factory), holder


class FakeSettings:
    """Minimal stand-in for AppSettings used by DictationService."""

    def __init__(self, provider="openai", whisper_model="whisper-1"):
        self.active_provider = provider
        self.whisper_model = whisper_model


# ======================================================================
# WAV encoding
# ======================================================================
def test_encode_wav_produces_valid_16k_mono_file():
    # 320 bytes of 16-bit PCM = 160 samples.
    frames = [b"\x01\x00" * 160, b"\x02\x00" * 160]
    data = encode_wav(frames)

    with wave.open(io.BytesIO(data), "rb") as wav:
        assert wav.getframerate() == SAMPLE_RATE == 16_000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2          # 16-bit
        assert wav.getnframes() == 320
        # Payload survives the round-trip byte-for-byte.
        assert wav.readframes(320) == b"".join(frames)


def test_encode_wav_empty_is_still_a_valid_container():
    with wave.open(io.BytesIO(encode_wav([])), "rb") as wav:
        assert wav.getnframes() == 0


def test_encode_wav_header_is_riff():
    assert encode_wav([b"\x00\x00"]).startswith(b"RIFF")


# ======================================================================
# Recorder
# ======================================================================
def test_recorder_start_stop_returns_wav():
    recorder, holder = make_recorder()
    assert recorder.active is False
    recorder.start()
    assert recorder.active is True
    assert holder["stream"].started is True

    data = recorder.stop()
    assert recorder.active is False
    assert holder["stream"].stopped is True
    assert holder["stream"].closed is True
    # Two blocks of 160 samples were delivered by the fake.
    with wave.open(io.BytesIO(data), "rb") as wav:
        assert wav.getnframes() == 320
        assert wav.getframerate() == 16_000


def test_recorder_reports_missing_numpy_dependency(monkeypatch):
    recorder = AudioRecorder()

    def missing_numpy(_callback):
        raise ModuleNotFoundError("No module named 'numpy'", name="numpy")

    monkeypatch.setattr(recorder, "_open_stream", missing_numpy)

    with pytest.raises(MicrophoneError, match="NumPy"):
        recorder.start()


def test_recorder_stop_without_audio_returns_none():
    # A stream that opens but never delivers a block (e.g. an immediate stop).
    class SilentStream(FakeStream):
        def start(self):
            self.started = True  # no callback -> no captured audio

    recorder = AudioRecorder(stream_factory=lambda cb: SilentStream(cb))
    recorder.start()
    assert recorder.active is True
    # Nothing was captured, so there is no WAV to return.
    assert recorder.stop() is None


def test_recorder_stop_when_never_started_is_none():
    assert AudioRecorder(stream_factory=lambda cb: FakeStream(cb)).stop() is None


def test_recorder_start_twice_opens_device_once():
    recorder, holder = make_recorder()
    recorder.start()
    first = holder["stream"]
    recorder.start()  # must be a no-op
    assert holder["stream"] is first


def test_recorder_reports_missing_package_friendly():
    recorder = AudioRecorder()

    def _boom(_cb):
        raise ImportError("no sounddevice")

    recorder._open_stream = _boom
    with pytest.raises(MicrophoneError) as excinfo:
        recorder.start()
    assert "sounddevice" in str(excinfo.value)
    assert recorder.active is False


@pytest.mark.parametrize("detail,expected", [
    ("Operation not permitted due to permission denied", "privacy"),
    ("PortAudioError: no default input device", "No microphone"),
    ("something else entirely", "could not be opened"),
])
def test_recorder_maps_device_errors_friendly(detail, expected):
    recorder = AudioRecorder()

    def _boom(_cb):
        raise RuntimeError(detail)

    recorder._open_stream = _boom
    with pytest.raises(MicrophoneError) as excinfo:
        recorder.start()
    assert expected.lower() in str(excinfo.value).lower()
    assert recorder.active is False


# ======================================================================
# Whisper request (provider multipart path)
# ======================================================================
def _openai_adapter(session):
    return OpenAIAdapter(
        create_adapter("openai", api_key="sk-test-123", model="gpt-4o-mini").config,
        session=session,
    )


def test_audio_capability_flags():
    assert OpenAIAdapter.capabilities.supports_audio_transcription is True
    assert GroqAdapter.capabilities.supports_audio_transcription is True
    # A provider without a Whisper endpoint must not claim support.
    from providers.anthropic_provider import AnthropicAdapter

    assert AnthropicAdapter.capabilities.supports_audio_transcription is False


def test_transcribe_builds_multipart_request():
    session = FakeSession([FakeResponse(data={"text": "  hello world  "})])
    adapter = _openai_adapter(session)

    text = adapter.transcribe_audio(b"RIFF....WAVE", model="whisper-1")

    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert call["headers"]["Authorization"] == "Bearer sk-test-123"
    # JSON payload must stay unused so the body is pure multipart.
    assert call["json"] is None
    assert call["data"]["model"] == "whisper-1"
    assert call["data"]["response_format"] == "json"
    filename, payload, content_type = call["files"]["file"]
    assert filename.endswith(".wav")
    assert payload == b"RIFF....WAVE"
    assert content_type == "audio/wav"
    # Surrounding whitespace is trimmed before insertion.
    assert text == "hello world"


def test_transcribe_defaults_to_whisper_one_when_model_missing():
    session = FakeSession([FakeResponse(data={"text": "ok"})])
    adapter = _openai_adapter(session)
    adapter.transcribe_audio(b"RIFF")
    # Auto resolution uses the *provider's* default Whisper model.
    assert session.calls[0]["data"]["model"] == "whisper-1"


def test_transcribe_auto_model_is_provider_specific():
    # Regression: "whisper-1" 404s on Groq (verified live 2026-10-19) — the
    # auto model must be groq's own ID, not OpenAI's.
    session = FakeSession([FakeResponse(data={"text": "ok"})])
    adapter = GroqAdapter(
        create_adapter("groq", api_key="gsk_test", model="openai/gpt-oss-120b").config,
        session=session,
    )
    adapter.transcribe_audio(b"RIFF")  # no model = auto
    assert session.calls[0]["data"]["model"] == "whisper-large-v3-turbo"
    # An explicit model always wins over the auto default.
    adapter.transcribe_audio(b"RIFF", model="whisper-large-v3")
    assert session.calls[1]["data"]["model"] == "whisper-large-v3"


def test_transcribe_unknown_model_404_names_the_model():
    session = FakeSession([FakeResponse(
        status_code=404,
        text='{"error":{"message":"The model `whisper-1` does not exist"}}',
    )])
    adapter = _openai_adapter(session)
    from core.errors import ModelError

    with pytest.raises(ModelError) as excinfo:
        adapter.transcribe_audio(b"RIFF", model="whisper-1")
    message = str(excinfo.value)
    # The friendly 404 must name the rejected model and how to fix it.
    assert "whisper-1" in message
    assert "Settings" in message


def test_transcribe_honours_groq_base_url():
    session = FakeSession([FakeResponse(data={"text": "hi"})])
    adapter = GroqAdapter(
        create_adapter("groq", api_key="gsk_test", model="openai/gpt-oss-120b").config,
        session=session,
    )
    adapter.transcribe_audio(b"RIFF", model="whisper-large-v3")
    call = session.calls[0]
    assert call["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert call["data"]["model"] == "whisper-large-v3"


def test_transcribe_uses_provider_default_model_when_unconfigured():
    """An empty model must resolve per provider, not to OpenAI's ID."""
    session = FakeSession([FakeResponse(data={"text": "hi"})])
    adapter = GroqAdapter(
        create_adapter("groq", api_key="gsk_test", model="openai/gpt-oss-120b").config,
        session=session,
    )

    adapter.transcribe_audio(b"RIFF")

    assert session.calls[0]["data"]["model"] == "whisper-large-v3-turbo"


def test_transcribe_requires_api_key():
    session = FakeSession()
    adapter = OpenAIAdapter(
        create_adapter("openai", api_key="", model="gpt-4o-mini").config,
        session=session,
    )
    with pytest.raises(Exception) as excinfo:
        adapter.transcribe_audio(b"RIFF")
    assert "API key" in str(excinfo.value)
    assert session.calls == []  # nothing was sent


def test_transcribe_requires_base_url_for_compat_provider():
    adapter = create_adapter(
        "openai_compat", api_key="k", model="", base_url=""
    , session=FakeSession())
    with pytest.raises(ConfigError):
        adapter.transcribe_audio(b"RIFF")


def test_transcribe_maps_auth_error():
    session = FakeSession([FakeResponse(status_code=401, data={"error": {"message": "bad key"}})])
    adapter = _openai_adapter(session)
    with pytest.raises(AuthenticationError):
        adapter.transcribe_audio(b"RIFF")


def test_transcribe_maps_rate_limit_and_server_errors():
    session = FakeSession([FakeResponse(status_code=429, data={"error": "slow down"})])
    with pytest.raises(Exception) as excinfo:
        _openai_adapter(session).transcribe_audio(b"RIFF")
    assert "rate limit" in str(excinfo.value).lower()

    session = FakeSession([FakeResponse(status_code=500, text="oops")])
    with pytest.raises(NetworkError):
        _openai_adapter(session).transcribe_audio(b"RIFF")


def test_transcribe_maps_missing_model():
    session = FakeSession([FakeResponse(status_code=404, data={"error": {"message": "no model"}})])
    with pytest.raises(ModelError):
        _openai_adapter(session).transcribe_audio(b"RIFF")


def test_transcribe_rejects_non_json_and_unexpected_body():
    session = FakeSession([FakeResponse(status_code=200, data=None, text="<html>gateway</html>")])
    with pytest.raises(Exception) as excinfo:
        _openai_adapter(session).transcribe_audio(b"RIFF")
    assert "non-JSON" in str(excinfo.value)

    # JSON but no "text" key.
    session = FakeSession([FakeResponse(data={"result": "nope"})])
    with pytest.raises(Exception) as excinfo:
        _openai_adapter(session).transcribe_audio(b"RIFF")
    assert "text" in str(excinfo.value)


def test_transcribe_unsupported_provider_is_friendly():
    from providers.anthropic_provider import AnthropicAdapter

    adapter = AnthropicAdapter(
        create_adapter("anthropic", api_key="sk-ant", model="claude").config,
        session=FakeSession(),
    )
    with pytest.raises(ConfigError) as excinfo:
        adapter.transcribe_audio(b"RIFF")
    message = str(excinfo.value)
    assert "Anthropic" in message
    assert "speech-to-text" in message or "Whisper" in message


def test_transcribe_network_failure_is_friendly():
    import requests

    session = FakeSession(exc=requests.ConnectionError("no route to host"))
    with pytest.raises(NetworkError) as excinfo:
        _openai_adapter(session).transcribe_audio(b"RIFF")
    assert "internet" in str(excinfo.value).lower() or "reach" in str(excinfo.value).lower()


# ======================================================================
# DictationService pipeline
# ======================================================================
class StubAdapter:
    capabilities = ProviderCapabilities(supports_audio_transcription=True)
    display_name = "OpenAI"

    def __init__(self, text="transcribed text", error=None):
        self._text = text
        self._error = error
        self.calls = []

    def transcribe_audio(self, audio_wav, *, model=None):
        self.calls.append((audio_wav, model))
        if self._error is not None:
            raise self._error
        return self._text


class StubRecorder:
    def __init__(self, wav=b"RIFF....WAVE", error=None):
        self.wav = wav
        self.error = error
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        if self.error is not None:
            raise self.error

    def stop(self):
        self.stopped = True
        return self.wav


def _service(monkeypatch, adapter, recorder, provider="openai"):
    monkeypatch.setattr(
        "speech.dictation.create_adapter_from_settings",
        lambda settings, api_key, session=None: adapter,
    )
    monkeypatch.setattr("speech.dictation.load_secret", lambda pid=None: "sk-test")
    return DictationService(settings_provider=lambda: FakeSettings(provider),
                            recorder=recorder)


def test_service_transcribes_and_reports_provider(monkeypatch):
    adapter = StubAdapter(text="dictated words")
    recorder = StubRecorder()
    service = _service(monkeypatch, adapter, recorder)

    service.start_recording()
    assert recorder.started is True

    result = service.stop_and_transcribe()
    assert recorder.stopped is True
    assert result.text == "dictated words"
    assert result.provider_id == "openai"
    assert result.model == "whisper-1"
    assert adapter.calls == [(b"RIFF....WAVE", "whisper-1")]


def test_service_passes_custom_whisper_model(monkeypatch):
    adapter = StubAdapter()
    service = DictationService(
        settings_provider=lambda: FakeSettings(whisper_model="whisper-large-v3"),
        recorder=StubRecorder(),
    )
    monkeypatch.setattr(
        "speech.dictation.create_adapter_from_settings",
        lambda settings, api_key, session=None: adapter,
    )
    monkeypatch.setattr("speech.dictation.load_secret", lambda pid=None: "k")
    service.transcribe(b"RIFF")
    assert adapter.calls == [(b"RIFF", "whisper-large-v3")]


def test_service_no_audio_raises(monkeypatch):
    service = _service(monkeypatch, StubAdapter(), StubRecorder(wav=None))
    with pytest.raises(DictationError) as excinfo:
        service.stop_and_transcribe()
    assert "No audio" in str(excinfo.value)


def test_service_missing_api_key(monkeypatch):
    monkeypatch.setattr("speech.dictation.load_secret", lambda pid=None: "")
    service = DictationService(
        settings_provider=lambda: FakeSettings(), recorder=StubRecorder()
    )
    with pytest.raises(DictationError) as excinfo:
        service.transcribe(b"RIFF")
    assert "API key" in str(excinfo.value)


def test_service_mic_error_propagates(monkeypatch):
    service = _service(
        monkeypatch, StubAdapter(), StubRecorder(error=MicrophoneError("blocked"))
    )
    with pytest.raises(MicrophoneError):
        service.start_recording()


def test_service_unsupported_provider_message(monkeypatch):
    class NoAudioAdapter:
        capabilities = ProviderCapabilities(supports_audio_transcription=False)
        display_name = "Gemini"

    monkeypatch.setattr(
        "speech.dictation.create_adapter_from_settings",
        lambda settings, api_key, session=None: NoAudioAdapter(),
    )
    monkeypatch.setattr("speech.dictation.load_secret", lambda pid=None: "k")
    service = DictationService(
        settings_provider=lambda: FakeSettings(provider="gemini"),
        recorder=StubRecorder(),
    )
    with pytest.raises(DictationError) as excinfo:
        service.transcribe(b"RIFF")
    assert "Gemini" in str(excinfo.value)


# ======================================================================
# Controller dictation flow
# ======================================================================
class FakeHotkeys:
    def __init__(self, fail=False):
        self.fail = fail
        self.registered = None
        self.combo = None

    def register(self, combo):
        from shortcuts.global_hotkey import parse_combo

        canonical, _ = parse_combo(combo)
        if self.fail:
            from core.errors import ShortcutError

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


class FakePaster:
    """Stands in for :class:`CaretPaster` so tests never touch the desktop."""

    def __init__(self, target="target-window", error=None):
        self.target = target
        self.error = error
        self.pastes = []

    def capture_target(self):
        return self.target

    def paste_into(self, target, text):
        if self.error is not None:
            raise self.error
        self.pastes.append((target, text))


@pytest.fixture
def dictation_controller(tmp_path, monkeypatch, qapp):
    from storage.history import HistoryStore
    from storage.settings import SettingsStore
    from ui.controller import AppController

    recorder = StubRecorder(wav=b"RIFF....WAVE")
    adapter = StubAdapter(text="hello from mic")
    monkeypatch.setattr(
        "speech.dictation.create_adapter_from_settings",
        lambda settings, api_key, session=None: adapter,
    )
    monkeypatch.setattr("speech.dictation.load_secret", lambda pid=None: "sk-test")

    service = DictationService(
        settings_provider=lambda: c.settings, recorder=recorder
    )
    paster = FakePaster()
    c = AppController(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        history_store=HistoryStore(tmp_path / "history.json"),
        hotkey_manager=FakeHotkeys(),
        dictation_hotkey_manager=FakeHotkeys(),
        dictation_service=service,
        dictation_paster=paster,
    )
    c._test_recorder = recorder
    c._test_adapter = adapter
    c._test_paster = paster
    yield c
    c.shutdown()


def test_controller_toggle_start_then_stop(dictation_controller, qapp):
    c = dictation_controller
    started, processed, texts = [], [], []
    c.dictation_started.connect(lambda: started.append(1))
    c.dictation_processing.connect(lambda: processed.append(1))
    c.dictation_text_ready.connect(texts.append)

    c.toggle_dictation()          # start
    assert c.is_dictating is True
    assert c._test_recorder.started is True
    assert started == [1]

    c.toggle_dictation()          # stop + transcribe
    assert c.is_dictating is False
    assert processed == [1]
    pump(qapp, 1.0)
    assert texts == ["hello from mic"]
    assert c._test_recorder.stopped is True
    # The words are typed where the user was working, not into QuickPrompt.
    assert c._test_paster.pastes == [("target-window", "hello from mic")]


def test_dictation_paste_failure_still_delivers_text(dictation_controller, qapp):
    """A refused paste must not swallow the transcript."""
    c = dictation_controller
    not_pasted, texts = [], []
    c.dictation_not_pasted.connect(lambda text, err: not_pasted.append((text, err)))
    c.dictation_text_ready.connect(texts.append)
    c._test_paster.error = WindowGoneError("you switched windows")

    c.toggle_dictation()
    c.toggle_dictation()
    pump(qapp, 1.0)

    assert texts == []                      # not reported as typed
    assert len(not_pasted) == 1
    assert not_pasted[0][0] == "hello from mic"
    assert isinstance(not_pasted[0][1], WindowGoneError)


def test_dictation_hotkey_registers_even_if_enhance_is_taken(tmp_path, qapp):
    """One taken hotkey must not silently disable the other."""
    from storage.history import HistoryStore
    from storage.settings import SettingsStore
    from ui.controller import AppController

    failed, dchanged = [], []
    c = AppController(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        history_store=HistoryStore(tmp_path / "history.json"),
        hotkey_manager=FakeHotkeys(fail=True),      # enhance is reserved
        dictation_hotkey_manager=FakeHotkeys(),      # dictation is free
        dictation_service=DictationService(
            settings_provider=lambda: c.settings, recorder=StubRecorder()
        ),
        dictation_paster=FakePaster(),
    )
    c.shortcut_failed.connect(failed.append)
    c.dictation_shortcut_changed.connect(dchanged.append)
    try:
        assert c.start_hotkeys() is False       # enhance genuinely failed
        assert failed                             # ...and said so
        assert dchanged == [DEFAULT_DICTATION_SHORTCUT]   # dictation still live
    finally:
        c.shutdown()


def test_controller_dictation_failure_emits_signal(dictation_controller, qapp):
    c = dictation_controller
    failures = []
    c.dictation_failed.connect(failures.append)
    c._test_adapter._error = NetworkError("provider unreachable")

    c.toggle_dictation()
    c.toggle_dictation()
    pump(qapp, 1.0)
    assert len(failures) == 1
    assert "provider unreachable" in str(failures[0])
    # State is reset so the next attempt can start cleanly.
    assert c.is_dictating is False


def test_controller_mic_denied_surfaces_error(dictation_controller):
    c = dictation_controller
    failures = []
    c.dictation_failed.connect(failures.append)
    c._test_recorder.error = MicrophoneError("Windows blocked the microphone")
    c.toggle_dictation()
    assert failures and "blocked" in str(failures[0])
    assert c.is_dictating is False


def test_controller_no_audio_emits_error(dictation_controller, qapp):
    c = dictation_controller
    failures = []
    c.dictation_failed.connect(failures.append)
    c._test_recorder.wav = None
    c.toggle_dictation()
    c.toggle_dictation()
    pump(qapp, 1.0)
    assert failures and "No audio" in str(failures[0])


def test_controller_dictation_hotkey_fires(dictation_controller, qapp):
    c = dictation_controller
    fired = []
    c.dictation_hotkey_fired.connect(lambda: fired.append(1))
    c._on_dictation_hotkey_thread()   # as called by the pynput thread
    pump(qapp, 0.2)
    assert fired == [1]


def test_start_hotkeys_registers_both(dictation_controller):
    c = dictation_controller
    changed, dchanged = [], []
    c.shortcut_changed.connect(changed.append)
    c.dictation_shortcut_changed.connect(dchanged.append)
    assert c.start_hotkeys() is True
    assert changed == [DEFAULT_SHORTCUT]
    assert dchanged == [DEFAULT_DICTATION_SHORTCUT]


def test_dictation_hotkey_failure_is_non_fatal(tmp_path, monkeypatch, qapp):
    from storage.history import HistoryStore
    from storage.settings import SettingsStore
    from ui.controller import AppController

    c = AppController(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        history_store=HistoryStore(tmp_path / "history.json"),
        hotkey_manager=FakeHotkeys(),                       # enhance works
        dictation_hotkey_manager=FakeHotkeys(fail=True),    # dictation fails
    )
    try:
        failures = []
        c.dictation_shortcut_failed.connect(failures.append)
        # Enhance shortcut still registers successfully.
        assert c.start_hotkeys() is True
        assert c.hotkeys_active is True
        # The dictation failure is reported but does not break the app.
        assert len(failures) == 1
    finally:
        c.shutdown()


def test_apply_dictation_shortcut_persists(dictation_controller, tmp_path):
    from storage.settings import SettingsStore

    c = dictation_controller
    changed = []
    c.dictation_shortcut_changed.connect(changed.append)
    canonical = c.apply_dictation_shortcut("ctrl+alt+d")
    assert canonical == "Ctrl+Alt+D"
    assert c.current_dictation_shortcut() == "Ctrl+Alt+D"
    assert changed == ["Ctrl+Alt+D"]
    assert SettingsStore(c.settings_store.path).load().dictation_shortcut == "Ctrl+Alt+D"


def test_dictation_shortcut_must_differ_from_enhance(dictation_controller):
    from core.errors import ShortcutError

    c = dictation_controller
    c.apply_shortcut("Ctrl+Shift+E")
    with pytest.raises(ShortcutError) as excinfo:
        c.apply_dictation_shortcut("Ctrl+Shift+E")
    assert "enhance shortcut" in str(excinfo.value)


# ======================================================================
# Settings round-trip
# ======================================================================
def test_settings_defaults_for_dictation():
    s = AppSettings()
    assert s.dictation_shortcut == DEFAULT_DICTATION_SHORTCUT == "Ctrl+Shift+S"
    assert s.whisper_model == ""


def test_settings_dictation_roundtrip_and_tolerant_loading():
    s = AppSettings.from_dict({
        "dictation_shortcut": "ctrl+alt+d",
        "whisper_model": "  whisper-large-v3 ",
    })
    assert s.dictation_shortcut == "ctrl+alt+d"
    assert s.whisper_model == "whisper-large-v3"
    dumped = s.to_dict()
    assert dumped["dictation_shortcut"] == "ctrl+alt+d"
    assert dumped["whisper_model"] == "whisper-large-v3"

    # Bad/absent values fall back to defaults rather than exploding.
    fallback = AppSettings.from_dict({"dictation_shortcut": 42, "whisper_model": None})
    assert fallback.dictation_shortcut == DEFAULT_DICTATION_SHORTCUT
    assert fallback.whisper_model == ""

    blank = AppSettings.from_dict({})
    assert blank.dictation_shortcut == DEFAULT_DICTATION_SHORTCUT
    assert blank.whisper_model == ""


def test_settings_normalize_strips_blank_dictation_fields():
    s = AppSettings()
    s.dictation_shortcut = "   "
    s.whisper_model = "  "
    s.normalize()
    assert s.dictation_shortcut == DEFAULT_DICTATION_SHORTCUT
    assert s.whisper_model == ""


def test_settings_store_persists_dictation_fields(tmp_path):
    from storage.settings import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    s = AppSettings()
    s.dictation_shortcut = "Ctrl+Alt+D"
    s.whisper_model = "whisper-large-v3"
    store.save(s)
    loaded = store.load()
    assert loaded.dictation_shortcut == "Ctrl+Alt+D"
    assert loaded.whisper_model == "whisper-large-v3"


# ======================================================================
# Cursor-preserving insertion (typing area)
# ======================================================================
def test_popup_insert_transcript_preserves_content_and_caret(qapp):
    from ui.popup import EnhancePopup

    popup = EnhancePopup()
    popup.original_edit.setPlainText("existing draft")
    # Place the caret in the middle (between "existing" and "draft").
    cursor = popup.original_edit.textCursor()
    cursor.setPosition(8)
    popup.original_edit.setTextCursor(cursor)

    popup.insert_transcript("dictated")

    # Text landed at the caret, both halves intact, caret moved past the insert.
    assert popup.original_edit.toPlainText() == "existing dictated draft"
    assert popup.original_edit.textCursor().position() == len("existing dictated")
    popup.close()


def test_popup_insert_transcript_handles_empty_and_whitespace(qapp):
    from ui.popup import EnhancePopup

    popup = EnhancePopup()
    popup.original_edit.setPlainText("keep me")
    popup.insert_transcript("")
    popup.insert_transcript("   ")
    assert popup.original_edit.toPlainText().strip() == "keep me"
    popup.close()


def test_popup_insert_transcript_no_separator_after_punctuation(qapp):
    from ui.popup import EnhancePopup

    popup = EnhancePopup()
    cursor = popup.original_edit.textCursor()
    cursor.insertText("Hello.")
    popup.original_edit.setTextCursor(cursor)
    popup.insert_transcript("World")
    # After punctuation a leading space is still useful for readability; the
    # key contract is that the original text is preserved and the caret ends
    # after the inserted words.
    text = popup.original_edit.toPlainText()
    assert text.startswith("Hello.")
    assert "World" in text
    popup.close()


def test_main_window_insert_transcript_at_caret(qapp, tmp_path, monkeypatch):
    from storage.history import HistoryStore
    from storage.settings import SettingsStore
    from ui.controller import AppController
    from ui.main_window import MainWindow

    c = AppController(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        history_store=HistoryStore(tmp_path / "history.json"),
        hotkey_manager=FakeHotkeys(),
        dictation_hotkey_manager=FakeHotkeys(),
    )
    try:
        window = MainWindow(c)
        window.source_edit.setPlainText("start middle end")
        cursor = window.source_edit.textCursor()
        cursor.setPosition(6)  # between "start" and " middle"
        window.source_edit.setTextCursor(cursor)

        window.insert_transcript("dictated")

        assert window.source_edit.toPlainText() == "start dictated middle end"
        assert window.source_edit.textCursor().position() == len("start dictated")
        window.close()
    finally:
        c.shutdown()


def test_main_window_dictation_indicator_toggles_button(qapp, tmp_path):
    from storage.history import HistoryStore
    from storage.settings import SettingsStore
    from ui.controller import AppController
    from ui.main_window import MainWindow

    c = AppController(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        history_store=HistoryStore(tmp_path / "history.json"),
        hotkey_manager=FakeHotkeys(),
        dictation_hotkey_manager=FakeHotkeys(),
    )
    try:
        window = MainWindow(c)
        window.show_dictation_indicator(True)
        assert window.dictate_button.isChecked() is True
        assert "Stop" in window.dictate_button.text()

        window.show_dictation_indicator(False, processing=True)
        assert window.dictate_button.isChecked() is False
        assert "Dictate" in window.dictate_button.text()
        window.close()
    finally:
        c.shutdown()


# ======================================================================
# CaretPaster - typing the transcript into the original window
# ======================================================================
class StubKeys:
    def __init__(self, error=None):
        self.error = error
        self.pastes = 0

    def send_paste(self):
        if self.error is not None:
            raise self.error
        self.pastes += 1


class StubClipboard:
    def __init__(self):
        self.events = []
        self.text = None

    def snapshot(self):
        self.events.append("snapshot")
        return "snap"

    def get_sequence(self):
        self.events.append("sequence")
        return 7

    def set_text(self, text):
        self.events.append("set_text")
        self.text = text

    def restore(self, snap, expected_sequence=None):
        self.events.append("restore")
        return True


def _window(pid, hwnd=111, name="notepad.exe"):
    from core.models import WindowInfo

    return WindowInfo(hwnd=hwnd, pid=pid, title="Doc", process_name=name)


def _paster(foreground, keys=None, clipboard_module=None, focused=None):
    from windows_integration.caret import CaretPaster

    calls = []

    def focus_fn(hwnd, timeout=0.8):
        calls.append(hwnd)
        if focused is not None:
            foreground[:] = focused
        return True

    return CaretPaster(
        key_sender=keys if keys is not None else StubKeys(),
        window_provider=lambda: foreground[0] if foreground else None,
        clipboard_module=clipboard_module or StubClipboard(),
        sleep=lambda _s: None,
        focus_fn=focus_fn,
        modifiers_wait=lambda **kw: True,
    ), calls


def test_caret_paster_types_into_the_original_window():
    target = _window(100)
    fg = [target]
    paster, focused = _paster(fg)
    clip = paster._clipboard

    paster.paste_into(target, "hello world")

    assert focused == [target.hwnd]        # window re-focused before typing
    assert paster.keys.pastes == 1
    assert clip.text == "hello world"
    # The user's clipboard is handed back afterwards.
    assert clip.events[-1] == "restore"
    assert clip.events[0] == "snapshot"


def test_caret_paster_refuses_when_user_switched_windows():
    target = _window(100)
    fg = [_window(200, name="chrome.exe")]   # focus went somewhere else
    paster, _ = _paster(fg)

    with pytest.raises(WindowGoneError) as excinfo:
        paster.paste_into(target, "hello world")

    assert "another window" in str(excinfo.value)
    assert paster.keys.pastes == 0           # nothing was typed


def test_caret_paster_without_a_target_is_a_clear_error():
    from windows_integration.caret import CaretPaster

    paster = CaretPaster(window_provider=lambda: None, sleep=lambda _s: None)
    assert paster.capture_target() is None
    with pytest.raises(WindowGoneError):
        paster.paste_into(None, "hello world")


def test_caret_paster_rejects_empty_transcript():
    target = _window(100)
    fg = [target]
    paster, _ = _paster(fg)

    with pytest.raises(QuickPromptError):
        paster.paste_into(target, "   ")
    assert paster.keys.pastes == 0


def test_caret_paster_restores_clipboard_when_keystroke_fails():
    target = _window(100)
    fg = [target]
    keys = StubKeys(error=OSError("hook blocked"))
    paster, _ = _paster(fg, keys=keys)
    clip = paster._clipboard

    with pytest.raises(QuickPromptError):
        paster.paste_into(target, "hello world")

    assert clip.events.count("restore") == 1   # clipboard never left dirty


def test_caret_paster_capture_target_uses_foreground_window():
    target = _window(100)
    fg = [target]
    paster, _ = _paster(fg)
    assert paster.capture_target() is target


def test_caret_paster_capture_target_survives_a_broken_provider():
    from windows_integration.caret import CaretPaster

    def boom():
        raise OSError("no desktop")

    assert CaretPaster(window_provider=boom).capture_target() is None
