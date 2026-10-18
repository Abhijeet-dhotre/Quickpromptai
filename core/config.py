"""Application settings model, defaults, validation and path handling."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, asdict

from .models import MODEL_STRATEGIES

APP_NAME = "QuickPromptAI"
DEFAULT_SHORTCUT = "Ctrl+Shift+E"
DEFAULT_DICTATION_SHORTCUT = "Ctrl+Shift+S"
#: Dictation model. Empty string = "auto": use the active provider's default
#: Whisper model (Groq and OpenAI do not share model IDs — "whisper-1" is an
#: OpenAI-only ID that 404s on Groq, so hardcoding it breaks dictation there).
DEFAULT_WHISPER_MODEL = ""
SUPPORTED_PROVIDERS = ("openai", "anthropic", "gemini", "groq", "openai_compat")


def data_dir() -> str:
    """Directory for settings/history/secrets (honors QUICKPROMPT_DATA_DIR)."""
    override = os.environ.get("QUICKPROMPT_DATA_DIR")
    if override:
        path = os.path.expanduser(override)
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, APP_NAME)
    else:  # pragma: no cover - development convenience on non-Windows
        path = os.path.join(os.path.expanduser("~"), ".quickprompt")
    os.makedirs(path, exist_ok=True)
    return path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


@dataclass
class AppSettings:
    """Everything the app persists — contains NO secrets by design."""

    active_provider: str = "openai"
    models: dict[str, str] = field(default_factory=dict)          # provider -> model
    base_urls: dict[str, str] = field(default_factory=dict)       # provider -> base URL
    shortcut: str = DEFAULT_SHORTCUT
    # Speech-to-text (dictation): global hotkey + transcription model. The
    # audio always goes to the ACTIVE provider's endpoint (no extra config).
    # whisper_model "" = auto (provider-specific default, see capabilities).
    dictation_shortcut: str = DEFAULT_DICTATION_SHORTCUT
    whisper_model: str = DEFAULT_WHISPER_MODEL
    timeout: float = 30.0
    max_tokens: int = 2048              # 0 -> provider default
    temperature_enabled: bool = False
    temperature: float = 0.7
    model_strategy: str = "universal"   # universal|chatgpt|claude|gemini
    max_input_chars: int = 24000
    history_enabled: bool = True
    history_max: int = 100
    price_overrides: dict[str, dict] = field(default_factory=dict)
    version: int = 1

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "AppSettings":
        """Tolerant loader: unknown keys ignored, bad values fall back."""
        data = dict(data or {})
        s = cls()

        def _str(key: str, default: str) -> str:
            value = data.get(key, default)
            return value if isinstance(value, str) else default

        def _float(key: str, default: float) -> float:
            try:
                return float(data.get(key, default))
            except (TypeError, ValueError):
                return default

        def _int(key: str, default: int) -> int:
            try:
                return int(data.get(key, default))
            except (TypeError, ValueError):
                return default

        def _bool(key: str, default: bool) -> bool:
            value = data.get(key, default)
            return bool(value) if isinstance(value, bool) else default

        def _dict(key: str) -> dict:
            value = data.get(key, {})
            return dict(value) if isinstance(value, dict) else {}

        s.active_provider = _str("active_provider", s.active_provider)
        s.models = {str(k): str(v) for k, v in _dict("models").items()}
        s.base_urls = {str(k): str(v) for k, v in _dict("base_urls").items()}
        s.shortcut = _str("shortcut", s.shortcut)
        s.dictation_shortcut = _str("dictation_shortcut", s.dictation_shortcut)
        s.whisper_model = _str("whisper_model", s.whisper_model)
        s.timeout = _float("timeout", s.timeout)
        s.max_tokens = _int("max_tokens", s.max_tokens)
        s.temperature_enabled = _bool("temperature_enabled", s.temperature_enabled)
        s.temperature = _float("temperature", s.temperature)
        s.model_strategy = _str("model_strategy", s.model_strategy)
        s.max_input_chars = _int("max_input_chars", s.max_input_chars)
        s.history_enabled = _bool("history_enabled", s.history_enabled)
        s.history_max = _int("history_max", s.history_max)
        s.price_overrides = _dict("price_overrides")
        s.version = _int("version", s.version)
        s.normalize()
        return s

    # ------------------------------------------------------------------
    def normalize(self) -> None:
        """Clamp values into supported ranges."""
        if self.active_provider not in SUPPORTED_PROVIDERS:
            self.active_provider = "openai"
        if self.model_strategy not in MODEL_STRATEGIES:
            self.model_strategy = "universal"
        self.timeout = min(max(self.timeout, 1.0), 300.0)
        self.max_tokens = min(max(self.max_tokens, 0), 32768)
        self.temperature = min(max(self.temperature, 0.0), 2.0)
        self.max_input_chars = min(max(self.max_input_chars, 200), 200_000)
        self.history_max = min(max(self.history_max, 0), 1000)
        self.shortcut = self.shortcut.strip() or DEFAULT_SHORTCUT
        self.dictation_shortcut = (
            self.dictation_shortcut.strip() or DEFAULT_DICTATION_SHORTCUT
        )
        # Whitespace-only becomes "" = auto; never fall back to another
        # provider's model ID here (resolution happens at request time).
        self.whisper_model = self.whisper_model.strip()

    def validate(self) -> list[str]:
        """Return human-readable warnings for values that look wrong."""
        warnings: list[str] = []
        if self.active_provider not in SUPPORTED_PROVIDERS:
            warnings.append(f"Unknown provider '{self.active_provider}'.")
        if not (1.0 <= self.timeout <= 300.0):
            warnings.append("Timeout should be between 1 and 300 seconds.")
        if not (0 <= self.max_tokens <= 32768):
            warnings.append("Maximum output length should be 0–32768 tokens.")
        if not (0.0 <= self.temperature <= 2.0):
            warnings.append("Temperature should be between 0.0 and 2.0.")
        if not (200 <= self.max_input_chars <= 200_000):
            warnings.append("Maximum input length should be 200–200,000 characters.")
        if self.model_strategy not in MODEL_STRATEGIES:
            warnings.append(
                f"Unknown model strategy '{self.model_strategy}' "
                f"(choose from {', '.join(MODEL_STRATEGIES)})."
            )
        return warnings
