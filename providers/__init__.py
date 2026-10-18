"""AI provider adapters.

Each provider implements its own authentication, request schema, response
parsing, and error mapping. Nothing is assumed to be shared between vendors.
"""
from __future__ import annotations

from .registry import (
    all_adapters,
    adapter_ids,
    create_adapter,
    create_adapter_from_settings,
    get_adapter_class,
)

__all__ = [
    "all_adapters",
    "adapter_ids",
    "create_adapter",
    "create_adapter_from_settings",
    "get_adapter_class",
]
