"""backend.runner pure parts: args, events, word timing, error classes.

The GPU path (_load_asr/_run) runs only with the real models; these tests lock
the protocol and the logic that turns onnx-asr segments into words.
"""
import json
import sys
import types
from types import SimpleNamespace as Seg

import pytest

from backend import models, runner
from backend import transcript as T


def test_diarization_loads_the_pinned_revision(monkeypatch):
    calls = {}

    class Pipeline:
        @staticmethod
        def from_pretrained(model, **kw):
            calls.update(kw, model=model)
            return None                  # refused: stops before any GPU work
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pyannote", types.ModuleType("pyannote"))
    monkeypatch.setitem(sys.modules, "pyannote.audio",
                        types.SimpleNamespace(Pipeline=Pipeline))
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    with pytest.raises(runner.HfGate):
        runner._diarize(None)
    assert calls == {"model": models.DIARIZATION_MODEL,
                     "revision": models.DIARIZATION_REVISION,
                     "token": "hf_test"}


def _events(capsys):
    out = capsys.readouterr().out.strip().splitlines()
    return [json.loads(x) for x in out if x.strip()]


def test_parse_args_repeatable_audio():
    a = runner.parse_args(["--mode", "pertrack", "--audio", "a.m4a",
                           "--audio", "b.m4a", "--out-dir", "o",
                           "--asr-model-dir", "m", "--vad-model-dir", "v"])
    assert a.audio == ["a.m4a", "b.m4a"]
    assert (a.out_dir, a.ffmpeg) == ("o", "ffmpeg")


def test_parse_args_accepts_mono():
    a = runner.parse_args(["--mode", "mono", "--audio", "m.m4a",
                           "--out-dir", "o", "--asr-model-dir", "m",
                           "--vad-model-dir", "v"])
    assert a.mode == "mono" and a.audio == ["m.m4a"]


def test_mono_with_two_inputs_is_an_error_before_any_gpu_work(capsys):
    rc = runner.main(["--mode", "mono", "--audio", "a", "--audio", "b",
                      "--out-dir", "o", "--asr-model-dir", "m",
                      "--vad-model-dir", "v"])
    assert rc == 1
    (ev,) = _events(capsys)
    assert ev["ev"] == "error" and "exactly one" in ev["msg"]


def test_parse_args_rejects_unknown_mode():
    with pytest.raises(SystemExit):
        runner.parse_args(["--mode", "nope", "--audio", "a", "--out-dir",
                           "o", "--asr-model-dir", "m", "--vad-model-dir",
                           "v"])


def test_parse_args_requires_model_dirs():
    with pytest.raises(SystemExit):
        runner.parse_args(["--mode", "pertrack", "--audio", "a",
                           "--out-dir", "o"])


def test_emit_phase_is_one_json_line(capsys):
    runner.emit_phase("transcribe")
    assert _events(capsys) == [{"ev": "phase", "phase": "transcribe"}]


def test_pct_emitter_throttles_by_rounded_int(capsys):
    cb = runner.make_pct_emitter("transcribe")
    for v in (0.0, 0.4, 0.6, 1.0, 1.2, 2.9, 2.95):
        cb(v)
    evs = _events(capsys)
    assert all(e["ev"] == "pct" for e in evs)
    assert [round(e["pct"]) for e in evs] == [0, 1, 3]


def test_pct_emitter_ignores_non_finite_and_non_numeric(capsys):
    cb = runner.make_pct_emitter("transcribe")
    for v in (None, "x", float("nan"), float("inf")):
        cb(v)
    assert _events(capsys) == []


def test_ffmpeg_cmd_decodes_to_16k_mono_pcm():
    cmd = runner.ffmpeg_cmd("ff.exe", "in.m4a")
    assert cmd[0] == "ff.exe" and cmd[-1] == "-"
    assert cmd[cmd.index("-ar") + 1] == "16000"
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-f") + 1] == "s16le"


def test_words_join_tokens_and_offset_by_segment_start():
    seg = Seg(start=10.0, end=12.0, tokens=[" hel", "lo", ",", " world"],
              timestamps=[0.0, 0.08, 0.3, 0.5])
    words = runner.words_from_segments([seg])
    assert [w["word"] for w in words] == ["hello,", "world"]
    assert words[0]["start"] == 10.0 and words[1]["start"] == 10.5


