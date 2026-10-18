"""Google Gemini generateContent API adapter."""
from __future__ import annotations

from core.errors import InvalidResponseError
from core.models import UsageInfo

from .base import HttpRequest, ProviderAdapter, ProviderCapabilities


class GeminiAdapter(ProviderAdapter):
    id = "gemini"
    display_name = "Google Gemini"
    capabilities = ProviderCapabilities(
        allows_custom_base_url=True,
        requires_base_url=False,
        default_base_url="https://generativelanguage.googleapis.com",
        default_model="gemini-2.0-flash",
        default_models=(
            "gemini-2.0-flash",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-1.5-flash",
        ),
        max_output_hint=8192,
    )

    def build_request(self, system_prompt: str, user_prompt: str) -> HttpRequest:
        key = self.require_api_key()
        base = self.resolve_base_url()
        model = self.config.model or self.capabilities.default_model
        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        }
        if system_prompt and self.capabilities.supports_system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        generation: dict = {}
        if self.config.max_tokens:
            generation["maxOutputTokens"] = self.config.max_tokens
        if self.config.temperature is not None and self.capabilities.supports_temperature:
            generation["temperature"] = self.config.temperature
        if generation:
            payload["generationConfig"] = generation
        return HttpRequest(
            method="POST",
            url=f"{base}/v1beta/models/{model}:generateContent",
            # Header (not query param) so the key never appears in URLs/logs.
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json_payload=payload,
        )

    def parse_response(self, data: dict) -> tuple[str, UsageInfo | None]:
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            # Safety blocks and empty responses land here.
            feedback = data.get("promptFeedback") or {}
            reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
            extra = f" (blocked: {reason})" if reason else ""
            raise InvalidResponseError(
                f"{self.display_name} returned no candidates{extra}."
            )
        first = candidates[0]
        content = first.get("content") if isinstance(first, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise InvalidResponseError(
                f"{self.display_name} returned an unexpected response structure."
            )
        text = "".join(
            part.get("text", "") for part in parts if isinstance(part, dict)
        )

        usage = None
        raw = data.get("usageMetadata")
        if isinstance(raw, dict):
            input_tokens = int(raw.get("promptTokenCount") or 0)
            output_tokens = int(raw.get("candidatesTokenCount") or 0)
            total = int(raw.get("totalTokenCount") or 0)
            usage = UsageInfo(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total or (input_tokens + output_tokens),
            )
        return text, usage

    def build_test_request(self) -> HttpRequest:
        key = self.require_api_key()
        base = self.resolve_base_url()
        return HttpRequest(
            method="GET",
            url=f"{base}/v1beta/models",
            headers={"x-goog-api-key": key},
        )
