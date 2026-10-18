"""Global hotkey registration with parsing, validation and clean teardown."""
from __future__ import annotations

import threading

from core.errors import ShortcutError

_MODIFIER_ALIASES = {
    "ctrl": "<ctrl>", "control": "<ctrl>",
    "shift": "<shift>",
    "alt": "<alt>",
    "win": "<cmd>", "cmd": "<cmd>", "super": "<cmd>", "meta": "<cmd>",
}

_KEY_ALIASES = {
    "space": "<space>", "tab": "<tab>", "enter": "<enter>", "return": "<enter>",
    "esc": "<esc>", "escape": "<esc>", "backspace": "<backspace>",
    "delete": "<delete>", "del": "<delete>", "insert": "<insert>",
    "home": "<home>", "end": "<end>",
    "pageup": "<page up>", "pgup": "<page up>",
    "pagedown": "<page down>", "pgdn": "<page down>",
    "up": "<up>", "down": "<down>", "left": "<left>", "right": "<right>",
    "pause": "<pause>", "printscreen": "<print screen>", "capslock": "<caps lock>",
}

_CANONICAL_MODIFIERS = {"<ctrl>": "Ctrl", "<shift>": "Shift", "<alt>": "Alt", "<cmd>": "Win"}
_MODIFIER_ORDER = ["<ctrl>", "<cmd>", "<alt>", "<shift>"]

_NAMED_LABELS = {
    "space": "Space", "tab": "Tab", "enter": "Enter", "esc": "Esc",
    "backspace": "Backspace", "delete": "Delete", "insert": "Insert",
    "home": "Home", "end": "End", "page up": "PgUp", "page down": "PgDn",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "pause": "Pause", "print screen": "PrtSc", "caps lock": "CapsLock",
}


def parse_combo(combo: str) -> tuple[str, str]:
    """Parse a shortcut string into ``(canonical_label, pynput_format)``.

    Examples: ``"Ctrl+Shift+E"`` -> ``("Ctrl+Shift+E", "<ctrl>+<shift>+e")``.
    Raises :class:`ShortcutError` for anything invalid.
    """
    raw = (combo or "").strip()
    if not raw:
        raise ShortcutError("Enter a keyboard shortcut, for example Ctrl+Shift+E.")

    tokens = [t.strip().lower() for t in raw.split("+") if t.strip()]
    if len(tokens) < 2:
        raise ShortcutError(
            f"'{combo}' is not a valid shortcut — include at least one modifier "
            "key, for example Ctrl+Shift+E."
        )

    modifiers: list[str] = []
    keys: list[str] = []
    for token in tokens:
        if token in _MODIFIER_ALIASES:
            mod = _MODIFIER_ALIASES[token]
            if mod not in modifiers:
                modifiers.append(mod)
        else:
            keys.append(token)

    if not modifiers:
        raise ShortcutError(
            "The shortcut needs at least one modifier key (Ctrl, Shift, Alt or Win)."
        )
    if len(keys) != 1:
        raise ShortcutError(
            "The shortcut must contain exactly one non-modifier key "
            "(for example Ctrl+Shift+E)."
        )

    key = tokens and keys[0]
    if len(key) == 1 and key.isalnum():
        pynput_key = key
        label = key.upper()
    elif key in _KEY_ALIASES:
        pynput_key = _KEY_ALIASES[key]
        inner = pynput_key.strip("<>")
        label = _NAMED_LABELS.get(inner, inner.capitalize())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        pynput_key = f"<f{int(key[1:])}>"
        label = f"F{int(key[1:])}"
    else:
        raise ShortcutError(
            f"'{key}' is not a supported key. Letters, digits, F1–F24 and "
            "common named keys (Space, Tab, Enter, Esc, Home, End, arrows…) "
            "are supported."
        )

    ordered = [m for m in _MODIFIER_ORDER if m in modifiers]
    pynput_format = "+".join(ordered + [pynput_key])
    canonical = "+".join([_CANONICAL_MODIFIERS[m] for m in ordered] + [label])
    return canonical, pynput_format


class GlobalHotkeyManager:
    """Registers exactly one global hotkey; safe to replace at runtime."""

    def __init__(self, callback):
        self._callback = callback
        self._lock = threading.Lock()
        self._listener = None
        self._combo: str | None = None
        self._format: str | None = None

    # ------------------------------------------------------------------
    @property
    def combo(self) -> str | None:
        return self._combo

    @property
    def is_registered(self) -> bool:
        return self._listener is not None

    def register(self, combo: str) -> str:
        """Register ``combo`` (replacing any previous hotkey).

        Returns the canonical label. Raises :class:`ShortcutError` when the
        combination cannot be registered (conflict/reserved key).
        """
        canonical, pynput_format = parse_combo(combo)
        with self._lock:
            self._unregister_locked()
            try:
                from pynput import keyboard

                listener = keyboard.GlobalHotKeys({pynput_format: self._on_hotkey})
                listener.start()
                # ``start`` only creates the listener thread.  Do not report a
                # shortcut as usable until pynput has installed its Windows hook;
                # otherwise a shortcut pressed immediately after a cold launch
                # can be missed.
                listener.wait()
            except Exception as exc:
                self._unregister_locked()
                raise ShortcutError(
                    f"The shortcut {canonical} could not be registered — it is "
                    "probably reserved by Windows or another application. "
                    "Choose a different combination."
                ) from exc
            self._listener = listener
            self._combo = canonical
            self._format = pynput_format
        return canonical

    def unregister(self) -> None:
        with self._lock:
            self._unregister_locked()

    # ------------------------------------------------------------------
    def _on_hotkey(self) -> None:
        try:
            self._callback()
        except Exception:
            pass  # a callback error must never kill the listener thread

    def _unregister_locked(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._combo = None
        self._format = None
