"""Speech-to-text (dictation) support.

This package owns everything specific to turning the user's voice into text:

* :mod:`speech.recorder` — microphone capture (the native equivalent of the
  browser ``MediaRecorder`` API). It records a short utterance at 16 kHz mono
  and hands back WAV bytes, the container/sample-rate Whisper expects.
* :mod:`speech.dictation` — the record → transcribe → insert pipeline that the
  controller drives when the dictation shortcut fires.

Transcription itself is *not* implemented here: it reuses the active
provider's existing Whisper endpoint through the provider adapter layer.
"""
from __future__ import annotations

from .recorder import SAMPLE_RATE, AudioRecorder
from .dictation import DictationError, DictationResult

__all__ = ["AudioRecorder", "SAMPLE_RATE", "DictationError", "DictationResult"]
