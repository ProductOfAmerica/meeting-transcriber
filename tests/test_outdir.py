"""Output dir resolution.

There is no output-folder UI: the default is ~/Transcripts, and a
hand-edited settings.json `last_output_dir` still wins (escape hatch).
"""
from backend import app


def test_effective_out_dir_default_when_unset():
    api = app.Api()
    api._settings = {"last_output_dir": ""}
    assert api._effective_out_dir() == app.OUT_DIR


def test_effective_out_dir_uses_saved():
    api = app.Api()
    api._settings = {"last_output_dir": r"D:\Recordings\Out"}
    assert str(api._effective_out_dir()) == r"D:\Recordings\Out"