def test_word_end_is_capped_by_slack_next_word_and_segment():
    slack = runner.WORD_END_SLACK
    seg = Seg(start=0.0, end=5.0, tokens=[" a", " b", " c"],
              timestamps=[0.0, 3.0, 3.05])
    a, b, c = runner.words_from_segments([seg])
    assert a["end"] == pytest.approx(slack)          # not stretched to 3.0
    assert b["end"] == pytest.approx(3.05)           # next word starts first
    assert c["end"] == pytest.approx(3.05 + slack)
    tail = Seg(start=0.0, end=1.1, tokens=[" z"], timestamps=[1.0])
    assert runner.words_from_segments([tail])[0]["end"] == pytest.approx(1.1)


def test_first_token_without_space_still_starts_a_word():
    seg = Seg(start=1.0, end=2.0, tokens=["ok", " go"], timestamps=[0.0, 0.4])
    assert [w["word"] for w in runner.words_from_segments([seg])] == [
        "ok", "go"]


def test_empty_segments_give_no_words():
    assert runner.words_from_segments(
        [Seg(start=0.0, end=1.0, tokens=[], timestamps=[])]) == []


def test_forced_cut_overlap_keeps_each_word_once():
    # onnx-asr cuts long speech at C and pads both chunks: [.., C+p], [C-p, ..]
    # The word at the cut appears in both, at slightly different times.
    s1 = Seg(start=0.0, end=30.2, tokens=[" one", " two"],
             timestamps=[29.8, 30.1])
    s2 = Seg(start=29.8, end=45.0, tokens=[" Two,", " three"],
             timestamps=[0.1, 0.35])
    words = runner.words_from_segments([s1, s2])
    assert [w["word"] for w in words] == ["one", "two", "three"]
    starts = [w["start"] for w in words]
    assert starts == sorted(starts)


def test_forced_cut_without_repeat_keeps_everything():
    s1 = Seg(start=0.0, end=30.2, tokens=[" one"], timestamps=[29.7])
    s2 = Seg(start=29.8, end=45.0, tokens=[" three"], timestamps=[0.35])
    assert [w["word"] for w in runner.words_from_segments([s1, s2])] == [
        "one", "three"]


def test_non_overlapping_segments_never_drop_repeats():
    # a speaker really saying the same word twice across a pause
    s1 = Seg(start=0.0, end=1.0, tokens=[" no"], timestamps=[0.2])
    s2 = Seg(start=2.0, end=3.0, tokens=[" no"], timestamps=[0.2])
    assert [w["word"] for w in runner.words_from_segments([s1, s2])] == [
        "no", "no"]


def test_vad_gaps_stay_below_turn_gap():
    # per-track turns split on word gaps > GAP_SEC; VAD must not merge pauses
    # that long into one segment
    assert (runner.VAD_OPTIONS["min_silence_duration_ms"]
            < T.GAP_SEC * 1000)


def test_classify_no_cuda_oom_other():
    code, msg = runner.classify(runner.NoCuda("CUDA EP missing"))
    assert code == "no_cuda" and "NVIDIA" in msg
    assert runner.classify(RuntimeError(
        "CUDA failure 2: out of memory"))[0] == "oom"

    class OutOfMemoryError(Exception):
        pass
    assert runner.classify(OutOfMemoryError("x"))[0] == "oom"
    code, msg = runner.classify(ValueError("disk full"))
    assert code == "other" and msg == "ValueError: disk full"


def test_undecodable_file_gets_a_plain_message(monkeypatch):
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: Seg(
        returncode=1, stdout=b"", stderr=b"header\nInvalid data found\n"))
    with pytest.raises(runner.BadAudio) as e:
        runner.load_audio("ffmpeg", "C:\\rec\\broken.m4a")
    code, msg = runner.classify(e.value)
    assert code == "other"
    assert msg.startswith("Transcribe couldn't read broken.m4a.")
    assert "(ffmpeg: Invalid data found)" in msg


def test_classify_hf_gate():
    code, msg = runner.classify(runner.HfGate("401 Unauthorized"))
    assert code == "hf_gate"
    assert "huggingface.co/" + runner.DIARIZATION_MODEL in msg


