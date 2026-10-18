"""Token-elevation check: detects target apps running as administrator.

A non-elevated process cannot send keystrokes *or* window messages into an
elevated (UAC) window (UIPI). Knowing this lets the app explain the failure
precisely instead of showing a generic "no selection" message.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

_KERNEL32 = ctypes.windll.kernel32
_ADVAPI32 = ctypes.windll.advapi32

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_INTEGRITY_LEVEL = 25  # TokenInformationClass
_SECURITY_MANDATORY_MEDIUM_RID = 0x2000

_KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_KERNEL32.OpenProcess.restype = wintypes.HANDLE
_KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
_KERNEL32.CloseHandle.restype = wintypes.BOOL

_ADVAPI32.OpenProcessToken.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
]
_ADVAPI32.OpenProcessToken.restype = wintypes.BOOL
_ADVAPI32.GetTokenInformation.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
_ADVAPI32.GetTokenInformation.restype = wintypes.BOOL
_ADVAPI32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_ADVAPI32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", _SID_AND_ATTRIBUTES)]


def is_process_elevated(pid: int) -> bool | None:
    """True/False when determinable, None when unknown.

    Elevated means the process runs at High/System integrity (typically
    "Run as administrator"), which blocks input from normal processes.
    """
    if not pid:
        return None
    process = _KERNEL32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return None
    try:
        token = wintypes.HANDLE()
        if not _ADVAPI32.OpenProcessToken(
            process, _TOKEN_QUERY, ctypes.byref(token)
        ):
            return None
        try:
            size = wintypes.DWORD(0)
            _ADVAPI32.GetTokenInformation(
                token, _TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size)
            )
            if not size.value:
                return None
            buffer = ctypes.create_string_buffer(size.value)
            if not _ADVAPI32.GetTokenInformation(
                token, _TOKEN_INTEGRITY_LEVEL, buffer, size, ctypes.byref(size)
            ):
                return None
            tml = ctypes.cast(
                buffer, ctypes.POINTER(_TOKEN_MANDATORY_LABEL)
            ).contents
            sid = tml.Label.Sid
            if not sid:
                return None
            # SID layout: [0]=revision, [1]=subauthority count, then the
            # identifier authority, then the sub-authority array. The last
            # sub-authority carries the integrity RID.
            sub_authority_count = ctypes.c_ubyte.from_address(sid + 1).value
            if not sub_authority_count:
                return None
            rid = _ADVAPI32.GetSidSubAuthority(
                sid, sub_authority_count - 1
            ).contents.value
            return rid > _SECURITY_MANDATORY_MEDIUM_RID
        finally:
            _KERNEL32.CloseHandle(token)
    finally:
        _KERNEL32.CloseHandle(process)
