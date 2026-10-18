"""Application controller.

Central coordinator between the UI, providers, Windows integration, secure
credentials, settings and history. All blocking work (selection capture, API
calls, clipboard/paste) runs on worker threads; results are delivered back to
the Qt main thread through signals.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QThread, Signal

from core.config import AppSettings
from core.enhancement import build_messages, validate_input
from core.errors import ShortcutError
from core.models import (
    CaptureResult,
    EnhanceMode,
    EnhanceResult,
    HistoryEntry,
    ReplaceResult,
)
from providers.registry import create_adapter_from_settings
from security.credstore import (
    backend_label,
    delete_secret,
    load_secret,
    save_secret,
)
from shortcuts.global_hotkey import GlobalHotkeyManager
from speech.dictation import DictationService
from storage.history import HistoryStore
from storage.settings import SettingsStore
from windows_integration import clipboard as clipboard_mod
from windows_integration.caret import CaretPaster
from windows_integration.capture import SelectionCapturer
from windows_integration.replace import TextReplacer

from .jobs import run_async


class DictationWorker(QThread):
    """Stops the recorder, transcribes, and types the result where it came from.

    All three steps are blocking, so they run off the UI thread. The transcript
    is typed into the window that was in front when dictation started - the user
    expects the words at their caret, not inside QuickPrompt.
    """

    ok = Signal(object)                  # DictationResult, typed into the target
    fail = Signal(object)                # dictation itself failed
    not_pasted = Signal(object, object)  # (DictationResult, paste error)

    def __init__(self, service, paster=None, target=None, parent=None):
        super().__init__(parent)
        self._service = service
        self._paster = paster
        self._target = target

    def run(self):  # noqa: D102 - worker thread
        try:
            result = self._service.stop_and_transcribe()
        except Exception as exc:  # noqa: BLE001
            self.fail.emit(exc)
            return
        try:
            self._paster.paste_into(self._target, result.text)
        except Exception as exc:  # noqa: BLE001 - reported, text never lost
            self.not_pasted.emit(result, exc)
        else:
            self.ok.emit(result)


class CaptureEnhanceWorker(QThread):
    """Captures the selection, then enhances it — with staged progress."""

    stage = Signal(str)                 # "capturing" | "enhancing"
    captured = Signal(object)           # CaptureResult
    ok = Signal(object, object)         # (CaptureResult, EnhanceResult)
    fail = Signal(str, object)          # ("capture"|"enhance", error)

    def __init__(self, capturer, enhance_fn, parent=None):
        super().__init__(parent)
        self._capturer = capturer
        self._enhance_fn = enhance_fn

    def run(self):  # noqa: D102 - worker thread
        try:
            self.stage.emit("capturing")
            capture = self._capturer.capture()
        except Exception as exc:  # noqa: BLE001
            self.fail.emit("capture", exc)
            return
        self.captured.emit(capture)
        try:
            self.stage.emit("enhancing")
            result = self._enhance_fn(capture.text)
        except Exception as exc:  # noqa: BLE001
            self.fail.emit("enhance", exc)
            return
        self.ok.emit(capture, result)


class AppController(QObject):
    # Popup flow -------------------------------------------------------
    popup_stage = Signal(str)              # human-readable stage text
    popup_capture_ready = Signal(object)   # CaptureResult
    popup_result = Signal(object, object)  # (CaptureResult|None, EnhanceResult)
    popup_error = Signal(str, object)      # (stage, error)
    popup_replace_done = Signal(object)    # ReplaceResult
    popup_replace_failed = Signal(object)  # error
    popup_closed = Signal()

    # Misc -------------------------------------------------------------
    hotkey_fired = Signal()                # emitted from the hotkey thread
    shortcut_changed = Signal(str)         # canonical combo
    shortcut_failed = Signal(object)       # error
    status_message = Signal(str)
    test_finished = Signal(object)         # TestConnectionResult | error

    # Dictation (speech-to-text) ----------------------------------------
    #: The dictation hotkey was pressed (emitted from the hotkey thread).
    dictation_hotkey_fired = Signal()
    #: Recording began — the UI should show a "listening" indicator.
    dictation_started = Signal()
    #: Transcription finished; the text should be inserted at the cursor.
    dictation_text_ready = Signal(str)
    #: Dictation failed at any stage (mic, network, API); payload is an error.
    dictation_failed = Signal(object)
    #: Transcribed fine, but it could not be typed into the original window.
    #: Payload is ``(text, error)`` so the UI can still show the transcript.
    dictation_not_pasted = Signal(str, object)
    #: Recording stopped and transcription is now running.
    dictation_processing = Signal()
    dictation_shortcut_changed = Signal(str)
    dictation_shortcut_failed = Signal(object)

    def __init__(
        self,
        settings_store: SettingsStore | None = None,
        history_store: HistoryStore | None = None,
        capturer=None,
        replacer=None,
        hotkey_manager=None,
        dictation_hotkey_manager=None,
        dictation_service=None,
        dictation_paster=None,
    ):
        super().__init__()
        self.settings_store = settings_store or SettingsStore()
        self.settings: AppSettings = self.settings_store.load()
        self.history_store = history_store or HistoryStore(
            max_entries=self.settings.history_max
        )
        self._capturer = capturer or SelectionCapturer()
        self._replacer = replacer or TextReplacer()
        self._hotkeys = hotkey_manager or GlobalHotkeyManager(self._on_hotkey_thread)
        # Dictation uses a *second* global hotkey so it never clashes with the
        # enhance shortcut. The service reads the live settings on demand, so
        # provider/shortcut edits apply to the very next utterance.
        self._dictation_hotkeys = dictation_hotkey_manager or GlobalHotkeyManager(
            self._on_dictation_hotkey_thread
        )
        self._dictation = dictation_service or DictationService(
            settings_provider=lambda: self.settings
        )
        # Types the transcript back into the window the user was writing in.
        self._dictation_paster = dictation_paster or CaretPaster()
        self._workers: list[QThread] = []
        self._generation = 0
        self._current_capture: CaptureResult | None = None
        self._dictating = False          # True while the mic is open
        self._dictation_worker = None    # in-flight transcription (if any)
        self._dictation_target = None    # window to type the transcript into

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        self._generation += 1
        try:
            self._hotkeys.unregister()
        except Exception:
            pass
        try:
            self._dictation_hotkeys.unregister()
        except Exception:
            pass
        # Never leave the microphone open on quit.
        try:
            self._dictation.recorder.stop()
        except Exception:
            pass
        self._dictating = False
        self._dictation_target = None
        for worker in list(self._workers):
            try:
                worker.wait(2500)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Settings & credentials
    # ------------------------------------------------------------------
    def save_settings(self, settings: AppSettings) -> None:
        settings.normalize()
        self.settings = settings
        self.settings_store.save(settings)
        self.history_store.set_max(settings.history_max)

    def has_api_key(self, provider_id: str | None = None) -> bool:
        return bool(load_secret(provider_id or self.settings.active_provider))

    def api_key_status(self) -> tuple[bool, str]:
        """(has_key_for_active_provider, backend_label)."""
        return self.has_api_key(), backend_label()

    def save_api_key(self, key: str, provider_id: str | None = None) -> None:
        save_secret(provider_id or self.settings.active_provider, key.strip())

    def delete_api_key(self, provider_id: str | None = None) -> bool:
        return delete_secret(provider_id or self.settings.active_provider)

    def clear_history(self) -> None:
        self.history_store.clear()

    # ------------------------------------------------------------------
    # Hotkeys
    # ------------------------------------------------------------------
    def start_hotkeys(self) -> bool:
        """Register both global hotkeys (enhance + dictation).

        Returns ``False`` only when the *enhance* shortcut fails — that one is
        the app's core feature. A dictation failure is reported through its own
        signal and leaves the rest of the app fully usable.
        """
        try:
            canonical = self._hotkeys.register(self.settings.shortcut)
        except ShortcutError as exc:
            self.settings.shortcut = self.settings.shortcut or "Ctrl+Shift+E"
            self.shortcut_failed.emit(exc)
            ok = False
        else:
            self.shortcut_changed.emit(canonical)
            ok = True
        # Register dictation independently: the two are separate hooks, so one
        # being taken by another app must not silently disable the other.
        self._start_dictation_hotkey()
        return ok

    def _start_dictation_hotkey(self) -> bool:
        try:
            canonical = self._dictation_hotkeys.register(
                self.settings.dictation_shortcut
            )
        except ShortcutError as exc:
            # Keep the configured value so the user can fix it in the UI; the
            # enhance shortcut keeps working meanwhile.
            self.dictation_shortcut_failed.emit(exc)
            return False
        self.dictation_shortcut_changed.emit(canonical)
        return True

    @property
    def hotkeys_active(self) -> bool:
        """Whether the global shortcut has finished registering."""
        return bool(
            getattr(self._hotkeys, "is_registered", False)
            or getattr(self._hotkeys, "combo", None)
        )

    def apply_shortcut(self, combo: str) -> str:
        """Register a new shortcut and persist it (rollback on failure)."""
        canonical = self._hotkeys.register(combo)
        self.settings.shortcut = canonical
        self.settings_store.save(self.settings)
        self.shortcut_changed.emit(canonical)
        return canonical

    def apply_dictation_shortcut(self, combo: str) -> str:
        """Register and persist the dictation shortcut.

        Raises :class:`ShortcutError` if the combination is taken by another
        app *or* by the enhance shortcut itself; the previous dictation
        shortcut stays active in that case.
        """
        from shortcuts.global_hotkey import parse_combo

        canonical, _fmt = parse_combo(combo)
        if canonical == self.current_shortcut():
            raise ShortcutError(
                f"{canonical} is already used by the enhance shortcut — pick "
                "a different combination for dictation."
            )
        registered = self._dictation_hotkeys.register(canonical)
        self.settings.dictation_shortcut = registered
        self.settings_store.save(self.settings)
        self.dictation_shortcut_changed.emit(registered)
        return registered

    def current_dictation_shortcut(self) -> str:
        return self._dictation_hotkeys.combo or self.settings.dictation_shortcut

    def current_shortcut(self) -> str:
        return self._hotkeys.combo or self.settings.shortcut

    def _on_hotkey_thread(self) -> None:
        # Called on the pynput listener thread; Qt queues this to the main thread.
        self.hotkey_fired.emit()

    def _on_dictation_hotkey_thread(self) -> None:
        # Also called from the pynput listener thread.
        self.dictation_hotkey_fired.emit()

    # ------------------------------------------------------------------
    # Dictation (speech-to-text)
    # ------------------------------------------------------------------
    @property
    def is_dictating(self) -> bool:
        """Whether the microphone is currently recording."""
        return self._dictating

    def toggle_dictation(self) -> None:
        """Start recording, or stop + transcribe if already recording.

        This is the dictation-shortcut entry point. It runs on the UI thread
        (the hotkey callback is marshalled there by Qt), and the potentially
        slow transcription happens on a worker thread.
        """
        if self._dictating:
            self.stop_dictation()
            return
        self.start_dictation()

    def start_dictation(self) -> None:
        """Open the microphone immediately (no warm-up) and show feedback."""
        if self._dictating:
            return
        # Remember where the user is typing *before* the mic opens: nothing of
        # ours is raised during dictation, so the foreground window is still
        # the one the shortcut was pressed in.
        self._dictation_target = self._dictation_paster.capture_target()
        try:
            self._dictation.start_recording()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self._dictating = False
            self._dictation_target = None
            self.dictation_failed.emit(exc)
            return
        self._dictating = True
        self.dictation_started.emit()

    def stop_dictation(self) -> None:
        """Stop recording and transcribe in the background."""
        if not self._dictating:
            return
        self._dictating = False
        self.dictation_processing.emit()
        # Bumping the generation discards a *dictation* result if the user
        # starts another one (or an enhance) before this one lands.
        self._generation += 1
        gen = self._generation
        worker = DictationWorker(
            self._dictation,
            paster=self._dictation_paster,
            target=self._dictation_target,
            parent=self,
        )
        worker.ok.connect(
            self._guard(gen, lambda result: self._on_dictation_ok(result))
        )
        worker.fail.connect(
            self._guard(gen, lambda error: self._on_dictation_fail(error))
        )
        worker.not_pasted.connect(
            self._guard(gen, self._on_dictation_not_pasted)
        )
        # Mirror run_async's lifecycle: release the C++ thread object once it
        # is done so it cannot outlive the controller at interpreter shutdown.
        # Clearing the reference on ``finished`` (not in the guarded handlers)
        # guarantees we never keep a handle to a deleted C++ object.
        worker.finished.connect(lambda w=worker: self._forget_dictation_worker(w))
        worker.finished.connect(worker.deleteLater)
        self._dictation_worker = worker
        self._track(worker)
        worker.start()

    def _forget_dictation_worker(self, worker) -> None:
        if self._dictation_worker is worker:
            self._dictation_worker = None

    def _on_dictation_ok(self, result) -> None:
        self._dictation_target = None
        self.dictation_text_ready.emit(result.text)

    def _on_dictation_fail(self, error: Exception) -> None:
        self._dictation_target = None
        self.dictation_failed.emit(error)

    def _on_dictation_not_pasted(self, result, error: Exception) -> None:
        # The words exist but could not be typed where the user was working.
        # Hand them to the UI so they land in our editor instead of vanishing.
        self._dictation_target = None
        self.dictation_not_pasted.emit(result.text, error)

    # ------------------------------------------------------------------
    # Enhancement
    # ------------------------------------------------------------------
    def enhance_text(self, text: str, mode: EnhanceMode) -> EnhanceResult:
        """Synchronous enhancement (call from a worker thread)."""
        cleaned = validate_input(text, self.settings.max_input_chars)
        api_key = load_secret(self.settings.active_provider)
        adapter = create_adapter_from_settings(self.settings, api_key)
        system_prompt, user_prompt = build_messages(
            cleaned, mode, self.settings.model_strategy
        )
        result = adapter.enhance(system_prompt, user_prompt)
        self._record_history(cleaned, mode, result)
        return result

    def _record_history(self, original: str, mode: EnhanceMode,
                        result: EnhanceResult) -> None:
        if not self.settings.history_enabled:
            return
        try:
            self.history_store.add(HistoryEntry(
                timestamp=time.time(),
                mode=mode.value,
                provider_id=result.provider_id or self.settings.active_provider,
                model=result.model,
                original=original,
                enhanced=result.text,
                input_tokens=result.usage.input_tokens if result.usage else 0,
                output_tokens=result.usage.output_tokens if result.usage else 0,
            ))
        except Exception:
            pass  # history must never break enhancement

    # ------------------------------------------------------------------
    # Popup workflow (hotkey-triggered)
    # ------------------------------------------------------------------
    def trigger_popup_enhance(self, mode: EnhanceMode | None = None) -> None:
        """Hotkey entry point: capture selection, then enhance it."""
        mode = mode or EnhanceMode.PROMPT_ENHANCE
        self._generation += 1
        gen = self._generation
        self.popup_stage.emit("Capturing selected text…")

        worker = CaptureEnhanceWorker(
            self._capturer,
            lambda text: self.enhance_text(text, mode),
            parent=self,
        )
        worker.stage.connect(
            self._guard(gen, lambda stage: self.popup_stage.emit(
                "Capturing selected text…" if stage == "capturing"
                else "Enhancing with the AI provider…"
            ))
        )
        worker.captured.connect(
            self._guard(gen, self._on_popup_captured)
        )
        worker.ok.connect(
            self._guard(gen, lambda capture, result: self._on_popup_result(capture, result))
        )
        worker.fail.connect(
            self._guard(gen, lambda stage, error: self._on_popup_fail(stage, error))
        )
        self._track(worker)
        worker.start()

    def retry_popup_enhance(self, text: str, mode: EnhanceMode) -> None:
        """Re-run enhancement on the (possibly edited) text in the popup."""
        self._generation += 1
        gen = self._generation
        self.popup_stage.emit("Enhancing with the AI provider…")

        def job():
            return self.enhance_text(text, mode)

        worker = run_async(
            job,
            self._guard(gen, lambda result: self._on_popup_result(self._current_capture, result)),
            self._guard(gen, lambda error: self._on_popup_fail("enhance", error)),
            parent=self,
        )
        self._track(worker)

    def cancel_popup_operation(self) -> None:
        """Abandon the in-flight popup operation (results are discarded)."""
        self._generation += 1
        self.popup_closed.emit()

    def replace_selected_text(self, new_text: str) -> None:
        """Replace the original selection (only after explicit user action)."""
        capture = self._current_capture
        self._generation += 1
        gen = self._generation
        if capture is None:
            from core.errors import WindowGoneError

            self.popup_replace_failed.emit(WindowGoneError(
                "There is no captured selection to replace in this session. "
                "Copy the result and paste it manually."
            ))
            return

        def job():
            return self._replacer.replace(capture, new_text)

        worker = run_async(
            job,
            self._guard(gen, self._on_replace_done),
            self._guard(gen, self._on_replace_failed),
            parent=self,
        )
        self._track(worker)

    def copy_to_clipboard(self, text: str) -> bool:
        try:
            clipboard_mod.set_text(text)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Popup callbacks (main thread)
    # ------------------------------------------------------------------
    def _on_popup_captured(self, capture: CaptureResult) -> None:
        self._current_capture = capture
        self.popup_capture_ready.emit(capture)

    def _on_popup_result(self, capture: CaptureResult | None, result: EnhanceResult) -> None:
        self._current_capture = capture
        self.popup_stage.emit("")
        self.popup_result.emit(capture, result)

    def _on_popup_fail(self, stage: str, error: Exception) -> None:
        self.popup_stage.emit("")
        self.popup_error.emit(stage, error)

    def _on_replace_done(self, result: ReplaceResult) -> None:
        self._current_capture = None
        self.popup_replace_done.emit(result)

    def _on_replace_failed(self, error: Exception) -> None:
        # If the original window vanished, fall back to clipboard so the
        # user does not lose the generated result.
        from core.errors import WindowGoneError

        if isinstance(error, WindowGoneError):
            pass  # popup shows the error and offers Copy
        self.popup_replace_failed.emit(error)

    # ------------------------------------------------------------------
    # Test connection
    # ------------------------------------------------------------------
    def test_connection_async(self, provider_id: str, api_key: str,
                              model: str, base_url: str,
                              timeout: float) -> None:
        from providers.registry import create_adapter

        def job():
            adapter = create_adapter(
                provider_id,
                api_key=api_key,
                model=model,
                base_url=base_url,
                timeout=timeout,
            )
            return adapter.test_connection()

        worker = run_async(job, self.test_finished.emit, self.test_finished.emit,
                           parent=self)
        self._track(worker)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _track(self, worker: QThread) -> None:
        self._workers.append(worker)
        worker.finished.connect(lambda w=worker: self._forget(w))

    def _forget(self, worker: QThread) -> None:
        try:
            self._workers.remove(worker)
        except ValueError:
            pass

    def _guard(self, gen: int, fn):
        """Wrap ``fn`` so it only runs while its generation is current."""

        def wrapper(*args):
            if gen != self._generation:
                return None
            return fn(*args)

        return wrapper
