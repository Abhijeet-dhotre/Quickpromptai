# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for QuickPrompt AI (one-file windowed executable)."""
import os
import time

ROOT = os.path.abspath(SPECPATH)  # noqa: F821 - SPECPATH provided by PyInstaller


def _make_pe_checksum_lock_tolerant():
    """Stop a file lock from destroying an otherwise complete build.

    ``EXE.assemble()`` calls ``update_exe_pe_checksum`` straight after writing
    the one-file executable. That helper does two things that both fail when the
    freshly written ~60 MB exe is held open by someone else:

    1. it maps the file through ``CheckSumMappedFile``, which real-time
       antivirus scanning regularly has locked at that instant;
    2. it rewrites the file to store the new checksum.

    PyInstaller only retries for ~1.5 s in total, then aborts the build and
    leaves no ``dist`` output at all - a ~2 minute build silently wasted.

    The PE checksum is only an anti-heuristic nicety (PyInstaller#5579), so this
    replaces the helper with a version that falls back to pefile's pure-Python
    checksum (no mapping, hence no lock), retries the rewrite, and gives up
    quietly rather than killing the build.
    """
    try:
        from PyInstaller.utils.win32 import winutils

        import pefile
    except ImportError:  # pragma: no cover - non-Windows or pefile missing
        return

    if getattr(winutils, "_quickprompt_checksum_patched", False):
        return

    native_compute = winutils.compute_exe_pe_checksum

    def compute_exe_pe_checksum(exe_path):
        try:
            return native_compute(exe_path)
        except Exception:
            with pefile.PE(exe_path, fast_load=True) as pe:
                return pe.generate_checksum()

    def update_exe_pe_checksum(exe_path):
        checksum = compute_exe_pe_checksum(exe_path)
        with pefile.PE(exe_path, fast_load=True) as pe:
            pe.OPTIONAL_HEADER.CheckSum = checksum
            data = pe.write()

        last = None
        for attempt in range(12):  # ~30 s of escalating backoff
            try:
                with open(exe_path, "wb") as fp:
                    fp.write(data)
                return
            except PermissionError as exc:
                last = exc
                time.sleep(0.5 + attempt * 0.5)
        print(f"[quickprompt] PE checksum not written ({last}); "
              "the executable is still complete and usable.")

    winutils.compute_exe_pe_checksum = compute_exe_pe_checksum
    winutils.update_exe_pe_checksum = update_exe_pe_checksum
    winutils._quickprompt_checksum_patched = True


_make_pe_checksum_lock_tolerant()


_make_pe_checksum_lock_tolerant()

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "assets", "icon.ico"), "assets"),
        (os.path.join(ROOT, "assets", "icon.png"), "assets"),
    ],
    hiddenimports=[
        # pynput selects its Windows backend dynamically:
        "pynput.keyboard._base",
        "pynput.keyboard._win32",
        "pynput.mouse._base",
        "pynput.mouse._win32",
        # keyring discovers backends dynamically:
        "keyring.backends.Windows",
        "keyring.backends.null",
        "keyring.backends.chainer",
        # sounddevice is imported lazily by the dictation recorder:
        "sounddevice",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pydoc_data", "unittest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="QuickPromptAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                     # UPX triggers AV heuristics; keep it off
    runtime_tmpdir=None,
    console=False,                 # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
)
