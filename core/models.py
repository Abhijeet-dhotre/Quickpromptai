"""Shared data models for QuickPrompt AI.

IMPORTANT: no model here ever stores an API key that gets persisted to disk.
``ProviderConfig.api_key`` exists only in memory for the lifetime of a request.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum


class EnhanceMode(str, Enum):
    QUICK_FIX = "quick_fix"
    PROMPT_ENHANCE = "prompt_enhance"
    ADVANCED_OPTIMIZE = "advanced_optimize"

    @property
    def label(self) -> str:
        return {
            EnhanceMode.QUICK_FIX: "Quick Fix",
            EnhanceMode.PROMPT_ENHANCE: "Prompt Enhance",
            EnhanceMode.ADVANCED_OPTIMIZE: "Advanced Optimize",
        }[self]


#: Optional model-specific strategies (best-effort formatting adaptation).
MODEL_STRATEGIES = ("universal", "chatgpt", "claude", "gemini")


@dataclass
class ProviderConfig:
    """Runtime-only configuration for one provider request."""

    provider_id: str = "openai"
    api_key: str | None = None
    model: str = ""
    base_url: str | None = None
    timeout: float = 30.0
    max_tokens: int | None = None      # None -> provider default
    temperature: float | None = None   # None -> omitted
    price_overrides: dict = field(default_factory=dict)  # cost estimation only


@dataclass
class UsageInfo:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None

    def summary(self) -> str:
        parts = [f"{self.total_tokens or (self.input_tokens + self.output_tokens)} tokens"]
        if self.estimated_cost_usd is not None:
            parts.append(f"≈ ${self.estimated_cost_usd:.4f}")
        return " · ".join(parts)


@dataclass
class EnhanceResult:
    text: str
    usage: UsageInfo | None = None
    provider_id: str = ""
    model: str = ""


@dataclass
class TestConnectionResult:
    ok: bool
    message: str


@dataclass(frozen=True)
class WindowInfo:
    """Identity of the foreground window at capture time."""

    hwnd: int = 0
    pid: int = 0
    title: str = ""
    process_name: str = ""


@dataclass
class CaptureResult:
    """A captured text selection and the window it came from."""

    text: str
    window: WindowInfo
    captured_at: float = field(default_factory=time.time)


@dataclass
class ReplaceResult:
    ok: bool
    verified: bool = False
    message: str = ""


@dataclass
class HistoryEntry:
    timestamp: float
    mode: str
    provider_id: str
    model: str
    original: str
    enhanced: str
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "HistoryEntry":
        return cls(
            timestamp=float(data.get("timestamp", 0.0)),
            mode=str(data.get("mode", "")),
            provider_id=str(data.get("provider_id", "")),
            model=str(data.get("model", "")),
            original=str(data.get("original", "")),
            enhanced=str(data.get("enhanced", "")),
            input_tokens=int(data.get("input_tokens", 0) or 0),
            output_tokens=int(data.get("output_tokens", 0) or 0),
        )

    def preview(self, limit: int = 60) -> str:
        text = " ".join(self.original.split())
        return text if len(text) <= limit else text[: limit - 1] + "…"
