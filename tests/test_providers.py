"""Tests for provider adapters using mocked HTTP sessions."""
import json

import pytest
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
    RateLimitError,
)
from core.models import ProviderConfig
from providers.anthropic_provider import AnthropicAdapter
from providers.gemini_provider import GeminiAdapter
from providers.openai_provider import OpenAIAdapter, OpenAIStyleAdapter
from providers.registry import (
    adapter_ids,
    create_adapter,
    create_adapter_from_settings,
    get_adapter_class,
)
from core.config import AppSettings


class FakeResponse:
    def __init__(self, status_code=200, data=None, text=None):
        self.status_code = status_code
        self._data = data
        self.text = text if text is not None else json.dumps(data or {})

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class FakeSession:
    def __init__(self, responses=None, exc=None):
        self.responses = list(responses or [FakeResponse()])
        self.exc = exc
        self.calls = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append({
            "method": method, "url": url, "headers": headers or {},
            "json": json, "timeout": timeout,
        })
        if self.exc is not None:
            raise self.exc
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def openai_cfg(**kwargs):
    base = dict(provider_id="openai", api_key="sk-test-123", model="gpt-4o-mini")
    base.update(kwargs)
    return ProviderConfig(**base)


OPENAI_OK = {
    "choices": [{"message": {"role": "assistant", "content": "ENHANCED PROMPT"}}],
    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
}


# ======================================================================
# OpenAI
# ======================================================================
def test_openai_registry_and_ids():
    assert adapter_ids() == ["openai", "anthropic", "gemini", "groq", "openai_compat"]
    assert get_adapter_class("openai") is OpenAIAdapter
    assert get_adapter_class("groq").display_name == "Groq"


def test_openai_unknown_provider():
    with pytest.raises(ConfigError):
        get_adapter_class("does-not-exist")


def test_openai_build_request():
    adapter = OpenAIAdapter(openai_cfg(max_tokens=1000, temperature=0.5))
    req = adapter.build_request("SYS", "USER")
    assert req.url == "https://api.openai.com/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer sk-test-123"
    assert req.json_payload["model"] == "gpt-4o-mini"
    assert req.json_payload["messages"][0] == {"role": "system", "content": "SYS"}
    assert req.json_payload["messages"][1] == {"role": "user", "content": "USER"}
    assert req.json_payload["max_tokens"] == 1000
    assert req.json_payload["temperature"] == 0.5


def test_openai_enhance_happy_path():
    session = FakeSession([FakeResponse(data=OPENAI_OK)])
    adapter = OpenAIAdapter(openai_cfg(), session=session)
    result = adapter.enhance("SYS", "USER")
    assert result.text == "ENHANCED PROMPT"
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 50
    assert result.usage.total_tokens == 150
    assert result.usage.estimated_cost_usd is not None  # gpt-4o-mini has a price
    assert result.provider_id == "openai"


def test_openai_temperature_omitted_when_none():
    adapter = OpenAIAdapter(openai_cfg(temperature=None))
    req = adapter.build_request("SYS", "USER")
    assert "temperature" not in req.json_payload
    assert "max_tokens" not in req.json_payload


def test_openai_missing_api_key():
    adapter = OpenAIAdapter(openai_cfg(api_key=None))
    with pytest.raises(MissingApiKeyError):
        adapter.enhance("SYS", "USER")


def test_openai_max_tokens_retry():
    """400 mentioning max_tokens -> retry once with max_completion_tokens."""
    bad = FakeResponse(status_code=400, data={
        "error": {"message": "Unsupported parameter: 'max_tokens' is not supported"}
    })
    session = FakeSession([bad, FakeResponse(data=OPENAI_OK)])
    adapter = OpenAIAdapter(openai_cfg(max_tokens=1000), session=session)
    result = adapter.enhance("SYS", "USER")
    assert result.text == "ENHANCED PROMPT"
    assert len(session.calls) == 2
    assert "max_tokens" in session.calls[0]["json"]
    assert "max_completion_tokens" in session.calls[1]["json"]


