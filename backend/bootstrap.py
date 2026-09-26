"""Wrap app startup: one instance at a time, and startup failures visible."""
from __future__ import annotations

import ctypes
import sys
import traceback
from ctypes import wintypes
from pathlib import Path

if getattr(sys, "frozen", False):
    from backend.firstrun import app_home   # import-safe (no I/O)
    _ROOT = app_home()
else:
    _ROOT = Path(__file__).resolve().parent.parent
LOG = _ROOT / "transcribe-app-error.log"
_MUTEX_NAME = "Local\\Transcribe.SingleInstance"
_ERROR_ALREADY_EXISTS = 183


def _single_instance():
    """Hold a named mutex for the life of the process. Returns None if another
    instance already holds it. One GPU, one install folder: two copies of the
    app would compete for both (and cleanup must not run under a setup)."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL,
                                 wintypes.LPCWSTR)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = k32.CreateMutexW(None, False, _MUTEX_NAME)
    if handle and ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        k32.CloseHandle(handle)
        return None
    return handle


def run():
    guard = _single_instance()
    if guard is None:
        ctypes.windll.user32.MessageBoxW(
            None, "Transcribe is already running.", "Transcribe", 0x40)
        return
    try:
        from backend.app import main
        main()
    except Exception:
        tb = traceback.format_exc()
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            LOG.write_text(tb, encoding="utf-8")
        except Exception:
            pass
        ctypes.windll.user32.MessageBoxW(
            None,
            "Transcribe failed to start.\n\nDetails written to:\n"
            + str(LOG) + "\n\n" + tb[-800:],
            "Transcribe - startup error", 0x10)


if __name__ == "__main__":
    run()
