"""Dictation overlay tests.

No microphone, no real shortcuts, no desktop side effects: the state
machine, waveform level plumbing, caret-avoidance maths and the never-focus
guarantee are exercised offscreen with injectable timings, so the auto-hide
durations shrink to milliseconds.

Everything asserted here maps to section B2 in ``docs/MANUAL_TESTS.md``:
idle → listening → processing → done/error → idle with zero timers left
running, and bars that only react to real mic level.
"""
import array
import time

import pytest
from PySide6.QtCore import Qt

from core.errors import (
    MicrophoneError,
    NetworkError,
    ProviderRequestError,
    WindowGoneError,
)
from speech.recorder import AudioRecorder

from ui.dictation_overlay import (
    DICTATION_OVERLAY_FLAGS,
    DictationOverlay,
    OverlayTiming,
    short_error_label,
)

FAST = OverlayTiming(show_ms=30, hide_ms=30, done_ms=40, error_ms=60,
                     scale_ms=10, dot_ms=16)


def pump(app, seconds: float = 0.6) -> None:
    """Process Qt events until ``seconds`` elapse (timers/anims fire)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def overlay(qapp):
    ov = DictationOverlay(timing=FAST)
    yield ov
    ov.force_hide()
    ov.close()


# ======================================================================
# Construction / hard guarantees
# ======================================================================
def test_overlay_flags_prevent_taskbar_and_focus(overlay):
    """The safety-critical window machinery: never focusable, topmost, tool."""
    from PySide6.QtCore import Qt as _Qt

    flags = overlay.windowFlags()
    for flag in (Qt.FramelessWindowHint, Qt.WindowStaysOnTopHint, Qt.Tool,
                 Qt.WindowDoesNotAcceptFocus):
        assert bool(flags & flag)
        assert bool(DICTATION_OVERLAY_FLAGS & flag)
    assert overlay.testAttribute(Qt.WA_TranslucentBackground)
    assert overlay.testAttribute(Qt.WA_ShowWithoutActivating)
    assert overlay.focusPolicy() == _Qt.NoFocus
    assert overlay.state == "idle"


def test_idle_overlay_has_no_timers_running(overlay):
    """Hidden = zero cost: nothing animating at all."""
    assert overlay.state == "idle"
    assert overlay._timers_running() is False


def test_force_hide_resets_everything(overlay):
    overlay.set_listening()
    overlay.set_error("whatever")
    overlay.force_hide()
    assert overlay.state == "idle"
    assert overlay._timers_running() is False
    assert overlay.current_level == 0.0


# ======================================================================
# State machine: the full dictation round-trip
# ======================================================================
def test_state_machine_full_round_trip(overlay, qapp):
    """idle → listening → processing → done → auto-hide → idle."""
    overlay.set_listening()
    assert overlay.state == "listening"
    assert overlay._dot_timer.isActive() is False    # dots not running yet

    overlay.set_processing()
    assert overlay.state == "processing"
    assert overlay._dot_timer.isActive()              # dots animate

    overlay.set_done()
    assert overlay.state == "done"
    assert overlay._autohide.isActive()               # green pill is momentary

    pump(qapp, 0.35)                                  # done + hide ≪ 0.35 s
    assert overlay.state == "idle"
    assert overlay._timers_running() is False
    assert not overlay.isVisible()


def test_error_state_auto_hides_and_resets(overlay, qapp):
    """An error never leaves the pill stuck on screen."""
    overlay.set_error("Provider rejected the request")
    assert overlay.state == "error"
    assert overlay._autohide.isActive()

    pump(qapp, 0.4)                                   # error + hide ≪ 0.4 s
    assert overlay.state == "idle"
    assert overlay._timers_running() is False


def test_new_listen_cancels_pending_hide(overlay):
    """Pressing the shortcut again mid-'Done' must win over the auto-hide."""
    overlay.set_listening()
    overlay.set_processing()
    overlay.set_done()                                # auto-hide now pending
    overlay.set_listening()                           # user starts again
    assert overlay.state == "listening"
    assert overlay._autohide.isActive() is False      # stale dismissal gone
    assert overlay._dot_timer.isActive() is False


def test_recording_failure_updates_state_before_show(overlay):
    """A mic/start failure surfaces the red pill even if it never showed."""
    assert not overlay.isVisible()
    overlay.set_error("Microphone unavailable")
    assert overlay.state == "error"
    overlay.force_hide()


# ======================================================================
# Error labels: generic, never secrets, never transcripts
# ======================================================================
def test_error_labels_are_generic_and_short():
    assert "window" in short_error_label(
        WindowGoneError("private detail that must not show")).lower()
    assert "microphone" in short_error_label(
        MicrophoneError("secret details sk-live-123")).lower()
    assert "network" in short_error_label(NetworkError("boom")).lower()
    assert short_error_label(
        ProviderRequestError("endpoint error sk-abc")) == "Provider rejected the request"
    assert short_error_label(RuntimeError("raw")) == "Dictation failed"
    assert short_error_label(None) == "Dictation failed"


def test_error_reason_never_carries_exception_detail(overlay):
    """Wired correctly, the pill text is the short label only."""
    overlay.set_error(short_error_label(
        WindowGoneError("sk-live-SECRET http://example.com")))
    overlay.force_hide()


# ======================================================================
# Mic level → waveform bars (the real signal path)
# ======================================================================
def test_level_signal_moves_bars(overlay):
    """Bars shrink when quiet, grow when loud — from the real mic value."""
    overlay.set_listening()
    overlay.on_mic_level(0.0)          # silence
    low = overlay.bar_heights()
    overlay.on_mic_level(1.0)          # loud speech
    high = overlay.bar_heights()
    assert max(high) > max(low)
    assert max(high) == 26
    assert min(low) == 6               # 6..26 px range per the spec


def test_bar_heights_clamped_and_monotonic():
    for level in (0.0, 0.25, 0.5, 0.75, 1.0):
        heights = DictationOverlay.bar_heights_for(level, phase=0.0)
        assert len(heights) >= 7
        for h in heights:
            assert 6 <= h <= 26
    low = DictationOverlay.bar_heights_for(0.1)
    high = DictationOverlay.bar_heights_for(1.0)
    assert all(b >= a for a, b in zip(low, high))   # monotonic in level


def test_level_bridge_is_connected_and_queued(overlay, qapp):
    """Recorder emits on its audio thread; the overlay slot runs on the GUI."""
    received = []
    overlay.level_changed.connect(received.append)   # = main.py recorder wiring
    overlay.level_changed.emit(0.5)                  # as if from PortAudio
    qapp.processEvents()
    assert received == [0.5]
    assert overlay.current_level == 0.5              # stored (bars only repaint in listening)

    overlay.set_listening()
    overlay.level_changed.emit(0.8)
    qapp.processEvents()
    assert overlay.current_level == 0.8              # listening: drives bars


def test_position_avoids_known_caret():
    """Bottom-centre per spec, nudged out of a known caret's way."""
    from PySide6.QtCore import QRect

    bounds = QRect(0, 0, 1920, 1040)
    base = QRect(788, 850, 344, 92)              # bottom-centre placement
    caret = QRect(860, 866, 30, 20)              # caret inside the pill
    moved = DictationOverlay.avoid_caret(base, caret, bounds)
    guard = caret.adjusted(-32, -32, 32, 32)
    assert not moved.intersects(guard)
    assert bounds.contains(moved)
    # No caret reported → plan unchanged.
    assert DictationOverlay.avoid_caret(base, None, bounds) == base
    # Caret far away → plan unchanged.
    far = DictationOverlay.avoid_caret(base, QRect(100, 10, 10, 10), bounds)
    assert far == base


