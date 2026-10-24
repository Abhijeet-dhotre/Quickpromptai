"""Wispr Flow-style dictation overlay.

A small floating pill that appears at the bottom of the monitor under the
mouse while dictation runs: a live waveform while you speak, gently pulsing
dots while the provider transcribes, a green check when the transcript was
typed, and a red badge when something failed. Every state is a single,
centre-aligned icon — no text is ever drawn on the pill (the human-readable
explanation of any failure stays in the app window). It is a pure *display*
for the dictation flow — it never starts or stops the microphone, never
reads the clipboard, never logs or shows transcripts or API keys.

Visual language (deliberately restrained, Apple-system style)
------------------------------------------------------------
* Dark frosted glass body (near-#1C1C1E) with a hairline border and a soft,
  downward-weighted shadow — no neon gradients, no outer glow strokes.
* Monochrome white waveform bars, Apple system colours only for the moment
  they represent (blue transcribing, green done, red error).
* Icon-only: the bars, dots, check mark and error badge are centred in the
  pill; there is no timer, no captions, no labels.
* Everything eases in and out with ``OutCubic``; no spring overshoot, no
  cartoon bounce. Pills shrink to 0.92 when compact, not 0.86.

Contract with the rest of the app
--------------------------------
* Created and wired in ``main.build_app`` against the controller's *existing*
  dictation signals. No controller dictation code was changed.
* MUST never be able to become the focused / foreground window. It is
  frameless, ``NoFocus``, ``WindowDoesNotAcceptFocus``, shown with
  ``WA_ShowWithoutActivating`` and additionally marked ``WS_EX_NOACTIVATE``
  via the Windows API. This is what keeps the safety check in
  :mod:`windows_integration.caret` intact: the pill can never displace the
  paste target from the foreground, so at paste time
  ``GetForegroundWindow`` is still the user's app and the "you switched
  windows" refusal does not start firing on its own.
* All animation is painter-based (transform/opacity), driven by
  QVariantAnimation and the recorder's real loudness signal. When the pill is
  hidden, every timer and animation is stopped — zero CPU while idle.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QWidget

from core.errors import (
    AuthenticationError,
    MicrophoneError,
    ModelError,
    NetworkError,
    ProviderRequestError,
    ProviderTimeoutError,
    QuotaError,
    RateLimitError,
    WindowGoneError,
)

# ----------------------------------------------------------------------
# Painted geometry (logical pixels)
# ----------------------------------------------------------------------
#: The pill is icon-only now, so it is a compact chip rather than a
#: full-width bar. 120x40 keeps ~19 px of quiet glass around the
#: 82px-wide waveform, per user feedback that 140x46 felt too big.
WIDGET_SIZE = QSize(160, 80)
#: The pill lives inside a slightly larger transparent widget so the soft
#: drop shadow is never clipped by the window edge.
PILL_RECT = QRect(20, 18, 120, 40)
PILL_RADIUS = 20.0
#: Distance from the pill's visible bottom edge to the taskbar. Small on
#: purpose — the pill sits tucked just above the taskbar, Wispr-style.
TASKBAR_GAP = 20
#: Vertical pad *inside* the widget between its edges and the pill. The top
#: margin is what placement subtracts; the bottom one only has to leave
#: room for the shadow's lowest layer (13px inflate + 7px drop = 20).
PILL_TOP_MARGIN = PILL_RECT.top()                            # 18
PILL_BOTTOM_MARGIN = WIDGET_SIZE.height() - PILL_RECT.bottom()   # 22

_BAR_COUNT = 8
_BAR_WIDTH = 5.0
_BAR_GAP = 6.0
_BAR_MIN = 6.0    # bar height at silence (px)
_BAR_MAX = 26.0   # bar height at full level (px)
_PHASE_STEP = 0.30   # waveform texture advance per level update

#: Window flags: stays above everything, no taskbar entry, never focusable.
DICTATION_OVERLAY_FLAGS = (
    Qt.FramelessWindowHint
    | Qt.WindowStaysOnTopHint
    | Qt.Tool
    | Qt.WindowDoesNotAcceptFocus
)

_WS_EX_NOACTIVATE = 0x0800_0000
_WS_EX_TOPMOST = 0x0000_0008
_WS_EX_TOOLWINDOW = 0x0000_0080

#: Apple-system palette: restrained accents on a monochrome waveform.
_WHITE = QColor(255, 255, 255)
_ACCENT = QColor(10, 132, 255)   # #0A84FF — Apple system blue
_GREEN = QColor(48, 209, 88)     # #30D158 — Apple system green
_RED = QColor(255, 69, 58)       # #FF453A — Apple system red


@dataclass
class OverlayTiming:
    """Animation durations (ms). Injectable so tests can shrink them."""

    show_ms: int = 300          # fade/scale-in (Apple-eased, no overshoot)
    hide_ms: int = 240          # fade-out + scale-down before going idle
    done_ms: int = 1200         # how long the green "Done" pill stays
    error_ms: int = 2000        # how long the red error pill stays
    scale_ms: int = 200         # pill resize between states
    dot_ms: int = 16            # processing-dots animation interval (~60fps)


def _noactivate_exstyle(current: int) -> int:
    """Extra window style that makes a window unable to take activation."""
    return current | _WS_EX_NOACTIVATE | _WS_EX_TOPMOST | _WS_EX_TOOLWINDOW


def caret_screen_rect() -> QRect | None:
    """Best-effort screen rect of the caret in the foreground window.

    Windows-only via ``GetGUIThreadInfo`` + ``ClientToScreen``; returns
    ``None`` on any failure so callers fall back to the docked position.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _GuiThreadInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        user32 = ctypes.windll.user32
        info = _GuiThreadInfo()
        info.cbSize = ctypes.sizeof(_GuiThreadInfo)
        if not user32.GetGUIThreadInfo(0, ctypes.byref(info)):
            return None
        if not info.hwndCaret:
            return None
        pt = wintypes.POINT(info.rcCaret.left, info.rcCaret.top)
        if not user32.ClientToScreen(info.hwndCaret, ctypes.byref(pt)):
            return None
        width = max(2, info.rcCaret.right - info.rcCaret.left)
        height = max(10, info.rcCaret.bottom - info.rcCaret.top)
        return QRect(pt.x, pt.y, width, height)
    except Exception:  # noqa: BLE001 - cosmetic placement only
        return None


