"""Tests never touch the developer's own Transcribe folders. backend.app fixes
its paths at import: from source, settings.json, logs and hf sit in the
checkout, and transcripts go to ~/Transcripts."""
import pytest

try:
    from backend import app
except ImportError:         # venv\ has no pywebview; test_api_guard runs there
    app = None


@pytest.fixture(autouse=True)
def _no_dev_folders(tmp_path, monkeypatch):
    if app is None:
        return
    home = tmp_path / "app-home"
    monkeypatch.setattr(app, "SETTINGS", home / "settings.json")
    monkeypatch.setattr(app, "LOGS", home / "logs")
    monkeypatch.setattr(app, "HF_HOME", home / "hf")
    monkeypatch.setattr(app, "OUT_DIR", tmp_path / "app-transcripts")
