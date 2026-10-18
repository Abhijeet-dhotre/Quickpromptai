"""Configuration validation tests."""
import os

from core.config import AppSettings, DEFAULT_SHORTCUT, SUPPORTED_PROVIDERS, data_dir


def test_data_dir_honors_env(tmp_path, monkeypatch):
    monkeypatch.setenv("QUICKPROMPT_DATA_DIR", str(tmp_path / "custom"))
    result = data_dir()
    assert result == str(tmp_path / "custom")
    assert os.path.isdir(result)


def test_supported_providers():
    assert set(SUPPORTED_PROVIDERS) == {"openai", "anthropic", "gemini", "groq",
                                        "openai_compat"}


def test_defaults_are_sane():
    settings = AppSettings()
    assert settings.validate() == []
    assert settings.shortcut == DEFAULT_SHORTCUT
    assert 1.0 <= settings.timeout <= 300.0
    assert 0 <= settings.max_tokens <= 32768


def test_normalize_fixes_shortcut_whitespace():
    settings = AppSettings(shortcut="   ")
    settings.normalize()
    assert settings.shortcut == DEFAULT_SHORTCUT


def test_settings_dict_roundtrip_is_key_stable():
    data = AppSettings().to_dict()
    assert "models" in data and "base_urls" in data and "shortcut" in data
    assert "api_key" not in data