def test_gate_error_recognizes_hub_refusals_only():
    hub = pytest.importorskip("huggingface_hub.errors")
    resp = type("Resp", (), {"status_code": 403, "headers": {},
                             "request": None})()
    assert runner.gate_error(hub.GatedRepoError("gated"))
    assert runner.gate_error(hub.RepositoryNotFoundError("nope"))
    assert runner.gate_error(hub.HfHubHTTPError("403", response=resp))
    try:                                         # wrapped refusal still counts
        try:
            raise hub.GatedRepoError("gated")
        except Exception as inner:
            raise RuntimeError("load failed") from inner
    except RuntimeError as outer:
        assert runner.gate_error(outer)
    # a CUDA OOM whose text happens to contain 401/403 is NOT a gate error
    assert not runner.gate_error(RuntimeError(
        "CUDA out of memory. Tried to allocate 1403.00 MiB"))
    resp.status_code = 500
    assert not runner.gate_error(hub.HfHubHTTPError("500", response=resp))


def test_main_turns_exceptions_into_one_error_event(capsys, monkeypatch):
    def boom(_args):
        raise RuntimeError("ffmpeg could not decode x.m4a: bad data")
    monkeypatch.setattr(runner, "_run", boom)
    rc = runner.main(["--mode", "pertrack", "--audio", "x.m4a", "--out-dir",
                      "o", "--asr-model-dir", "m", "--vad-model-dir", "v"])
    assert rc == 1
    (ev,) = _events(capsys)
    assert ev["ev"] == "error" and ev["code"] == "other"
    assert "could not decode" in ev["msg"]


# --- the context retry for short segments -----------------------------------

def _audio(seconds):
    """Sample numbers stand in for audio, so a window's first value tells a
    fake model where the window starts."""
    return list(range(round(seconds * runner.SAMPLE_RATE)))


class _Plain:
    """The model without VAD: decodes any window as the given (token,
    absolute time) pairs that fall in it, stamped from the window's start."""

    def __init__(self, *tokens):
        self.tokens, self.windows = tokens, []

    def recognize(self, window, sample_rate):
        start = window[0] / sample_rate
        end = (window[-1] + 1) / sample_rate
        self.windows.append((start, end))
        inside = [(tok, t - start) for tok, t in self.tokens
                  if start <= t < end]
        return Seg(tokens=[tok for tok, _ in inside],
                   timestamps=[t for _, t in inside])


def _retried(plain, *segments, seconds=30):
    return [(w["word"], w["start"]) for w in runner.words_from_segments(
        runner.retry_short_segments(plain, _audio(seconds), list(segments)))]


def test_a_short_segment_with_unheard_speech_is_decoded_with_context():
    # 10 to 13 s: one word, then about 2 s of speech with none
    seg = Seg(start=10.0, end=13.0, tokens=[" Right."], timestamps=[0.4])
    plain = _Plain((" said", 9.0), (" the", 11.5), (" sched", 11.8),
                   ("ule.", 12.0), (" next", 13.5))
    assert _retried(plain, seg) == [("the", 11.5), ("schedule.", 11.8)]
    assert plain.windows == [(8.0, 15.0)]


def test_the_retry_keeps_whole_words_that_start_in_the_segment():
    seg = Seg(start=10.0, end=12.0, tokens=[], timestamps=[])
    plain = _Plain((" early", 9.7), ("ish", 9.9), (" now", 9.9),
                   (" what", 10.5), ("ever", 12.1), (" late", 12.2))
    assert _retried(plain, seg) == [("now", 9.9), ("whatever", 10.5)]


def test_the_first_pass_stays_unless_the_retry_has_more_real_words():
    seg = Seg(start=10.0, end=13.0, tokens=[" Right."], timestamps=[0.4])
    plain = _Plain((" Um,", 11.0), (" right.", 11.4))
    assert _retried(plain, seg) == [("Right.", 10.4)]
    assert len(plain.windows) == 1


def test_long_covered_and_forced_cut_segments_are_not_retried():
    plain = _Plain((" word", 21.0))
    long = Seg(start=0.0, end=6.5, tokens=[" a"], timestamps=[0.3])
    covered = Seg(start=8.0, end=9.6, tokens=[" b", " c", " d"],
                  timestamps=[0.3, 0.7, 1.1])
    cut = [Seg(start=10.0, end=20.2, tokens=[" e"], timestamps=[1.0]),
           Seg(start=19.8, end=22.0, tokens=[" f"], timestamps=[0.3])]
    _retried(plain, long, covered, *cut)
    assert plain.windows == []


def test_transcribe_retries_after_the_vad_pass(capsys):
    segments = [Seg(start=10.0, end=12.0, tokens=[], timestamps=[])]
    with_vad = Seg(recognize=lambda audio, sample_rate: iter(segments))
    words = runner._transcribe((with_vad, _Plain((" late", 10.5))),
                               _audio(20), 20.0)
    assert [w["word"] for w in words] == ["late"]