def test_openai_custom_base_url():
    adapter = OpenAIAdapter(openai_cfg(base_url="https://llm.example.com/v1/"))
    req = adapter.build_request("S", "U")
    assert req.url == "https://llm.example.com/v1/chat/completions"


# ======================================================================
# Error mapping (shared) — parametrized over status codes
# ======================================================================
@pytest.mark.parametrize("status,data,expected", [
    (401, {"error": {"message": "bad key"}}, AuthenticationError),
    (402, {"error": {"message": "pay up"}}, QuotaError),
    (403, {"error": {"message": "quota exceeded for org"}}, QuotaError),
    (403, {"error": {"message": "model not allowed"}}, AuthenticationError),
    (404, {"error": {"message": "no such model"}}, ModelError),
    (429, {"error": {"message": "slow down"}}, RateLimitError),
    (429, {"error": {"message": "insufficient_quota"}}, QuotaError),
    (500, {"error": {"message": "boom"}}, NetworkError),
    (503, {"error": {"message": "unavailable"}}, NetworkError),
    (529, {"error": {"message": "overloaded"}}, NetworkError),
    (400, {"error": {"message": "bad request"}}, ProviderRequestError),
])
def test_openai_error_mapping(status, data, expected):
    session = FakeSession([FakeResponse(status_code=status, data=data)])
    adapter = OpenAIAdapter(openai_cfg(), session=session)
    with pytest.raises(expected):
        adapter.enhance("SYS", "USER")


def test_openai_timeout():
    session = FakeSession(exc=requests.Timeout())
    adapter = OpenAIAdapter(openai_cfg(), session=session)
    with pytest.raises(ProviderTimeoutError) as excinfo:
        adapter.enhance("SYS", "USER")
    assert excinfo.value.retryable is True


def test_openai_connection_error():
    session = FakeSession(exc=requests.ConnectionError())
    adapter = OpenAIAdapter(openai_cfg(), session=session)
    with pytest.raises(NetworkError):
        adapter.enhance("SYS", "USER")


def test_openai_non_json_response():
    session = FakeSession([FakeResponse(data=None, text="<html>oops</html>")])
    adapter = OpenAIAdapter(openai_cfg(), session=session)
    with pytest.raises(InvalidResponseError):
        adapter.enhance("SYS", "USER")


def test_openai_empty_content():
    data = {"choices": [{"message": {"content": ""}}]}
    session = FakeSession([FakeResponse(data=data)])
    adapter = OpenAIAdapter(openai_cfg(), session=session)
    with pytest.raises(InvalidResponseError):
        adapter.enhance("SYS", "USER")


def test_openai_test_connection():
    session = FakeSession([FakeResponse(data={"data": []})])
    adapter = OpenAIAdapter(openai_cfg(), session=session)
    result = adapter.test_connection()
    assert result.ok is True
    assert session.calls[0]["method"] == "GET"
    assert session.calls[0]["url"].endswith("/models")


# ======================================================================
# Anthropic
# ======================================================================
ANTHROPIC_OK = {
    "content": [{"type": "text", "text": "CLAUDE ENHANCED"}],
    "usage": {"input_tokens": 40, "output_tokens": 25},
}


def anthropic_cfg(**kwargs):
    base = dict(provider_id="anthropic", api_key="sk-ant-test",
                model="claude-sonnet-4-20250514")
    base.update(kwargs)
    return ProviderConfig(**base)


def test_anthropic_build_request():
    adapter = AnthropicAdapter(anthropic_cfg(max_tokens=500, temperature=0.2))
    req = adapter.build_request("SYS", "USER")
    assert req.url == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "sk-ant-test"
    assert req.headers["anthropic-version"]
    payload = req.json_payload
    assert payload["system"] == "SYS"
    assert payload["messages"] == [{"role": "user", "content": "USER"}]
    assert payload["max_tokens"] == 500
    assert payload["temperature"] == 0.2


