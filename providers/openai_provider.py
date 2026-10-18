"""OpenAI and OpenAI-compatible (chat/completions) provider adapters."""
from __future__ import annotations

from core.errors import InvalidResponseError, ProviderRequestError
from core.models import UsageInfo

from .base import HttpRequest, ProviderAdapter, ProviderCapabilities


class OpenAIStyleAdapter(ProviderAdapter):
    """Shared logic for OpenAI's /chat/completions schema.

    Used by the official OpenAI endpoint and by any self-hosted or third-party
    endpoint that speaks the OpenAI chat/completions protocol.
    """

    id = "openai_compat"
    display_name = "OpenAI-compatible"
    capabilities = ProviderCapabilities(
        allows_custom_base_url=True,
        requires_base_url=True,
        supports_audio_transcription=True,
        default_whisper_model="whisper-1",
        default_base_url="",
        default_model="",
        default_models=(),
        max_output_hint=8192,
    )

    def __init__(self, config, session=None):
        super().__init__(config, session)
        # Newer OpenAI models reject "max_tokens"; retry once with
        # "max_completion_tokens" if the provider complains.
        self._use_max_completion_tokens = False

    def build_request(self, system_prompt: str, user_prompt: str) -> HttpRequest:
        key = self.require_api_key()
        base = self.resolve_base_url()
        if not base:
            from core.errors import ConfigError

            raise ConfigError(
                "No API base URL is configured. Enter the endpoint URL "
                "(for example https://api.example.com/v1) in Settings → Provider."
            )
        messages = []
        if system_prompt and self.capabilities.supports_system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload: dict = {"model": self.config.model, "messages": messages}
        if self.config.max_tokens:
            field_name = (
                "max_completion_tokens" if self._use_max_completion_tokens else "max_tokens"
            )
            payload[field_name] = self.config.max_tokens
        if self.config.temperature is not None and self.capabilities.supports_temperature:
            payload["temperature"] = self.config.temperature

        return HttpRequest(
            method="POST",
            url=f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json_payload=payload,
        )

    def parse_response(self, data: dict) -> tuple[str, UsageInfo | None]:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise InvalidResponseError(
                f"{self.display_name} returned no choices in the response."
            )
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        if not isinstance(message, dict):
            raise InvalidResponseError(
                f"{self.display_name} returned an unexpected response structure."
            )
        text = message.get("content")
        text = text if isinstance(text, str) else ""

        usage = None
        raw_usage = data.get("usage")
        if isinstance(raw_usage, dict):
            usage = UsageInfo(
                input_tokens=int(raw_usage.get("prompt_tokens") or 0),
                output_tokens=int(raw_usage.get("completion_tokens") or 0),
                total_tokens=int(raw_usage.get("total_tokens") or 0),
            )
            if not usage.total_tokens:
                usage.total_tokens = usage.input_tokens + usage.output_tokens
        return text, usage

    def build_test_request(self) -> HttpRequest:
        key = self.require_api_key()
        base = self.resolve_base_url() or "https://api.openai.com/v1"
        return HttpRequest(
            method="GET",
            url=f"{base}/models",
            headers={"Authorization": f"Bearer {key}"},
        )

    def transcribe_audio(self, audio_wav: bytes, *, model: str | None = None) -> str:
        """Post a WAV recording to ``{base}/audio/transcriptions`` (Whisper).

        ``audio_wav`` must be 16 kHz mono PCM — the sample rate Whisper was
        trained on — so the service neither resamples nor loses fidelity. The
        transcription model is separate from the chat model; when the caller
        passes ``None``/empty ("auto"), the provider's default Whisper model
        is used, because model IDs are provider-specific.
        """
        key = self.require_api_key()
        base = self.resolve_base_url()
        if not base:
            from core.errors import ConfigError

            raise ConfigError(
                "No API base URL is configured. Enter the endpoint URL "
                "(for example https://api.example.com/v1) in Settings → Provider."
            )
        resolved_model = (
            model or "").strip() or self.capabilities.default_whisper_model or "whisper-1"
        request = HttpRequest(
            method="POST",
            url=f"{base}/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            # ``response_format=json`` guarantees a stable {"text": "..."}
            # body across OpenAI-compatible gateways.
            form_data={"model": resolved_model, "response_format": "json"},
            files={"file": ("quickprompt-dictation.wav", audio_wav, "audio/wav")},
        )
        response = self._send(request)
        if response.status_code >= 400:
            # A missing model is the most common dictation misconfiguration
            # (e.g. an OpenAI-only ID on another provider) — name it clearly
            # instead of the generic "check model ID" message.
            if response.status_code == 404:
                from core.errors import ModelError

                raise ModelError(
                    f"{self.display_name} rejected the dictation model "
                    f"'{resolved_model}' (HTTP 404). It is not available on this "
                    "provider — clear the model in Settings → Provider "
                    "(Dictation (Whisper) model) to use the provider default, "
                    "or enter one this provider supports."
                )
            raise self.map_http_error(response.status_code, response.text)
        try:
            data = response.json()
        except ValueError:
            raise InvalidResponseError(
                f"{self.display_name} returned a non-JSON transcription "
                f"response (HTTP {response.status_code})."
            )
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str):
            raise InvalidResponseError(
                f"{self.display_name} returned an unexpected transcription "
                "response (no 'text' field)."
            )
        return text.strip()

    def enhance(self, system_prompt: str, user_prompt: str):
        try:
            return super().enhance(system_prompt, user_prompt)
        except ProviderRequestError as exc:
            detail = (exc.detail or "").lower()
            if (
                exc.status == 400
                and "max_tokens" in detail
                and self.config.max_tokens
                and not self._use_max_completion_tokens
            ):
                self._use_max_completion_tokens = True
                return super().enhance(system_prompt, user_prompt)
            raise

    def test_connection(self):
        result = super().test_connection()
        # Some gateways do not expose /models; the auth check already passed.
        return result


class OpenAIAdapter(OpenAIStyleAdapter):
    id = "openai"
    display_name = "OpenAI"
    capabilities = ProviderCapabilities(
        allows_custom_base_url=True,
        requires_base_url=False,
        supports_audio_transcription=True,
        default_whisper_model="whisper-1",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        default_models=(
            "gpt-4o-mini",
            "gpt-4o",
            "gpt-4.1-mini",
            "gpt-4.1",
            "gpt-4.1-nano",
            "o4-mini",
        ),
        max_output_hint=16384,
    )


class GroqAdapter(OpenAIStyleAdapter):
    """Groq Cloud — OpenAI-compatible chat/completions API.

    Endpoint: https://api.groq.com/openai/v1
    Model IDs verified against https://console.groq.com/docs/models.
    """

    id = "groq"
    display_name = "Groq"
    capabilities = ProviderCapabilities(
        allows_custom_base_url=True,
        requires_base_url=False,
        supports_audio_transcription=True,
        # Groq does NOT host OpenAI's "whisper-1" — it 404s. Its Whisper
        # models are whisper-large-v3 / whisper-large-v3-turbo (verified live
        # against /v1/models 2026-10-19); turbo is cheapest and fastest.
        default_whisper_model="whisper-large-v3-turbo",
        default_base_url="https://api.groq.com/openai/v1",
        default_model="openai/gpt-oss-120b",
        default_models=(
            # Available on standard developer keys (verified live 2026-10-18).
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.8-27b",
            "allam-2-7b",
            # Enterprise-tier on many accounts (404 on free developer keys):
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
        ),
        max_output_hint=32768,
    )
