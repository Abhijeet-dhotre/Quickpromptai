"""Dictation pipeline: record → transcribe → hand the text back.

Kept free of Qt so it can be unit-tested headlessly. The controller owns the
threading; this module owns the *what* — it transcribes one recording using
the active provider's existing Whisper endpoint and packages the outcome.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.errors import QuickPromptError, friendly_error
from providers.registry import create_adapter_from_settings
from security.credstore import load_secret
from speech.recorder import AudioRecorder


class DictationError(QuickPromptError):
    """A dictation step failed (microphone, network, or API)."""

    code = "dictation"
    default_message = "Speech-to-text failed. Please try again."


@dataclass
class DictationResult:
    """Successful transcription of one recording."""

    text: str
    provider_id: str = ""
    model: str = ""


class DictationService:
    """Transcribes recordings with the app's current provider configuration.

    ``settings`` and ``recorder`` are injected so tests can supply fakes; the
    defaults wire the service to the live configuration and a real microphone.
    """

    def __init__(self, settings_provider, recorder: AudioRecorder | None = None):
        # ``settings_provider`` is a zero-arg callable returning the *current*
        # AppSettings, so edits made in the UI apply to the next dictation
        # without restarting.
        self._settings = settings_provider
        self.recorder = recorder or AudioRecorder()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def start_recording(self) -> None:
        """Open the microphone. Raises :class:`core.errors.MicrophoneError`."""
        self.recorder.start()

    def stop_and_transcribe(self) -> DictationResult:
        """Stop recording, then transcribe the captured audio.

        Raises :class:`DictationError` when there is nothing to transcribe or
        the provider call fails; the original provider error is preserved on
        ``__cause__`` for logging and re-raised as the message source.
        """
        wav_bytes = self.recorder.stop()
        if not wav_bytes:
            raise DictationError(
                "No audio was captured — nothing to transcribe. "
                "Press the shortcut, speak, then press it again to stop."
            )
        return self.transcribe(wav_bytes)

    def transcribe(self, wav_bytes: bytes) -> DictationResult:
        """Send ``wav_bytes`` to the provider's Whisper endpoint."""
        settings = self._settings()
        api_key = load_secret(settings.active_provider)
        if not api_key:
            raise DictationError(
                "No API key is configured for speech-to-text. "
                "Open Settings → Provider and save your API key first."
            )
        adapter = create_adapter_from_settings(settings, api_key)
        if not getattr(adapter.capabilities, "supports_audio_transcription", False):
            raise DictationError(
                f"{adapter.display_name} does not support speech-to-text. "
                "Switch to OpenAI, Groq or an OpenAI-compatible endpoint "
                "that exposes the Whisper /audio/transcriptions route."
            )
        # ""/"auto" -> None: the adapter resolves its provider-specific
        # default (model IDs are NOT shared between providers).
        requested = (settings.whisper_model or "").strip()
        effective = (
            requested
            or getattr(adapter.capabilities, "default_whisper_model", "")
            or "whisper-1"
        )
        try:
            text = adapter.transcribe_audio(wav_bytes, model=requested or None)
        except QuickPromptError:
            raise
        except Exception as exc:  # noqa: BLE001 - network/codec surprises
            raise DictationError(friendly_error(exc)) from exc
        return DictationResult(
            text=text, provider_id=settings.active_provider, model=effective
        )
