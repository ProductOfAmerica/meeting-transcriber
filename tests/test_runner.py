"""Unit tests for backend.runner's pure parts (no whisperx / no GPU).

The whisperx orchestration in runner._run is exercised only on the user's
GPU box (documented in the plan); here we lock the event protocol, arg
parsing, gate-hint, and the JSON-shape contract with the real consumer.
"""
import json

import pytest

from backend import runner
from backend import transcript as T


def _events(capsys):
    out = capsys.readouterr().out.strip().splitlines()
    return [json.loads(x) for x in out if x.strip()]


def test_parse_args_ok():
    a = runner.parse_args(["--mode", "pertrack", "--audio", "x.m4a",
                           "--out-dir", "o"])
    assert (a.mode, a.audio, a.out_dir) == ("pertrack", "x.m4a", "o")


def test_parse_args_rejects_bad_mode():
    with pytest.raises(SystemExit):
        runner.parse_args(["--mode", "nope", "--audio", "a",
                           "--out-dir", "o"])


def test_parse_args_requires_all():
    with pytest.raises(SystemExit):
        runner.parse_args(["--mode", "mono"])


def test_emit_phase_is_one_json_line(capsys):
    runner.emit_phase("transcribe")
    assert _events(capsys) == [{"ev": "phase", "phase": "transcribe"}]


def test_pct_emitter_throttles_by_rounded_int(capsys):
    cb = runner.make_pct_emitter("align")
    for v in (0.0, 0.4, 0.6, 1.0, 1.2, 2.9, 2.95):
        cb(v)
    evs = _events(capsys)
    assert all(e["ev"] == "pct" and e["phase"] == "align" for e in evs)
    assert [round(e["pct"]) for e in evs] == [0, 1, 3]


def test_pct_emitter_announce_emits_phase_first(capsys):
    cb = runner.make_pct_emitter("transcribe", announce=True)
    cb(5.0)
    evs = _events(capsys)
    assert evs[0] == {"ev": "phase", "phase": "transcribe"}
    assert evs[1]["ev"] == "pct" and evs[1]["phase"] == "transcribe"


def test_pct_emitter_ignores_non_finite_and_non_numeric(capsys):
    cb = runner.make_pct_emitter("diarize")
    cb(None)
    cb("nan-ish")
    cb(float("nan"))
    cb(float("inf"))
    assert _events(capsys) == []          # nothing emitted, no crash


def test_gate_hint_wraps_pyannote_auth():
    msg = runner.gate_hint(RuntimeError("401 Client Error: Unauthorized"))
    assert "huggingface.co/pyannote" in msg
    assert "401" in msg


def test_gate_hint_passthrough_other():
    assert runner.gate_hint(ValueError("disk full")) == "disk full"


def test_build_result_json_roundtrips_pertrack():
    result = {
        "segments": [{"start": 0.0, "end": 1.0, "text": "hi",
                      "words": [{"word": "hi", "start": 0.0, "end": 0.5,
                                 "score": 0.9}]}],
        "word_segments": [{"word": "hi", "start": 0.0, "end": 0.5,
                           "score": 0.9}],
    }
    j = runner.build_result_json(result, "en")
    assert j["language"] == "en"
    words = T.words_from_whisperx_json(j)
    assert [w["word"] for w in words] == ["hi"]


def test_build_result_json_roundtrips_mono_with_speaker():
    result = {
        "segments": [{"start": 0.0, "end": 1.0, "text": "yo",
                      "speaker": "SPEAKER_00",
                      "words": [{"word": "yo", "start": 0.0, "end": 0.4,
                                 "speaker": "SPEAKER_00"}]}],
        "word_segments": [{"word": "yo", "start": 0.0, "end": 0.4,
                           "speaker": "SPEAKER_00"}],
    }
    words = T.words_from_whisperx_json(
        runner.build_result_json(result, "en"))
    assert words[0]["speaker"] == "SPEAKER_00"
