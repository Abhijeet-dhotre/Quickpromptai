"""Provider adapter interface and shared HTTP/error handling."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import requests

from core.errors import (
    AuthenticationError,
    ConfigError,
    InvalidResponseError,
    ModelError,
    MissingApiKeyError,
    NetworkError,
    ProviderRequestError,
    ProviderTimeoutError,
    QuotaError,
    QuickPromptError,
    RateLimitError,
)
from core.models import EnhanceResult, ProviderConfig, TestConnectionResult, UsageInfo
from core.pricing import estimate_cost


@dataclass
class ProviderCapabilities:
    requires_api_key: bool = True
    supports_temperature: bool = True
    supports_system_prompt: bool = True
    allows_custom_base_url: bool = True
    requires_base_url: bool = False
    #: Whether the provider exposes an OpenAI-style ``/audio/transcriptions``
    #: (Whisper) endpoint used for dictation.
    supports_audio_transcription: bool = False
    #: Default transcription model for dictation when the user leaves the
    #: setting on "auto". Model IDs are provider-specific (Groq's Whisper
    #: models are not called "whisper-1").
    default_whisper_model: str = ""
    default_base_url: str = ""
    default_model: str = ""
    default_models: tuple[str, ...] = ()
    api_key_label: str = "API key"
    max_output_hint: int = 8192


@dataclass
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    json_payload: dict | None = None
    # Multipart uploads (dictation). ``form_data`` maps field name -> value and
    # ``files`` maps field name -> (filename, bytes, content_type). They are
    # mutually exclusive with ``json_payload``.
    form_data: dict[str, str] | None = None
    files: dict[str, tuple[str, bytes, str]] | None = None


def extract_error_message(body: str) -> str:
    """Best-effort extraction of a human-readable message from an error body."""
    if not body:
        return ""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return " ".join(body.split())[:200]
    if isinstance(data, dict):
        err = data.get("error", data)
        if isinstance(err, dict):
            for key in ("message", "detail", "error_description"):
                value = err.get(key)
                if isinstance(value, str) and value.strip():
                    return " ".join(value.split())[:300]
        if isinstance(err, str) and err.strip():
            return " ".join(err.split())[:300]
    return ""


class ProviderAdapter(ABC):
    """Base class every provider implements."""

    id: str = ""
    display_name: str = ""
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def __init__(self, config: ProviderConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()

    # ------------------------------------------------------------------
    # Provider-specific parts
    # ------------------------------------------------------------------
    @abstractmethod
    def build_request(self, system_prompt: str, user_prompt: str) -> HttpRequest:
        """Build the HTTP request for an enhancement call."""

    @abstractmethod
    def parse_response(self, data: dict) -> tuple[str, UsageInfo | None]:
        """Extract the enhanced text and usage data from a response body."""

    @abstractmethod
    def build_test_request(self) -> HttpRequest:
        """Build a cheap request used by the 'Test Connection' button."""

    # ------------------------------------------------------------------
    # Shared behaviour
    # ------------------------------------------------------------------
    def resolve_base_url(self) -> str:
        base = (self.config.base_url or "").strip() or self.capabilities.default_base_url
        return base.rstrip("/")

    def require_api_key(self) -> str:
        key = (self.config.api_key or "").strip()
        if not key:
            raise MissingApiKeyError(
                f"No API key is configured for {self.display_name}. "
                "Open Settings → Provider and save your API key first."
            )
        return key

    def enhance(self, system_prompt: str, user_prompt: str) -> EnhanceResult:
        self.require_api_key()
        request = self.build_request(system_prompt, user_prompt)
        response = self._send(request)
        if response.status_code >= 400:
            raise self.map_http_error(response.status_code, response.text)
        try:
            data = response.json()
        except ValueError:
            raise InvalidResponseError(
                f"{self.display_name} returned a non-JSON response "
                f"(HTTP {response.status_code})."
            )
        if not isinstance(data, dict):
            raise InvalidResponseError(
                f"{self.display_name} returned an unexpected response structure."
            )
        text, usage = self.parse_response(data)
        text = (text or "").strip()
        if not text:
            raise InvalidResponseError(
                f"{self.display_name} returned an empty response. "
                "Try again or pick another model."
            )
        if usage is not None:
            usage.estimated_cost_usd = estimate_cost(
                self.config.model, usage, self.config.price_overrides
            )
        return EnhanceResult(
            text=text, usage=usage, provider_id=self.id, model=self.config.model
        )

    def test_connection(self) -> TestConnectionResult:
        self.require_api_key()
        request = self.build_test_request()
        response = self._send(request)
        if response.status_code >= 400:
            raise self.map_http_error(response.status_code, response.text)
        model = self.config.model or "(default)"
        return TestConnectionResult(
            True, f"Connected to {self.display_name} — model: {model}."
        )

    # ------------------------------------------------------------------
    # Speech-to-text (dictation)
    # ------------------------------------------------------------------
    def transcribe_audio(self, audio_wav: bytes, *, model: str | None = None) -> str:
        """Return the transcript of a WAV recording.

        Providers that do not expose a Whisper-style ``/audio/transcriptions``
        endpoint inherit this implementation and report a clear, actionable
        message instead of sending a request that is guaranteed to fail.
        """
        raise ConfigError(
            f"{self.display_name} does not offer a Whisper-compatible audio "
            "endpoint, so dictation is unavailable for this provider. "
            "Switch to OpenAI, Groq or an OpenAI-compatible endpoint to "
            "use speech-to-text."
        )

    def _send(self, request: HttpRequest) -> requests.Response:
        # Only forward the multipart fields when they are actually set, so the
        # simpler JSON-only call sites (and their lightweight test doubles)
        # keep the exact same shape as before.
        extra: dict = {}
        if request.form_data is not None:
            extra["data"] = request.form_data
        if request.files is not None:
            extra["files"] = request.files
        try:
            return self.session.request(
                request.method,
                request.url,
                headers=request.headers or None,
                json=request.json_payload,
                timeout=self.config.timeout,
                **extra,
            )
        except requests.Timeout:
            raise ProviderTimeoutError(
                f"{self.display_name} did not answer within "
                f"{self.config.timeout:g} s."
            )
        except requests.ConnectionError as exc:
            raise NetworkError(
                f"Could not reach {self.display_name}. Check your internet "
                f"connection ({exc.__class__.__name__})."
            )
        except requests.RequestException as exc:
            raise NetworkError(
                f"Network error while calling {self.display_name} "
                f"({exc.__class__.__name__})."
            )

    # ------------------------------------------------------------------
    # Error mapping (shared generic implementation)
    # ------------------------------------------------------------------
    def map_http_error(self, status: int, body: str) -> QuickPromptError:
        detail = extract_error_message(body)
        suffix = f" Provider said: {detail}" if detail else ""
        provider = self.display_name
        lower = detail.lower()

        if status == 401:
            return AuthenticationError(
                f"The {provider} API key was rejected (HTTP 401). "
                "Check the key in Settings → Provider." + suffix
            )
        if status == 402:
            return QuotaError(
                f"{provider} reports insufficient credits (HTTP 402)."
                + suffix
            )
        if status == 403:
            if any(word in lower for word in ("quota", "billing", "credit", "exceeded")):
                return QuotaError(
                    f"{provider} reports a quota or billing problem (HTTP 403)."
                    + suffix
                )
            return AuthenticationError(
                f"{provider} denied access (HTTP 403). The key may lack "
                "permission for this model." + suffix
            )
        if status == 404:
            return ModelError(
                f"{provider} could not find the model or endpoint (HTTP 404). "
                "Check the model ID"
                + (" and API base URL." if self.capabilities.allows_custom_base_url else ".")
                + suffix
            )
        if status == 429:
            if any(word in lower for word in ("quota", "insufficient", "billing")):
                return QuotaError(
                    f"{provider} reports an exhausted quota (HTTP 429)." + suffix
                )
            return RateLimitError(
                f"{provider} rate limit reached (HTTP 429). Wait a moment and retry."
                + suffix
            )
        if status in (400, 422):
            return ProviderRequestError(
                f"{provider} rejected the request (HTTP {status}). Check the "
                "model ID, temperature, and output-length settings." + suffix,
                detail=detail,
                status=status,
            )
        if status == 529:
            return NetworkError(
                f"{provider} is overloaded (HTTP 529). Try again shortly." + suffix
            )
        if status >= 500:
            return NetworkError(
                f"{provider} server error (HTTP {status}). Try again shortly."
                + suffix
            )
        return QuickPromptError(
            f"{provider} returned an unexpected error (HTTP {status})." + suffix
        )
