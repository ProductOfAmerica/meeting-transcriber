"""Pinned, sha256-verified downloads. Import-safe."""
from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

from .procs import Cancelled


def verify_sha256(path: Path, want: str) -> None:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    got = h.hexdigest()
    if got != want:
        raise RuntimeError(
            "Download integrity check FAILED for " + Path(path).name + ".\n"
            "expected sha256 " + want + "\n"
            "got      sha256 " + got + "\n"
            "The pinned download was moved, corrupted, or tampered with. "
            "Not proceeding.")


def download(url, dest, want_sha, on_pct=None, cancelled=None) -> None:
    """Download url to dest through dest.part; verify sha256 before the file
    appears at dest. cancelled() -> bool is polled between chunks."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(
        url, headers={"User-Agent": "Transcribe-setup"})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            while True:
                if cancelled is not None and cancelled():
                    raise Cancelled()
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total and on_pct:
                    on_pct(done * 100.0 / total)
    verify_sha256(tmp, want_sha)
    tmp.replace(dest)
