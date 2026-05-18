"""Regression guard: _emit must serialize payloads as JSON, not Python %r.

The old `"window.__on(%r, %r)" % (...)` emitted Python literals: a None
became `None` and True became `True`, which are ReferenceErrors in JS. The
structured progress payload carries None (mono: track/tracks/name), so this
path was a latent crash until _emit switched to json.dumps.
"""
import json

from backend import app


class _FakeWin:
    def __init__(self):
        self.calls = []

    def evaluate_js(self, s):
        self.calls.append(s)


def _emit_once(payload, channel="progress"):
    api = app.Api()
    api._window = _FakeWin()
    api._emit(channel, payload)
    assert len(api._window.calls) == 1
    return api._window.calls[0]


def test_emit_none_and_bool_are_valid_js_not_python():
    payload = {"stage": "Aligning", "mode": "mono", "track": None,
               "tracks": None, "name": None, "ok": True}
    js = _emit_once(payload)
    assert "None" not in js and "True" not in js   # the latent %r bug
    assert js == "window.__on(%s, %s)" % (
        json.dumps("progress"), json.dumps(payload))
    inner = js[len("window.__on("):-1]
    chan, payload_js = inner.split(", ", 1)
    assert json.loads(chan) == "progress"
    assert json.loads(payload_js)["track"] is None


def test_emit_string_payload_stays_valid():
    js = _emit_once("boom <tag> & 'x'", channel="error")
    assert js == 'window.__on("error", %s)' % json.dumps("boom <tag> & 'x'")
    assert "&amp;" not in js   # not HTML-escaped: sink is textContent
