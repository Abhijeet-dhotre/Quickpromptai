"""Best-effort cost estimation from token usage.

Prices change often and differ per account/region, so the built-in table is a
rough default that users can override via ``price_overrides`` in
settings.json::

    "price_overrides": {
        "my-model": {"input": 0.15, "output": 0.60}   # USD per 1M tokens
    }

When no price is known, cost is reported as unavailable — never guessed.
"""
from __future__ import annotations

from .models import UsageInfo

# USD per 1M tokens: {"model_prefix": {"input": x, "output": y}}
# Matched by longest prefix. Approximate public list prices.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "o4-mini": {"input": 1.10, "output": 4.40},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
    "claude-3-7-sonnet": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-haiku-4": {"input": 0.80, "output": 4.00},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    # Groq (official list prices where published)
    "openai/gpt-oss-120b": {"input": 0.15, "output": 0.60},
    "openai/gpt-oss-20b": {"input": 0.075, "output": 0.30},
    "qwen/qwen3.8-27b": {"input": 0.80, "output": 4.00},
}


def _lookup_price(model: str, table: dict[str, dict[str, float]]) -> dict[str, float] | None:
    if not model:
        return None
    model_l = model.lower()
    best: dict[str, float] | None = None
    best_len = 0
    for prefix, price in table.items():
        if model_l.startswith(prefix.lower()) and len(prefix) > best_len:
            best, best_len = price, len(prefix)
    return best


def estimate_cost(model: str, usage: UsageInfo | None,
                  overrides: dict | None = None) -> float | None:
    """Estimated USD cost, or None when no price info is available."""
    if usage is None or not (usage.input_tokens or usage.output_tokens):
        return None
    table = dict(DEFAULT_PRICES)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and "input" in value and "output" in value:
            try:
                table[str(key)] = {
                    "input": float(value["input"]),
                    "output": float(value["output"]),
                }
            except (TypeError, ValueError):
                continue
    price = _lookup_price(model, table)
    if not price:
        return None
    cost = (usage.input_tokens * price["input"] + usage.output_tokens * price["output"]) / 1_000_000
    return round(cost, 6)
