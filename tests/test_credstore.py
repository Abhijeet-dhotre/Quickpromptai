"""Credential storage tests: Credential Manager round-trip + DPAPI vault."""
import sys
import uuid

import pytest

from security.credstore import (
    DpapiVault,
    backend_label,
    delete_secret,
    load_secret,
    save_secret,
)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows credential store")
def test_keyring_roundtrip_and_cleanup():
    name = f"test-{uuid.uuid4().hex}"
    try:
        assert load_secret(name) is None
        save_secret(name, "s3cret-value-42")
        assert load_secret(name) == "s3cret-value-42"
        # overwrite
        save_secret(name, "updated-value")
        assert load_secret(name) == "updated-value"
        assert delete_secret(name) is True
        assert load_secret(name) is None
        assert delete_secret(name) is False  # already gone
    finally:
        try:
            delete_secret(name)
        except Exception:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
def test_dpapi_vault_roundtrip(tmp_path):
    path = tmp_path / "secrets.dpapi"
    vault = DpapiVault(path)
    assert vault.get("openai") is None
    vault.set("openai", "sk-key-123")
    vault.set("gemini", "g-key-456")

    # Fresh instance reads the encrypted file back.
    vault2 = DpapiVault(path)
    assert vault2.get("openai") == "sk-key-123"
    assert vault2.get("gemini") == "g-key-456"

    assert vault2.delete("openai") is True
    assert vault2.get("openai") is None
    assert vault2.get("gemini") == "g-key-456"  # other entries untouched

    # File must not contain plaintext.
    assert b"sk-key-123" not in path.read_bytes()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
def test_dpapi_vault_tolerates_corrupt_file(tmp_path):
    path = tmp_path / "secrets.dpapi"
    path.write_bytes(b"!!! not base64 !!!")
    vault = DpapiVault(path)
    assert vault.get("anything") is None  # corrupt -> empty, no crash


def test_backend_label_is_informative():
    label = backend_label()
    assert isinstance(label, str) and label
    if sys.platform == "win32":
        assert label != "unavailable"
