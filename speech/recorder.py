"""Microphone capture for dictation (the native ``MediaRecorder`` equivalent).

The browser's ``MediaRecorder`` streams encoded audio to a ``Blob``. On the
desktop the closest equivalent is PortAudio (via the ``sounddevice`` package):
we open the default input device, pull 16-bit PCM blocks on a background
thread, and encode them into an in-memory WAV file when recording stops.

Design notes
------------
* **16 kHz mono** — Whisper was trained on 16 kHz mono audio, so recording at
  that rate avoids any resampling by the service and keeps uploads small
  (~32 KB/s). Devices that cannot do 16 kHz are resampled by PortAudio, which
  is still far cheaper than uploading 48 kHz audio.
* **Immediate start** — ``start()`` opens the stream synchronously and returns
  as soon as the device is running, so there is no warm-up before speaking.
* **Friendly failures** — every PortAudio failure mode (missing package, no
  device, permission denied) is translated into a :class:`MicrophoneError`
  with a message a non-technical user can act on.
"""
from __future__ import annotations

import array
import io
import math
import wave

from core.errors import MicrophoneError

#: Whisper's native input rate. Recording here means the audio needs no
#: resampling on the server side.
SAMPLE_RATE = 16_000
_CHANNELS = 1          # mono — stereo doubles the upload for no benefit
_SAMPLE_WIDTH = 2     # 16-bit PCM
_BLOCK_SECONDS = 0.02  # capture granularity (~20 ms per callback; ~50/s, which
                       # is also the update rate the waveform overlay needs)

#: Asymmetric smoothing for the loudness meter (see ``_update_level``):
#: spikes punch through almost instantly, the tail decays more slowly so the
#: bars resemble real speech instead of blinking.
_LEVEL_ATTACK = 0.70   # rise towards the target (fast)
_LEVEL_RELEASE = 0.15  # fall towards the target (slower)
_NOISE_GATE = 0.0025   # RMS below this counts as silence (room tone)


def encode_wav(frames: list[bytes], sample_rate: int = SAMPLE_RATE) -> bytes:
    """Encode raw 16-bit PCM blocks into an in-memory mono WAV file.

    ``frames`` are the concatenated ``bytes`` blocks produced by the audio
    callback. The result is a standard RIFF/WAVE container, which the Whisper
    ``/audio/transcriptions`` endpoint accepts directly.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(_CHANNELS)
        wav.setsampwidth(_SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        for block in frames:
            if block:
                wav.writeframes(block)
    return buffer.getvalue()


class AudioRecorder:
    """Toggle-style microphone recorder: ``start()`` … ``stop()`` → WAV bytes.

    ``stream_factory`` is an injection seam for tests; production code uses
    the default PortAudio-backed factory.

    ``level_callback`` (optional, settable any time) receives the smoothed
    mic loudness as a float 0.0–1.0 roughly 50 times per second, straight from
    the PortAudio callback thread. The dictation waveform overlay connects a
    Qt signal to it so the value is marshalled into the GUI thread.
    """

    def __init__(self, *, stream_factory=None, level_callback=None):
        self._stream_factory = stream_factory
        self.level_callback = level_callback
        self._stream = None
        self._frames: list[bytes] = []
        self._level = 0.0  # last smoothed loudness (0..1)

    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        """Whether the microphone is currently being captured."""
        return self._stream is not None

    def start(self) -> None:
        """Begin capturing immediately. Raises :class:`MicrophoneError`.

        Calling this while already recording is a no-op so a double-pressed
        shortcut can never open the device twice.
        """
        if self.active:
            return
        self._frames = []
        self._level = 0.0

        def _on_audio(indata, _frames, _time_info, _status) -> None:
            # Runs on PortAudio's thread; ``bytes(indata)`` copies the block so
            # it stays valid after PortAudio reuses its buffer.
            self._frames.append(bytes(indata))
            # Loudness metering rides the same callback: one real RMS value
            # per ~20 ms block, no timers and no extra polling anywhere.
            self._update_level(indata)

        try:
            stream = self._open_stream(_on_audio)
            stream.start()
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "")
            package = "NumPy" if missing == "numpy" else "sounddevice"
            raise MicrophoneError(
                f"Speech-to-text needs the '{package}' package. "
                "Run install.bat (or reinstall QuickPrompt AI), then try again."
            ) from exc
        except ImportError as exc:
            raise MicrophoneError(
                "Speech-to-text could not load its sounddevice/NumPy microphone dependencies. "
                "Run install.bat (or reinstall QuickPrompt AI), then try again."
            ) from exc
        except Exception as exc:  # noqa: BLE001 - every PortAudio failure
            raise self._friendly_open_error(exc) from exc
        self._stream = stream

    def stop(self) -> bytes | None:
        """Stop capturing and return the recording as WAV bytes.

        Returns ``None`` when nothing was recorded (for example when the user
        pressed the shortcut twice before saying anything).
        """
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001 - device may already be gone
                pass
        frames, self._frames = self._frames, []
        if not frames:
            return None
        return encode_wav(frames)

    # ------------------------------------------------------------------
    def _update_level(self, indata) -> None:
        """Meter one audio block and push the smoothed RMS to ``level_callback``.

        Runs on the PortAudio thread. The value is normalised loudness 0..1
        with a perceptual squash and a silence gate, smoothed asymmetrically
        (rises fast, falls slower) so the dictation overlay's bars read like a
        real waveform. Metering must never break recording: any failure is
        swallowed and the callback is optional.
        """
        try:
            raw = indata.tobytes() if hasattr(indata, "tobytes") else bytes(indata)
            samples = array.array("h")
            samples.frombytes(raw)
            if samples:
                rms = math.sqrt(
                    sum(abs(s) * abs(s) for s in samples) / len(samples)
                ) / 32768.0
            else:
                rms = 0.0
            target = (
                0.0 if rms < _NOISE_GATE
                else min(1.0, (rms * 6.0) ** 0.6)
            )
        except Exception:  # noqa: BLE001 - metering may never kill the mic
            target = self._level
        smoothing = _LEVEL_ATTACK if target > self._level else _LEVEL_RELEASE
        self._level = self._level + (target - self._level) * smoothing
        if self.level_callback is not None:
            try:
                self.level_callback(self._level)
            except Exception:  # noqa: BLE001 - a broken meter never breaks audio
                pass

    @property
    def level(self) -> float:
        """Latest smoothed loudness (0.0–1.0) of the running recording."""
        return self._level

    # ------------------------------------------------------------------
    def _open_stream(self, callback):
        """Create (but do not start) the input stream."""
        if self._stream_factory is not None:
            return self._stream_factory(callback)
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise ImportError("sounddevice is not installed") from exc
        return sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=_CHANNELS,
            dtype="int16",
            blocksize=int(SAMPLE_RATE * _BLOCK_SECONDS),
            callback=callback,
        )

    @staticmethod
    def _friendly_open_error(exc: Exception) -> MicrophoneError:
        """Translate a PortAudio/open-device failure into a clear message."""
        text = str(exc).lower()
        if "permission" in text or "denied" in text or "not authorized" in text:
            return MicrophoneError(
                "Windows blocked microphone access. Allow desktop apps to use "
                "the microphone in Settings → Privacy → Microphone, then retry."
            )
        if any(word in text for word in ("no default input", "no device", "device unavailable")):
            return MicrophoneError(
                "No microphone was found. Connect or enable a microphone, "
                "then try again."
            )
        return MicrophoneError(
            f"The microphone could not be opened ({exc.__class__.__name__}). "
            "Check your input device and try again."
        )
