"""Wrap app startup so pythonw failures are visible, not silent."""
from __future__ import annotations

import ctypes
import sys
import traceback
from pathlib import Path

if getattr(sys, "frozen", False):
    from backend.firstrun import app_home   # import-safe (no I/O)
    _ROOT = app_home()
else:
    _ROOT = Path(__file__).resolve().parent.parent
LOG = _ROOT / "transcribe-app-error.log"


def run():
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