def short_error_label(exc: BaseException | None) -> str:
    """A short, generic pill label for a dictation failure.

    Deliberately *derived from the exception type only* — raw exception text
    must never reach the pill, so no URLs, no provider payloads, no secrets.
    The same errors are still fully explained in the app window by
    ``core.errors``.
    """
    if isinstance(exc, WindowGoneError):
        return "Window changed — text kept in the app"
    if isinstance(exc, MicrophoneError):
        return "Microphone unavailable"
    if isinstance(exc, (AuthenticationError, QuotaError, RateLimitError,
                        ProviderRequestError)):
        return "Provider rejected the request"
    if isinstance(exc, (NetworkError, ProviderTimeoutError)):
        return "Network problem — try again"
    if isinstance(exc, ModelError):
        return "Transcription model unavailable"
    return "Dictation failed"


class DictationOverlay(QWidget):
    """Floating dictation pill. Pure display; hooks into existing signals.

    The widget *never* shows itself on its own: only the ``set_*`` state
    methods (wired to the controller's dictation signals) open or hide it.
    """

    #: Bridge from the microphone thread. ``speech.recorder.AudioRecorder``
    #: calls ``overlay.level_changed.emit(level)`` from the PortAudio callback
    #: (~50 times/s); Qt queues this signal to the GUI thread, where
    #: :meth:`on_mic_level` repaints the waveform. This connection is made in
    #: the constructor, so a recorder only ever needs the ``emit`` callable.
    level_changed = Signal(float)

    def __init__(self, timing: OverlayTiming | None = None, parent=None):
        super().__init__(parent)
        self._timing = timing or OverlayTiming()
        self.setWindowFlags(DICTATION_OVERLAY_FLAGS)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(WIDGET_SIZE)
        # The app-wide stylesheet paints every QWidget opaque; a local
        # transparent rule keeps this widget's surrounding margins clear.
        self.setStyleSheet("QWidget { background: transparent; }")

        # -- runtime state -------------------------------------------------
        self._state = "idle"       # idle | listening | processing | done | error
        self._appear = 0.0         # entrance value 0..1 (opacity + scale)
        self._scale_value = 1.0    # state-dependent pill size factor
        self._shake_value = 0.0    # 0..1 across the error shake keyframes
        self._level = 0.0          # last smoothed mic loudness (0..1)
        self._phase = 0.0          # waveform texture phase
        self._dot_phase = 0.0      # processing-dots phase
        self._error_label = ""
        self._noactivate_applied = False

        # -- timers (all stopped when idle → zero CPU) ---------------------
        self._dot_timer = QTimer(self)              # processing: pulsing dots
        self._dot_timer.setInterval(self._timing.dot_ms)
        self._dot_timer.timeout.connect(self._on_dot_tick)
        self._autohide = QTimer(self)               # done/error auto-dismiss
        self._autohide.setSingleShot(True)
        self._autohide.timeout.connect(self._begin_hide)

        # -- animations ----------------------------------------------------
        aduras = self._timing
        self._show_anim = QVariantAnimation(self)   # fade + scale-in
        self._show_anim.setDuration(aduras.show_ms)
        self._show_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._show_anim.valueChanged.connect(self._on_appear_tick)
        self._hide_anim = QVariantAnimation(self)   # fade + scale-down out
        self._hide_anim.setDuration(aduras.hide_ms)
        self._hide_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hide_anim.valueChanged.connect(self._on_appear_tick)
        self._hide_anim.finished.connect(self._on_hidden)
        self._scale_anim = QVariantAnimation(self)  # pill size between states
        self._scale_anim.setDuration(aduras.scale_ms)
        self._scale_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._scale_anim.valueChanged.connect(self._on_scale_tick)
        self._shake_anim = QVariantAnimation(self)  # error shake
        self._shake_anim.setDuration(max(1, self._timing.error_ms // 4))
        self._shake_anim.setEasingCurve(QEasingCurve.Linear)
        self._shake_anim.valueChanged.connect(self._on_shake_tick)
        self._shake_anim.finished.connect(self._on_shake_done)

        # The recorder emits from its audio thread; this queued connection is
        # what hops the value onto the GUI thread.
        self.level_changed.connect(self.on_mic_level)

    # ==================================================================
    # Mic level bridge (GUI thread)
    # ==================================================================
    @Slot(float)
    def on_mic_level(self, value: float) -> None:
        """Receive one smoothed mic loudness value (0..1)."""
        self._level = max(0.0, min(1.0, float(value)))
        self._phase += _PHASE_STEP
        if self._state == "listening":
            self.update()   # repaint the waveform with the new level

    @property
    def state(self) -> str:
        """Current state: idle | listening | processing | done | error."""
        return self._state

    @property
    def current_level(self) -> float:
        """Latest smoothed mic level handed in through :attr:`level_changed`."""
        return self._level

    def bar_heights(self) -> list[int]:
        """Bar heights (px) the next repaint would draw."""
        return self.bar_heights_for(self._level, self._phase)

    @staticmethod
    def bar_heights_for(level: float, phase: float = 0.0) -> list[int]:
        """Map a mic level onto per-bar heights (clamped 6..26 px).

        The *envelope* comes exclusively from the real level; the light
        per-bar flutter only makes a steady voice look alive. Deterministic
        in ``(level, phase)`` for tests.
        """
        lvl = max(0.0, min(1.0, float(level)))
        heights = []
        for i in range(_BAR_COUNT):
            flutter = 0.5 + 0.5 * math.sin(phase * (1.25 + 0.13 * i) + i * 0.9)
            share = 0.28 + 0.72 * flutter          # 0.28 .. 1.00
            h = _BAR_MIN + (_BAR_MAX - _BAR_MIN) * lvl * share
            heights.append(int(round(min(_BAR_MAX, max(_BAR_MIN, h)))))
        return heights

    # ==================================================================
    # State machine (driven ONLY by the controller's dictation signals)
    # ==================================================================
    def set_listening(self) -> None:
        """Recording started: show the pill, ease in, start the waveform."""
        self._autohide.stop()
        self._state = "listening"
        self._level = 0.0
        self._phase = 0.0
        self._dot_timer.stop()
        self._scale_anim_to(1.0)
        self._begin_appear()
        self.update()

    def set_processing(self) -> None:
        """Recording stopped, transcription running: shrink, blue tint, dots."""
        self._autohide.stop()
        self._state = "processing"
        self._dot_timer.start(self._timing.dot_ms)
        self._scale_anim_to(0.92)
        self._begin_appear()
        self.update()

    def set_done(self) -> None:
        """Transcript typed at the caret: green check, then auto-hide."""
        self._autohide.stop()
        self._state = "done"
        self._stop_live_timers()
        self._scale_anim_to(0.92)
        self._begin_appear()
        self._autohide.start(self._timing.done_ms)
        self.update()

    def set_error(self, reason: str | None = None) -> None:
        """Failure (mic, network, provider, refused paste): shake, then hide.

        ``reason`` is expected to be the short, generic label from
        :func:`short_error_label` — never raw exception text.
        """
        self._autohide.stop()
        self._state = "error"
        # The reason is logged nowhere and shown nowhere on the pill; main.py
        # shows the full friendly explanation in the app window. Kept only so
        # embedders/tests can assert what was passed in.
        self._error_label = (reason or "").splitlines()[0].strip() or "Dictation failed"
        self._stop_live_timers()
        self._scale_anim_to(1.0)
        self._begin_appear()
        self._shake_anim.stop()
        self._shake_anim.setStartValue(0.0)
        self._shake_anim.setEndValue(1.0)
        self._shake_anim.start()
        self._autohide.start(self._timing.error_ms)   # never stick on screen
        self.update()

    def hide_now(self) -> None:
        """Animated hide (fade + scale-down, then idle)."""
        self._begin_hide()

    def force_hide(self) -> None:
        """Immediate hide with all timers/animations cancelled (shutdown)."""
        self._cancel_all()
        self._appear = 0.0
        self._state = "idle"
        self.hide()
        self.update()

    # internal aliases/properties used by tests -------------------------
    def _stop_live_timers(self) -> None:
        """Stop the per-state heartbeat timer (processing dots)."""
        self._dot_timer.stop()

    def _cancel_autohide(self) -> None:
        self._autohide.stop()

    def _cancel_all(self) -> None:
        self._stop_live_timers()
        self._autohide.stop()
        for anim in (self._show_anim, self._hide_anim,
                     self._scale_anim, self._shake_anim):
            anim.stop()

    def _timers_running(self) -> bool:
        """Whether any overlay timer is active (must be idle-only)."""
        return bool(self._dot_timer.isActive() or self._autohide.isActive())

    # ==================================================================
    # Appear / hide / scale plumbing
    # ==================================================================
    def _on_appear_tick(self, value: float) -> None:
        self._appear = float(value)
        self.update()

    def _on_scale_tick(self, value: float) -> None:
        self._scale_value = float(value)
        self.update()

    def _on_shake_tick(self, value: float) -> None:
        self._shake_value = float(value)
        self.update()

    def _on_shake_done(self) -> None:
        self._shake_value = 0.0
        self.update()

    def _on_dot_tick(self) -> None:
        self._dot_phase += 0.12   # ~1 s per full dot pulse cycle
        self.update()

    def _scale_anim_to(self, target: float) -> None:
        self._scale_anim.stop()
        self._scale_anim.setStartValue(self._scale_value)
        self._scale_anim.setEndValue(float(target))
        self._scale_anim.start()

    def _begin_appear(self) -> None:
        if not self.isVisible():
            self._place_and_show()
            start = 0.0
        elif self._appear >= 0.985:
            start = 0.94   # subtle re-pop when the state flips while visible
        else:
            start = max(0.0, self._appear)
        self._show_anim.stop()
        self._show_anim.setStartValue(start)
        self._show_anim.setEndValue(1.0)
        self._show_anim.start()
        if self._hide_anim.state() == QVariantAnimation.State.Running:
            self._hide_anim.stop()

    def _begin_hide(self) -> None:
        if not self.isVisible():
            self._on_hidden()
            return
        self._cancel_all()
        self._hide_anim.setStartValue(self._appear)
        self._hide_anim.setEndValue(0.0)
        self._hide_anim.start()

    def _on_hidden(self) -> None:
        """Back to true idle: everything stopped, nothing on screen."""
        self._appear = 0.0
        self._scale_value = 1.0
        self._shake_value = 0.0
        self._state = "idle"
        self._cancel_all()
        self.hide()
        self.update()

    # ==================================================================
    # Placement
    # ==================================================================
    def _place_and_show(self) -> None:
        self.position_for_cursor()
        # WA_ShowWithoutActivating prevents any focus change; the extra
        # WS_EX_NOACTIVATE makes this true even against tricky setups.
        self.show()
        if not self._noactivate_applied:
            self._make_unfocusable()
            self._noactivate_applied = True

    def position_for_cursor(self) -> None:
        """Dock at the bottom-centre of the monitor under the mouse cursor.

        The *visible pill* (not the shadow-marged widget) ends up
        :data:`TASKBAR_GAP` px above the taskbar, and never covers the caret
        when the foreground window reports one.
        """
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        x = avail.center().x() - self.width() // 2
        # Exclusive bottom edge of the usable desktop; PILL_BOTTOM_MARGIN is
        # the transparent space *inside* the widget below the pill.
        taskbar_top = avail.top() + avail.height()
        y = taskbar_top - TASKBAR_GAP - PILL_TOP_MARGIN - PILL_RECT.height()
        base = QRect(x, y, self.width(), self.height())
        base = self.avoid_caret(base, caret_screen_rect(), avail)
        self.move(base.topLeft())

    @staticmethod
    def avoid_caret(base: QRect | None, caret: QRect | None,
                    bounds: QRect) -> QRect:
        """Shift a planned pill rect so it never covers the caret.

        Tries *above* the caret first (usual typing position), then below;
        gives up silently when neither fits — decor never breaks layout.
        """
        if base is None:
            return QRect(0, 0, 0, 0)
        if caret is None or not caret.isValid() or base.isEmpty():
            return base
        guard = caret.adjusted(-32, -32, 32, 32)
        if not base.intersects(guard):
            return base
        above = QRect(base.left(), guard.top() - 12 - base.height(),
                      base.width(), base.height())
        if above.top() < bounds.top() + 4:
            above.moveTop(bounds.top() + 4)
        if not above.intersects(guard):
            return above
        below = QRect(base.left(), guard.bottom() + 12,
                      base.width(), base.height())
        if below.bottom() > bounds.bottom() - 4:
            below.moveBottom(bounds.bottom() - 4)
        if not below.intersects(guard):
            return below
        return base

    def _make_unfocusable(self) -> None:
        """Apply WS_EX_NOACTIVATE so this window can never be foregrounded.

        Qt's flags get us most of the way; this is the belt-and-braces that
        keeps the dictation paste-target check honest. Best-effort only.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes

            hwnd = int(self.winId())
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
            set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
            current = int(get_long(hwnd, -20))          # GWL_EXSTYLE
            desired = _noactivate_exstyle(current)
            if desired != current:
                set_long(hwnd, -20, desired)
        except Exception:  # noqa: BLE001 - the Qt flags are the first line
            pass

    # ==================================================================
    # Painting (transform/opacity only, ~60fps when visible)
    # ==================================================================
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        if self._state == "idle" or self._appear <= 0.001:
            return        # nothing painted → nothing to pay CPU for

        level = max(0.0, min(1.0, self._level))
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # entrance/exit: opacity + eased scale only (no geometry moves)
        p.setOpacity(max(0.0, min(1.0, self._appear)))
        entrance = 0.94 + 0.06 * self._appear      # 0.94 → 1.00, no overshoot
        scale = max(0.90, min(1.02, entrance * self._scale_value))
        pill = QRectF(PILL_RECT)
        centre = pill.center()
        p.translate(centre.x(), centre.y())
        p.scale(scale, scale)
        p.translate(-centre.x(), -centre.y())

        shake = self._shake_offset()
        if shake:
            p.translate(shake, 0.0)

        # — soft drop shadow, weighted downward (ambient + key layer) —
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0))
        for inflate, drop, alpha in ((13, 7, 4), (7, 3, 9), (3, 1, 16)):
            p.setOpacity(max(0.0, min(1.0, self._appear)) * alpha / 255.0)
            shifted = pill.translated(0.0, float(drop))
            p.drawRoundedRect(
                shifted.adjusted(-inflate, -inflate, inflate, inflate),
                PILL_RADIUS + inflate, PILL_RADIUS + inflate,
            )

        # — the pill body: dark frosted glass, faintly lighter at the top —
        p.setOpacity(max(0.0, min(1.0, self._appear)))
        p.setPen(Qt.NoPen)
        body = QLinearGradient(0.0, pill.top(), 0.0, pill.bottom())
        body.setColorAt(0.0, QColor(35, 37, 43, 232))
        body.setColorAt(1.0, QColor(21, 23, 28, 236))
        p.setBrush(QBrush(body))
        p.drawRoundedRect(pill, PILL_RADIUS, PILL_RADIUS)

        # — single hairline border; while listening its alpha breathes
        #    gently with the real mic level (no glow stroke) —
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(self._border_color(level), 1.0))
        p.drawRoundedRect(
            pill.adjusted(0.5, 0.5, -0.5, -0.5), PILL_RADIUS, PILL_RADIUS,
        )

        # — content: a single centre-aligned icon per state, never text —
        if self._state == "listening":
            self._paint_bars(p, pill)
        elif self._state == "processing":
            self._paint_dots(p, pill)
        elif self._state == "done":
            self._paint_check(p, pill)
        elif self._state == "error":
            self._paint_error(p, pill)

    def _shake_offset(self) -> float:
        """One short, heavily damped sideways nudge (not a cartoon shake)."""
        if self._shake_value <= 0.0:
            return 0.0
        t = self._shake_value
        return 4.0 * (1.0 - t) ** 2 * math.sin(t * 3.0 * math.pi)

    def _border_color(self, level: float) -> QColor:
        """Hairline border colour for the current state.

        ``level`` (0..1) only affects the listening state, where the border
        brightens at most from 10% to ~23% white — a quiet cue that the
        microphone is live, never a glow.
        """
        if self._state == "listening":
            return QColor(255, 255, 255, 26 + int(34 * level))
        if self._state == "processing":
            return QColor(_ACCENT.red(), _ACCENT.green(), _ACCENT.blue(), 115)
        if self._state == "done":
            return QColor(_GREEN.red(), _GREEN.green(), _GREEN.blue(), 120)
        return QColor(_RED.red(), _RED.green(), _RED.blue(), 150)

    def _paint_bars(self, p: QPainter, pill: QRectF) -> None:
        """8 rounded monochrome bars (Apple voice-memo style), centred."""
        heights = self.bar_heights_for(self._level, self._phase)
        bar_color = QColor(_WHITE)
        bar_color.setAlpha(242)
        penless = QPen(Qt.NoPen)
        p.setPen(penless)
        p.setBrush(QBrush(bar_color))
        row_width = _BAR_COUNT * _BAR_WIDTH + (_BAR_COUNT - 1) * _BAR_GAP
        x = pill.center().x() - row_width / 2.0
        cy = pill.center().y() + 1.0
        for h in heights:
            p.drawRoundedRect(
                QRectF(x, cy - h / 2.0, _BAR_WIDTH, float(h)),
                _BAR_WIDTH / 2.0, _BAR_WIDTH / 2.0,   # rounded caps
            )
            x += _BAR_WIDTH + _BAR_GAP

    def _paint_dots(self, p: QPainter, pill: QRectF) -> None:
        """iMessage-style pulse: three white dots fading in sequence."""
        centre_x = pill.center().x()
        cy = pill.center().y()
        p.setPen(Qt.NoPen)
        for i in range(3):
            pulse = 0.5 + 0.5 * math.sin(self._dot_phase
                                         + i * (2.0 * math.pi / 3.0))
            radius = 3.8 if i == 1 else 3.2
            dot = QColor(_WHITE)
            dot.setAlpha(int(88 + 150 * pulse))       # 0.35 .. 0.93 white
            p.setBrush(QBrush(dot))
            drift = 1.5 * (pulse - 0.5)               # barely moves: 3px
            p.drawEllipse(QPointF(centre_x + (i - 1) * 14.0, cy - drift),
                          radius, radius)

    def _paint_check(self, p: QPainter, pill: QRectF) -> None:
        """Single green checkmark, centred — icon-only success moment."""
        cx = pill.center().x()
        cy = pill.center().y()
        check = QPen(_GREEN, 3.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(check)
        p.drawLine(QPointF(cx - 10.0, cy + 2.0), QPointF(cx - 3.0, cy + 8.0))
        p.drawLine(QPointF(cx - 3.0, cy + 8.0), QPointF(cx + 11.0, cy - 8.0))

    def _paint_error(self, p: QPainter, pill: QRectF) -> None:
        """Centred red badge with a painter-drawn '!' — icon only, no text."""
        cx = pill.center().x()
        cy = pill.center().y()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(_RED))          # opaque Apple-red disc
        p.drawEllipse(QPointF(cx, cy), 10.0, 10.0)
        # Exclamation glyph drawn from shapes (rounded bar + dot), so the
        # error icon contains no fonts or strings at all.
        p.setBrush(QBrush(_WHITE))
        p.drawRoundedRect(QRectF(cx - 1.8, cy - 9.5, 3.6, 11.0), 1.8, 1.8)
        p.drawEllipse(QPointF(cx, cy + 5.5), 2.0, 2.0)
