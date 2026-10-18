"""Local enhancement history with enable/disable and clear controls.

History stores only the original text, the enhanced result, and usage
metadata — never API keys or provider credentials.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from core.config import data_dir
from core.models import HistoryEntry


class HistoryStore:
    def __init__(self, path: str | os.PathLike | None = None, max_entries: int = 100):
        self.path = Path(path) if path else Path(data_dir()) / "history.json"
        self.max_entries = max(0, int(max_entries))
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def _load(self) -> list[HistoryEntry]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        entries: list[HistoryEntry] = []
        for item in data:
            if isinstance(item, dict):
                try:
                    entries.append(HistoryEntry.from_dict(item))
                except (TypeError, ValueError):
                    continue
        return entries

    def _save(self, entries: list[HistoryEntry]) -> None:
        if self.max_entries == 0:
            self.clear()
            return
        entries = entries[-self.max_entries:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps([e.to_dict() for e in entries], indent=2), encoding="utf-8"
        )
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    def add(self, entry: HistoryEntry) -> None:
        with self._lock:
            if self.max_entries == 0:
                return
            entries = self._load()
            entries.append(entry)
            self._save(entries)

    def list(self, limit: int | None = None) -> list[HistoryEntry]:
        with self._lock:
            entries = self._load()
        entries.reverse()  # newest first
        return entries[:limit] if limit else entries

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                self.path.write_text("[]", encoding="utf-8")

    def set_max(self, max_entries: int) -> None:
        with self._lock:
            self.max_entries = max(0, int(max_entries))
            if self.max_entries:
                self._save(self._load())
            else:
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    pass