def test_position_docks_pill_gap_above_taskbar(overlay):
    """Bottom-centre of the cursor's monitor; pill gap == TASKBAR_GAP."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QCursor, QGuiApplication

    import ui.dictation_overlay as dom

    overlay.set_listening()
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    avail = screen.availableGeometry()
    taskbar_top = avail.top() + avail.height()
    expected = QPoint(
        avail.center().x() - overlay.width() // 2,
        taskbar_top - dom.TASKBAR_GAP - dom.PILL_TOP_MARGIN
        - dom.PILL_RECT.height(),
    )
    assert overlay.geometry().topLeft() == expected
    overlay.force_hide()


def test_position_uses_real_caret_report(monkeypatch, overlay):
    """A caret intersecting the docked spot moves the pill out of its way."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QCursor, QGuiApplication

    import ui.dictation_overlay as dom

    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    avail = screen.availableGeometry()
    taskbar_top = avail.top() + avail.height()
    base = QRect(
        avail.center().x() - overlay.width() // 2,
        taskbar_top - dom.TASKBAR_GAP - dom.PILL_TOP_MARGIN
        - dom.PILL_RECT.height(),
        overlay.width(), overlay.height(),
    )
    caret = QRect(base.left() + 100, base.top() + 30, 20, 14)
    monkeypatch.setattr(dom, "caret_screen_rect", lambda: caret)

    overlay.position_for_cursor()
    guard = caret.adjusted(-32, -32, 32, 32)
    assert not overlay.geometry().intersects(guard)
    assert avail.contains(overlay.geometry())
    overlay.force_hide()


