"""Folder operations that survive Windows reality. Import-safe.

Antivirus scanners (Defender) and indexers briefly hold files that were just
written, so a rename or delete right after an install can fail with access
denied (WinError 5), sharing violation (32) or directory not empty (145).
These helpers retry those errors with backoff instead of failing the setup.
"""
from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path

_TRANSIENT = {5, 32, 145}


def retry(op, attempts: int = 8, first_delay: float = 0.1):
    """Run op(), retrying transient Windows file errors with backoff."""
    delay = first_delay
    for i in range(attempts):
        try:
            return op()
        except OSError as exc:
            if (i == attempts - 1
                    or getattr(exc, "winerror", None) not in _TRANSIENT):
                raise
            time.sleep(delay)
            delay = min(delay * 2, 2.0)


def rmtree(path) -> None:
    """Delete a folder (or file), clearing read-only bits and retrying."""
    path = Path(path)
    if path.is_file() or path.is_symlink():
        retry(path.unlink)
        return
    if not path.exists():
        return

    def onerror(func, target, exc_info):
        if isinstance(exc_info[1], PermissionError):
            try:
                os.chmod(target, stat.S_IWRITE)
            except OSError:
                pass
        retry(lambda: func(target))

    shutil.rmtree(path, onerror=onerror)


def move_into_place(tmp, final) -> None:
    """Rename a finished temp folder to its final name. If a folder with that
    name already exists (built earlier with the same key), keep it and
    discard ours."""
    tmp, final = Path(tmp), Path(final)

    def op():
        if final.exists():
            return False
        tmp.rename(final)
        return True

    if not retry(op):
        rmtree(tmp)
