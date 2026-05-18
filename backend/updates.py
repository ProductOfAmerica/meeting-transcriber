"""Non-blocking WhisperX update check against PyPI. Fails silent offline."""
from __future__ import annotations

import json
import urllib.request
from importlib import metadata


def _installed():
    return metadata.version("whisperx")


def _latest(timeout: float) -> str:
    req = urllib.request.Request(
        "https://pypi.org/pypi/whisperx/json",
        headers={"User-Agent": "meeting-transcriber-gui"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["info"]["version"]


def _as_tuple(v: str):
    parts = []
    for chunk in v.split("."):
        num = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def check_whisperx_update(timeout: float = 2.0, enabled: bool = True):
    if not enabled:
        return None
    try:
        installed = _installed()
        latest = _latest(timeout)
        if _as_tuple(latest) > _as_tuple(installed):
            return latest
        return None
    except Exception:
        return None
