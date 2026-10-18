"""Provider registry: lookup and construction of adapters."""
from __future__ import annotations

from core.errors import ConfigError
from core.models import ProviderConfig

from .anthropic_provider import AnthropicAdapter
from .base import ProviderAdapter
from .gemini_provider import GeminiAdapter
from .openai_provider import GroqAdapter, OpenAIAdapter, OpenAIStyleAdapter

_ADAPTER_CLASSES: tuple[type[ProviderAdapter], ...] = (
    OpenAIAdapter,
    AnthropicAdapter,
    GeminiAdapter,
    GroqAdapter,
    OpenAIStyleAdapter,  # id: openai_compat
)


def all_adapters() -> tuple[type[ProviderAdapter], ...]:
    return _ADAPTER_CLASSES


def adapter_ids() -> list[str]:
    return [cls.id for cls in _ADAPTER_CLASSES]


def get_adapter_class(provider_id: str) -> type[ProviderAdapter]:
    for cls in _ADAPTER_CLASSES:
        if cls.id == provider_id:
            return cls
    raise ConfigError(
        f"Unknown provider '{provider_id}'. Choose one of: "
        + ", ".join(adapter_ids())
    )


def create_adapter(
    provider_id: str,
    *,
    api_key: str | None,
    model: str,
    base_url: str | None = None,
    timeout: float = 30.0,
    max_tokens: int | None = None,
    temperature: float | None = None,
    price_overrides: dict | None = None,
    session=None,
) -> ProviderAdapter:
    cls = get_adapter_class(provider_id)
    config = ProviderConfig(
        provider_id=provider_id,
        api_key=api_key,
        model=model or cls.capabilities.default_model,
        base_url=(base_url or "").strip() or None,
        timeout=timeout,
        max_tokens=max_tokens or None,
        temperature=temperature,
        price_overrides=dict(price_overrides or {}),
    )
    return cls(config, session=session)


def create_adapter_from_settings(settings, api_key: str | None, session=None) -> ProviderAdapter:
    """Build the adapter for the app's current settings (no secrets stored)."""
    provider_id = settings.active_provider
    model = settings.models.get(provider_id, "")
    base_url = settings.base_urls.get(provider_id, "")
    return create_adapter(
        provider_id,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=settings.timeout,
        max_tokens=settings.max_tokens or None,
        temperature=settings.temperature if settings.temperature_enabled else None,
        price_overrides=settings.price_overrides,
        session=session,
    )