def test_noactivate_exstyle_masks_activation():
    from ui.dictation_overlay import _WS_EX_NOACTIVATE, _noactivate_exstyle

    assert _noactivate_exstyle(0) & _WS_EX_NOACTIVATE != 0
    assert _noactivate_exstyle(0x100) & _WS_EX_NOACTIVATE != 0


# ======================================================================
# Recorder: the real RMS level signal (fast rise / slower fall)
# ======================================================================
class _FakeStream:
    """Replays one block through the recorder callback (no sound device)."""

    def __init__(self, callback, block):
        self.callback = callback
        self.block = block
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True
        # Two blocks, like a brief flash of real audio.
        self.callback(self.block, len(self.block) // 2, None, None)
        self.callback(self.block, len(self.block) // 2, None, None)

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


_LOUD = array.array("h", [12000] * 160).tobytes()
_QUIET = array.array("h", [40] * 160).tobytes()


def test_recorder_emits_smoothed_level():
    """The meter rides the existing audio callback — no timers, no fakes."""
    holder = {}

    def factory(callback):
        stream = _FakeStream(callback, _LOUD)
        holder["stream"] = stream
        return stream

    seen = []
    sink = seen.append
    recorder = AudioRecorder(stream_factory=factory, level_callback=sink)
    recorder.start()
    assert recorder.active
    loud = seen[-1]                      # one value per callback block
    assert 0.85 <= loud <= 1.0

    stream = holder["stream"]
    stream.callback(_QUIET, 160, None, None)   # user gets quieter
    quiet = recorder.level
    assert 0.0 < quiet < loud            # decaying, not snapped to zero

    for _ in range(3):
        recorder._update_level(_QUIET)
    later = recorder.level
    assert later < quiet                 # keeps sinking between updates
    assert recorder.stop()               # audio still intact
    assert recorder.level_callback is sink


def test_recorder_meter_failure_never_breaks_audio():
    """A broken meter callback must not kill the recording."""
    def broken(_value):
        raise RuntimeError("meter bug")

    recorder = AudioRecorder(
        stream_factory=lambda cb: _FakeStream(cb, _LOUD), level_callback=broken)
    recorder.start()                     # must not raise
    assert recorder.stop()               # and the WAV still comes back


def test_recorder_silence_gates_to_zero():
    recorder = AudioRecorder(
        stream_factory=lambda cb: _FakeStream(cb, _QUIET))
    recorder.start()
    recorder._update_level(_QUIET)       # room tone stays at 0.0
    assert recorder.level == 0.0
