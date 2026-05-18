"""Probe whether a chosen output folder actually accepts a new file.

This is the mechanism, not a path blocklist: Controlled Folder Access and
OneDrive placeholders both surface as a failed create here, and we re-ask.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path


class ProbeError(Exception):
    """The chosen folder cannot accept a transcript file."""


def probe_writable(folder) -> None:
    folder = Path(folder)
    if not folder.is_dir():
        raise ProbeError(
            f"{folder} is not an existing folder. Pick a folder that exists.")
    test = folder / f".__wx_probe_{uuid.uuid4().hex}.tmp"
    try:
        with open(test, "w", encoding="utf-8") as fh:
            fh.write("probe")
        os.remove(test)
    except OSError as exc:
        raise ProbeError(
            f"Cannot create files in {folder}. This is usually Controlled "
            f"Folder Access or OneDrive (Documents is protected). Pick a "
            f"plain local folder, then copy into Documents via File Explorer. "
            f"({exc.__class__.__name__})") from exc