def test_anthropic_max_tokens_required_default():
    adapter = AnthropicAdapter(anthropic_cfg(max_tokens=None))
    payload = adapter.build_request("S", "U").json_payload
    assert payload["max_tokens"] == 2048  # API requires it


def test_anthropic_parse_response_and_usage():
    session = FakeSession([FakeResponse(data=ANTHROPIC_OK)])
    adapter = AnthropicAdapter(anthropic_cfg(), session=session)
    result = adapter.enhance("SYS", "USER")
    assert result.text == "CLAUDE ENHANCED"
    assert result.usage.input_tokens == 40
    assert result.usage.output_tokens == 25
    assert result.usage.total_tokens == 65


def test_anthropic_missing_key():
    with pytest.raises(MissingApiKeyError):
        AnthropicAdapter(anthropic_cfg(api_key="")).build_request("S", "U")


# ======================================================================
# Gemini
# ======================================================================
GEMINI_OK = {
    "candidates": [{"content": {"parts": [{"text": "GEMINI ENHANCED"}]}}],
    "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 20,
                      "totalTokenCount": 50},
}


def gemini_cfg(**kwargs):
    base = dict(provider_id="gemini", api_key="g-test-key", model="gemini-2.0-flash")
    base.update(kwargs)
    return ProviderConfig(**base)


def test_gemini_build_request_uses_header_not_url():
    adapter = GeminiAdapter(gemini_cfg(max_tokens=300))
    req = adapter.build_request("SYS", "USER")
    assert req.url == ("https://generativelanguage.googleapis.com"
                       "/v1beta/models/gemini-2.0-flash:generateContent")
    assert req.headers["x-goog-api-key"] == "g-test-key"
    assert "g-test-key" not in req.url  # key must never appear in the URL
    payload = req.json_payload
    assert payload["systemInstruction"] == {"parts": [{"text": "SYS"}]}
    assert payload["contents"][0]["parts"][0]["text"] == "USER"
    assert payload["generationConfig"]["maxOutputTokens"] == 300


def test_gemini_parse_response():
    session = FakeSession([FakeResponse(data=GEMINI_OK)])
    adapter = GeminiAdapter(gemini_cfg(), session=session)
    result = adapter.enhance("SYS", "USER")
    assert result.text == "GEMINI ENHANCED"
    assert result.usage.total_tokens == 50
    assert result.usage.input_tokens == 30


def test_gemini_blocked_prompt():
    data = {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}
    session = FakeSession([FakeResponse(data=data)])
    adapter = GeminiAdapter(gemini_cfg(), session=session)
    with pytest.raises(InvalidResponseError) as excinfo:
        adapter.enhance("SYS", "USER")
    assert "SAFETY" in str(excinfo.value)


# ======================================================================
# OpenAI-compatible
# ======================================================================
def test_openai_compat_requires_base_url():
    adapter = OpenAIStyleAdapter(ProviderConfig(
        provider_id="openai_compat", api_key="k", model="m", base_url=None))
    with pytest.raises(ConfigError):
        adapter.build_request("S", "U")


def test_openai_compat_custom_endpoint():
    cfg = ProviderConfig(provider_id="openai_compat", api_key="k", model="my-model",
                         base_url="https://gateway.corp.internal/v1")
    adapter = OpenAIStyleAdapter(cfg)
    req = adapter.build_request("S", "U")
    assert req.url == "https://gateway.corp.internal/v1/chat/completions"
    assert req.json_payload["model"] == "my-model"


