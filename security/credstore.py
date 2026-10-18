"""Secure credential storage for API keys.

Primary backend: the ``keyring`` library, which on Windows stores secrets in
**Windows Credential Manager** (a CredWrite-managed vault).

Fallback backend: a DPAPI-encrypted file inside the app data directory.
DPAPI binds the encryption to the current Windows user account.

Secrets are never written to settings.json, logs, or the executable.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from pathlib import Path

from core.config import data_dir
from core.errors import SecurityError

SERVICE_NAME = "QuickPromptAI"


# ----------------------------------------------------------------------
# DPAPI primitives (fallback vault)
# ----------------------------------------------------------------------
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]


def _dpapi_transform(data: bytes, protect: bool) -> bytes:
    if sys.platform != "win32":  # pragma: no cover - Windows app
        raise SecurityError("DPAPI is only available on Windows.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.c_wchar_p,
        ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = ctypes.c_int
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    in_buffer = ctypes.create_string_buffer(data, len(data))
    in_blob = _DATA_BLOB(len(data), ctypes.addressof(in_buffer))
    out_blob = _DATA_BLOB()
    function = (
        crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    )
    ok = function(
        ctypes.byref(in_blob),
        "QuickPromptAI" if protect else None,
        None,
        None,
        None,
        0x1 if protect else 0x0,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecurityError(
            "Windows DPAPI refused the operation "
            f"(error {ctypes.GetLastError()})."
        )
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


class DpapiVault:
    """Encrypted-file vault used when Credential Manager is unavailable."""

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else Path(data_dir()) / "secrets.dpapi"

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            raw = base64.b64decode(self.path.read_bytes(), validate=True)
            data = json.loads(_dpapi_transform(raw, protect=False).decode("utf-8"))
            return dict(data) if isinstance(data, dict) else {}
        except SecurityError:
            raise
        except Exception:
            return {}

    def _save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = _dpapi_transform(
            json.dumps(data).encode("utf-8"), protect=True
        )
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(base64.b64encode(raw))
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:  # pragma: no cover - Windows ignores this
            pass

    def get(self, name: str) -> str | None:
        return self._load().get(name)

    def set(self, name: str, value: str) -> None:
        data = self._load()
        data[name] = value
        self._save(data)

    def delete(self, name: str) -> bool:
        data = self._load()
        if name in data:
            del data[name]
            self._save(data)
            return True
        return False


# ----------------------------------------------------------------------
# Public API (keyring first, DPAPI vault second)
# ----------------------------------------------------------------------
def _keyring_module():
    try:
        import keyring  # noqa: PLC0415 - lazy import keeps startup fast

        return keyring
    except Exception:  # pragma: no cover - depends on environment
        return None


def backend_label() -> str:
    """Human-readable description of the active credential backend."""
    keyring = _keyring_module()
    if keyring is not None:
        try:
            backend = keyring.get_keyring()
            name = backend.__class__.__name__.lower()
            priority = getattr(backend, "priority", 0) or 0
            if "windows" in name and priority > 0:
                return "Windows Credential Manager"
            if "fail" not in name and "null" not in name and priority > 0:
                return f"keyring ({backend.__class__.__name__})"
        except Exception:  # pragma: no cover
            pass
    if sys.platform == "win32":
        return "DPAPI-encrypted file"
    return "unavailable"


def save_secret(name: str, value: str) -> None:
    keyring = _keyring_module()
    if keyring is not None:
        try:
            keyring.set_password(SERVICE_NAME, name, value)
            return
        except Exception:
            pass
    if sys.platform == "win32":
        DpapiVault().set(name, value)
        return
    raise SecurityError(
        "No secure credential store is available on this system."
    )


def load_secret(name: str) -> str | None:
    keyring = _keyring_module()
    if keyring is not None:
        try:
            value = keyring.get_password(SERVICE_NAME, name)
            if value:
                return value
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            return DpapiVault().get(name)
        except SecurityError:
            raise
        except Exception:
            return None
    return None


def delete_secret(name: str) -> bool:
    """Remove a stored secret from every backend. Returns True if removed."""
    removed = False
    keyring = _keyring_module()
    if keyring is not None:
        try:
            if keyring.get_password(SERVICE_NAME, name):
                keyring.delete_password(SERVICE_NAME, name)
                removed = True
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            if DpapiVault().delete(name):
                removed = True
        except SecurityError:
            raise
        except Exception:
            pass
    return removed
