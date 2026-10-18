"""Windows integration package."""
from .clipboard import ClipboardSnapshot, get_sequence, get_text, restore, set_text, snapshot
from .window_info import get_foreground_window, same_process

__all__ = [
    "ClipboardSnapshot", "get_sequence", "get_text", "restore", "set_text",
    "snapshot", "get_foreground_window", "same_process",
]