# ======================================================================
# Groq (OpenAI-compatible schema, own endpoint & models)
# ======================================================================
def test_groq_build_request():
    from providers.openai_provider import GroqAdapter

    adapter = GroqAdapter(ProviderConfig(
        provider_id="groq", api_key="gsk-test", model="llama-3.3-70b-versatile",
        max_tokens=2048,
    ))
    req = adapter.build_request("SYS", "USER")
    assert req.url == "https://api.groq.com/openai/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer gsk-test"
    assert req.json_payload["model"] == "llama-3.3-70b-versatile"
    assert req.json_payload["max_tokens"] == 2048


def test_groq_happy_path_and_test_connection():
    from providers.openai_provider import GroqAdapter

    session = FakeSession([FakeResponse(data=OPENAI_OK)])
    adapter = GroqAdapter(ProviderConfig(
        provider_id="groq", api_key="gsk-test", model="llama-3.3-70b-versatile",
    ), session=session)
    result = adapter.enhance("SYS", "USER")
    assert result.text == "ENHANCED PROMPT"
    assert result.provider_id == "groq"

    session2 = FakeSession([FakeResponse(data={"data": []})])
    adapter2 = GroqAdapter(ProviderConfig(
        provider_id="groq", api_key="gsk-test", model="llama-3.3-70b-versatile",
    ), session=session2)
    assert adapter2.test_connection().ok is True
    assert session2.calls[0]["url"] == "https://api.groq.com/openai/v1/models"


def test_groq_in_settings_construction():
    settings = AppSettings(
        active_provider="groq",
        models={"groq": "openai/gpt-oss-120b"},
        base_urls={},
        timeout=20.0,
    )
    adapter = create_adapter_from_settings(settings, api_key="gsk-x")
    assert adapter.id == "groq"
    assert adapter.config.model == "openai/gpt-oss-120b"
    assert adapter.config.base_url is None  # default endpoint used


def test_groq_cost_estimate():
    from providers.openai_provider import GroqAdapter

    session = FakeSession([FakeResponse(data={
        "choices": [{"message": {"content": "x"}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 1000,
                  "total_tokens": 2000},
    })])
    adapter = GroqAdapter(ProviderConfig(
        provider_id="groq", api_key="gsk-test", model="openai/gpt-oss-120b",
    ), session=session)
    result = adapter.enhance("S", "U")
    # 1000 * 0.15 + 1000 * 0.60 = 0.75 / 1M-token... = $0.00075
    assert result.usage.estimated_cost_usd == pytest.approx(0.00075)


# ======================================================================
# Registry / settings-driven construction
# ======================================================================
def test_create_adapter_from_settings():
    settings = AppSettings(
        active_provider="gemini",
        models={"gemini": "gemini-2.5-pro"},
        base_urls={"gemini": ""},
        timeout=12.0,
        max_tokens=1000,
        temperature_enabled=True,
        temperature=0.3,
    )
    adapter = create_adapter_from_settings(settings, api_key="gk")
    assert isinstance(adapter, GeminiAdapter)
    assert adapter.config.model == "gemini-2.5-pro"
    assert adapter.config.timeout == 12.0
    assert adapter.config.max_tokens == 1000
    assert adapter.config.temperature == 0.3


def test_create_adapter_default_model_fallback():
    adapter = create_adapter("anthropic", api_key="k", model="")
    assert adapter.config.model == adapter.capabilities.default_model


def test_cost_estimation_applied_in_enhance():
    session = FakeSession([FakeResponse(data=OPENAI_OK)])
    adapter = OpenAIAdapter(openai_cfg(model="gpt-4o-mini"), session=session)
    result = adapter.enhance("S", "U")
    # 100 * 0.15 + 50 * 0.60 = 45 / 1M
    assert result.usage.estimated_cost_usd == pytest.approx(0.000045)


def test_cost_unknown_model_returns_none():
    session = FakeSession([FakeResponse(data={
        "choices": [{"message": {"content": "x"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    })])
    adapter = OpenAIAdapter(openai_cfg(model="totally-unknown-model"), session=session)
    result = adapter.enhance("S", "U")
    assert result.usage.estimated_cost_usd is None
