"""Settings and history persistence tests."""
import json

import pytest

from core.config import AppSettings, DEFAULT_SHORTCUT
from core.models import HistoryEntry
from storage.history import HistoryStore
from storage.settings import SettingsStore


# ======================================================================
# SettingsStore
# ======================================================================
def test_missing_file_returns_defaults(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    settings = store.load()
    assert settings.active_provider == "openai"
    assert settings.shortcut == DEFAULT_SHORTCUT
    assert settings.history_enabled is True


def test_roundtrip(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    settings = AppSettings(
        active_provider="anthropic",
        models={"anthropic": "claude-3-7-sonnet-latest"},
        base_urls={"openai_compat": "https://x.example/v1"},
        shortcut="Ctrl+Alt+Q",
        timeout=45.0,
        max_tokens=4096,
        temperature_enabled=True,
        temperature=0.4,
        model_strategy="claude",
        history_enabled=False,
        history_max=25,
    )
    store.save(settings)
    loaded = store.load()
    assert loaded.active_provider == "anthropic"
    assert loaded.models["anthropic"] == "claude-3-7-sonnet-latest"
    assert loaded.base_urls["openai_compat"] == "https://x.example/v1"
    assert loaded.shortcut == "Ctrl+Alt+Q"
    assert loaded.timeout == 45.0
    assert loaded.max_tokens == 4096
    assert loaded.temperature_enabled is True
    assert loaded.temperature == 0.4
    assert loaded.model_strategy == "claude"
    assert loaded.history_enabled is False
    assert loaded.history_max == 25


def test_no_secrets_written_to_disk(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save(AppSettings())
    raw = (tmp_path / "settings.json").read_text(encoding="utf-8").lower()
    for needle in ("api_key", "apikey", "password", "secret"):
        assert needle not in raw


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not valid json", encoding="utf-8")
    settings = SettingsStore(path).load()
    assert settings.active_provider == "openai"
    assert (tmp_path / "settings.json.bak").exists()  # corrupt copy preserved


def test_save_refuses_secret_like_payload(tmp_path):
    class Sneaky:
        def normalize(self):
            pass

        def to_dict(self):
            return {"api_key": "sk-leak"}

    store = SettingsStore(tmp_path / "settings.json")
    with pytest.raises(ValueError):
        store.save(Sneaky())
    assert not (tmp_path / "settings.json").exists()


def test_from_dict_tolerates_garbage():
    settings = AppSettings.from_dict({
        "active_provider": "weird-provider",
        "timeout": "not-a-number",
        "max_tokens": None,
        "shortcut": 12345,
        "history_enabled": "yes",
        "model_strategy": "chaos",
        "unknown_key": {"nested": True},
        "models": {"openai": "gpt-4o", "bad": None},
    })
    assert settings.active_provider == "openai"      # invalid -> default
    assert settings.timeout == 30.0                  # invalid -> default
    assert settings.max_tokens == 2048
    assert settings.shortcut == DEFAULT_SHORTCUT
    assert settings.history_enabled is True         # non-bool values fall back to default
    assert settings.model_strategy == "universal"
    assert settings.models["openai"] == "gpt-4o"


def test_normalize_clamps_ranges():
    settings = AppSettings(timeout=9999, max_tokens=10**9, temperature=9.9,
                           max_input_chars=5, history_max=-3)
    settings.normalize()
    assert settings.timeout == 300.0
    assert settings.max_tokens == 32768
    assert settings.temperature == 2.0
    assert settings.max_input_chars == 200
    assert settings.history_max == 0


def test_validate_flags():
    settings = AppSettings(timeout=0.0, temperature=5.0, model_strategy="nope")
    warnings = settings.validate()
    assert len(warnings) >= 3


# ======================================================================
# HistoryStore
# ======================================================================
def entry(i):
    return HistoryEntry(timestamp=float(i), mode="quick_fix", provider_id="openai",
                        model="m", original=f"orig {i}", enhanced=f"enh {i}")


def test_history_add_and_order(tmp_path):
    store = HistoryStore(tmp_path / "history.json", max_entries=10)
    for i in range(3):
        store.add(entry(i))
    items = store.list()
    assert [e.original for e in items] == ["orig 2", "orig 1", "orig 0"]  # newest first


def test_history_cap_enforced(tmp_path):
    store = HistoryStore(tmp_path / "history.json", max_entries=5)
    for i in range(10):
        store.add(entry(i))
    items = store.list()
    assert len(items) == 5
    assert items[0].original == "orig 9"  # newest survive


def test_history_clear(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    store.add(entry(1))
    store.clear()
    assert store.list() == []
    assert not (tmp_path / "history.json").exists()


def test_history_zero_max_disables_storage(tmp_path):
    store = HistoryStore(tmp_path / "history.json", max_entries=0)
    store.add(entry(1))
    assert store.list() == []
    assert not (tmp_path / "history.json").exists()


def test_history_corrupt_file_treated_as_empty(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("garbage", encoding="utf-8")
    assert HistoryStore(path).list() == []


def test_history_skips_malformed_entries(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps([
        {"timestamp": 1.0, "mode": "x", "provider_id": "p", "model": "m",
         "original": "o", "enhanced": "e"},
        "not-a-dict",
        {"timestamp": "bad"},
    ]), encoding="utf-8")
    items = HistoryStore(path).list()
    assert len(items) == 1
    assert items[0].original == "o"
