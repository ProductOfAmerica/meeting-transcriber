"""Settings loading. Kept separate from app.py so it is unit-testable
without pywebview.

settings.json is read-only from the app's side: there is no settings UI.
A user can hand-edit it (e.g. last_output_dir) as an escape hatch.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULTS = {"last_output_dir": ""}


def load_settings(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(DEFAULTS)
        merged = dict(DEFAULTS)
        merged.update({k: data[k] for k in DEFAULTS if k in data})
        return merged
    except Exception:
        return dict(DEFAULTS)
