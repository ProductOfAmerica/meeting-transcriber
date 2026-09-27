"""Prepare a source checkout to transcribe: download the pinned speech models
and the pinned ffmpeg into the repo (models\\ and runtime\\, both gitignored),
the same files first-run setup installs for the exe. run.cmd runs it before
every launch, and whatever is already there is skipped. It needs only the
standard library, so either environment can run it:

    .venv-build\\Scripts\\python.exe -m backend.devsetup
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from . import fetch, firstrun, models

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    layout = firstrun.Layout(ROOT, ROOT)
    models.fetch(layout.models, on_file=lambda spec, name: print(
        f"{spec.name}: {name}", flush=True))
    if not layout.ffmpeg_exe.exists():
        print("ffmpeg", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "ffmpeg.zip"
            fetch.download(firstrun.FFMPEG_URL, archive, firstrun.FFMPEG_SHA256)
            firstrun.install_ffmpeg(layout, archive)
    print(f"ready: {layout.models} and {layout.ffmpeg_exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
