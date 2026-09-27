"""Output dir resolution.

There is no output-folder UI: the default is ~/Transcripts, and a
hand-edited settings.json `last_output_dir` still wins (escape hatch).
"""
import json

from backend import app


def test_effective_out_dir_default_when_unset():
    api = app.Api()
    api._settings = {"last_output_dir": ""}
    assert api._effective_out_dir() == app.OUT_DIR


def test_effective_out_dir_uses_saved():
    api = app.Api()
    api._settings = {"last_output_dir": r"D:\Recordings\Out"}
    assert str(api._effective_out_dir()) == r"D:\Recordings\Out"


def test_api_reads_settings_json_outside_the_checkout(tmp_path):
    # conftest.py points SETTINGS into tmp_path, never at the checkout's file
    assert app.SETTINGS.is_relative_to(tmp_path)
    app.SETTINGS.parent.mkdir(parents=True)
    app.SETTINGS.write_text(json.dumps({"last_output_dir": r"D:\Out"}),
                            encoding="utf-8")
    assert str(app.Api()._effective_out_dir()) == r"D:\Out"
