"""Atomic JSON persistence for application settings (never secrets)."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from core.config import AppSettings, data_dir

#: Keys that must never appear in a persisted settings file.
_FORBIDDEN_KEYS = {"api_key", "apikey", "secret", "password", "access_token", "auth_token"}


class SettingsStore:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else Path(data_dir()) / "settings.json"

    def load(self) -> AppSettings:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return AppSettings()
        except OSError:
            return AppSettings()
        try:
            data = json.loads(raw)
        except ValueError:
            # Corrupt file: keep a backup for inspection, start fresh.
            try:
                shutil.copyfile(self.path, str(self.path) + ".bak")
            except OSError:
                pass
            return AppSettings()
        if not isinstance(data, dict):
            return AppSettings()
        return AppSettings.from_dict(data)

    def save(self, settings: AppSettings) -> None:
        settings.normalize()
        data = settings.to_dict()
        lowered = {key.lower() for key in data}
        leaked = lowered & _FORBIDDEN_KEYS
        if leaked:  # safety net — secrets never touch disk
            raise ValueError(
                f"Refusing to persist secret-like keys in settings: {sorted(leaked)}"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)
