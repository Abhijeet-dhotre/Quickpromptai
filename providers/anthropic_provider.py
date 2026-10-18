"""Anthropic Claude Messages API adapter."""
from __future__ import annotations

from core.errors import InvalidResponseError
from core.models import UsageInfo

from .base import HttpRequest, ProviderAdapter, ProviderCapabilities


class AnthropicAdapter(ProviderAdapter):
    id = "anthropic"
    display_name = "Anthropic"
    capabilities = ProviderCapabilities(
        allows_custom_base_url=True,
        requires_base_url=False,
        default_base_url="https://api.anthropic.com",
        default_model="claude-sonnet-4-20250514",
        default_models=(
            "claude-sonnet-4-20250514",
            "claude-3-7-sonnet-latest",
            "claude-3-5-haiku-latest",
        ),
        max_output_hint=8192,
    )
    ANTHROPIC_VERSION = "2023-06-01"

    def _headers(self, key: str) -> dict:
        return {
            "x-api-key": key,
            "anthropic-version": self.ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def build_request(self, system_prompt: str, user_prompt: str) -> HttpRequest:
        key = self.require_api_key()
        base = self.resolve_base_url()
        payload: dict = {
            "model": self.config.model,
            # max_tokens is required by the Anthropic Messages API.
            "max_tokens": self.config.max_tokens or 2048,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if system_prompt and self.capabilities.supports_system_prompt:
            payload["system"] = system_prompt
        if self.config.temperature is not None and self.capabilities.supports_temperature:
            payload["temperature"] = self.config.temperature
        return HttpRequest(
            method="POST",
            url=f"{base}/v1/messages",
            headers=self._headers(key),
            json_payload=payload,
        )

    def parse_response(self, data: dict) -> tuple[str, UsageInfo | None]:
        content = data.get("content")
        if not isinstance(content, list):
            raise InvalidResponseError(
                f"{self.display_name} returned an unexpected response structure."
            )
        text = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = None
        raw = data.get("usage")
        if isinstance(raw, dict):
            input_tokens = int(raw.get("input_tokens") or 0)
            output_tokens = int(raw.get("output_tokens") or 0)
            usage = UsageInfo(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            )
        return text, usage

    def build_test_request(self) -> HttpRequest:
        key = self.require_api_key()
        base = self.resolve_base_url()
        return HttpRequest(
            method="GET",
            url=f"{base}/v1/models",
            headers=self._headers(key),
        )

    def test_connection(self):
        from core.models import TestConnectionResult

        try:
            return super().test_connection()
        except Exception as exc:
            # Older gateways may not implement /v1/models — fall back to a
            # minimal 1-token message to prove the key works.
            from core.errors import ModelError

            if not isinstance(exc, ModelError):
                raise
            key = self.require_api_key()
            base = self.resolve_base_url()
            request = HttpRequest(
                method="POST",
                url=f"{base}/v1/messages",
                headers=self._headers(key),
                json_payload={
                    "model": self.config.model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                },
            )
            response = self._send(request)
            if response.status_code >= 400:
                raise self.map_http_error(response.status_code, response.text)
            model = self.config.model or "(default)"
            return TestConnectionResult(
                True, f"Connected to {self.display_name} — model: {model}."
            )
