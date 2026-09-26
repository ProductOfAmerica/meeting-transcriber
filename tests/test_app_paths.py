"""The page names no files: the Api keeps the chosen recording and the last
transcript itself, so nothing reachable from JavaScript reads or opens a path
the page supplies."""
import inspect

from backend import app


def test_page_facing_methods_take_no_paths():
    for name in ("start", "copy_transcript", "open_folder"):
        params = list(inspect.signature(getattr(app.Api, name)).parameters)
        assert params == ["self"], name


def test_start_without_a_chosen_recording_does_nothing():
    api = app.Api()
    api.start()
    assert api._jobs._job is None           # no job was begun


def test_copy_transcript_reads_the_last_output(tmp_path):
    api = app.Api()
    assert api.copy_transcript()["ok"] is False
    out = tmp_path / "Weekly sync.transcript.txt"
    out.write_text("Meeting transcript\n", encoding="utf-8")
    api._last_output = out
    assert api.copy_transcript() == {"ok": True, "text": "Meeting transcript\n"}


def test_open_folder_opens_the_last_outputs_folder(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(app.os, "startfile", opened.append)
    api = app.Api()
    api.open_folder()                       # nothing yet: nothing opens
    api._last_output = tmp_path / "x.transcript.txt"
    api.open_folder()
    assert opened == [tmp_path]
