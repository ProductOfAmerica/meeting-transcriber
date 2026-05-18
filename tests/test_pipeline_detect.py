from pathlib import Path
from backend import pipeline as P


def test_single_mixed_file_is_mono(tmp_path):
    f = tmp_path / "audio123.m4a"
    f.write_bytes(b"x")
    r = P.detect_input(f)
    assert r["mode"] == "mono"
    assert r["audio"] == [f]


def test_meeting_folder_no_audio_record_is_mono(tmp_path):
    (tmp_path / "audio123.m4a").write_bytes(b"x")
    (tmp_path / "video123.mp4").write_bytes(b"x")
    (tmp_path / "recording.conf").write_text('{"items":[]}', encoding="utf-8")
    r = P.detect_input(tmp_path)
    assert r["mode"] == "mono"
    assert r["audio"][0].name == "audio123.m4a"


def test_audio_record_folder_is_pertrack(tmp_path):
    rec = tmp_path / "Audio Record"
    rec.mkdir()
    (rec / "Alice Example.m4a").write_bytes(b"x")
    (rec / "Bob Example.m4a").write_bytes(b"x")
    (tmp_path / "audio123.m4a").write_bytes(b"x")
    r = P.detect_input(tmp_path)
    assert r["mode"] == "pertrack"
    assert sorted(p.name for p in r["audio"]) == [
        "Alice Example.m4a", "Bob Example.m4a"]


def test_audio_record_case_insensitive(tmp_path):
    rec = tmp_path / "audio record"
    rec.mkdir()
    (rec / "A.m4a").write_bytes(b"x")
    r = P.detect_input(tmp_path)
    assert r["mode"] == "pertrack"


def test_empty_or_unknown_folder_asks(tmp_path):
    r = P.detect_input(tmp_path)
    assert r["mode"] == "ask"


import os
from backend import pipeline as P2


def test_cuda_path_prepended_first(tmp_path):
    venv = tmp_path / "venv"
    (venv / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin").mkdir(parents=True)
    (venv / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin").mkdir(parents=True)
    env = P2.build_env(venv, base_env={"PATH": "C:\\existing"})
    parts = env["PATH"].split(os.pathsep)
    assert parts[0].endswith(r"nvidia\cudnn\bin")
    assert parts[1].endswith(r"nvidia\cublas\bin")
    # frozen build's managed ffmpeg (home/runtime/ffmpeg); harmless in dev
    assert parts[2].endswith(r"runtime\ffmpeg")
    assert "C:\\existing" in env["PATH"]
    assert env["PYTHONUNBUFFERED"] == "1"   # runner child fully unbuffered
    assert env is not None


import pytest


def test_derive_magic_from_mixed_file(tmp_path):
    (tmp_path / "audio1721414883.m4a").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    rec = tmp_path / "Audio Record"
    rec.mkdir()
    (rec / "audioBen11721414883.m4a").write_bytes(b"x")
    assert P.derive_magic(tmp_path) == "1721414883"


def test_derive_magic_none_when_no_mixed(tmp_path):
    (tmp_path / "Audio Record").mkdir()
    assert P.derive_magic(tmp_path) is None


def test_speaker_name_from_track_real_samples():
    assert P.speaker_name_from_track(
        "audioAlice11721414883.m4a", "1721414883") == "Alice"
    assert P.speaker_name_from_track(
        "audioCarol21721414883.m4a", "1721414883") == "Carol"


def test_speaker_name_from_track_fallbacks():
    # magic unknown: still strips audio prefix + trailing digits
    assert P.speaker_name_from_track("audioAlice11721414883.m4a", None) == "Alice"
    # nothing parseable -> the stem
    assert P.speaker_name_from_track("weird.m4a", None) == "weird"


def test_drop_intermediates_keeps_only_transcript(tmp_path):
    for n in ("a.json", "a.srt", "a.vtt", "a.tsv", "a.txt",
              "X.transcript.txt"):
        (tmp_path / n).write_text("x", encoding="utf-8")
    P._drop_intermediates(tmp_path, "a")
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["X.transcript.txt"]


def test_run_pertrack_empty_raises_runtime_not_gated():
    with pytest.raises(RuntimeError) as e:
        P.run_pertrack(audio=[], out_dir=".", venv_dir=".")
    assert not isinstance(e.value, P.PerTrackNotVerified)
    assert "per-participant" in str(e.value).lower()


def _pertrack_folder(tmp_path):
    (tmp_path / "audio99.m4a").write_bytes(b"x")
    rec = tmp_path / "Audio Record"
    rec.mkdir()
    (rec / "audioBob199.m4a").write_bytes(b"x")
    (rec / "audioAmy299.m4a").write_bytes(b"x")
    return rec


def test_detect_picked_mixed_file_routes_pertrack(tmp_path):
    _pertrack_folder(tmp_path)
    r = P.detect_input(tmp_path / "audio99.m4a")   # user picks the mixed file
    assert r["mode"] == "pertrack"
    assert sorted(p.name for p in r["audio"]) == [
        "audioAmy299.m4a", "audioBob199.m4a"]


def test_detect_picked_participant_track_routes_pertrack(tmp_path):
    rec = _pertrack_folder(tmp_path)
    r = P.detect_input(rec / "audioBob199.m4a")    # user picks one track
    assert r["mode"] == "pertrack"
    assert len(r["audio"]) == 2


def test_detect_lone_media_file_still_mono(tmp_path):
    f = tmp_path / "random.mp3"
    f.write_bytes(b"x")
    r = P.detect_input(f)
    assert r["mode"] == "mono"
    assert r["audio"] == [f]


def test_summarize_pertrack_human_fields(tmp_path):
    _pertrack_folder(tmp_path)
    s = P.summarize_input(tmp_path / "audio99.m4a")
    assert s["mode"] == "pertrack"
    assert s["needs_token"] is False
    assert s["speakers"] == ["Amy", "Bob"]
    assert s["title"] == tmp_path.name


def test_summarize_mono_needs_token(tmp_path):
    f = tmp_path / "lone.mp3"
    f.write_bytes(b"x")
    s = P.summarize_input(f)
    assert s["mode"] == "mono"
    assert s["needs_token"] is True
    assert s["speakers"] == []
    assert s["title"] == "lone.mp3"


def _noop(*a, **k):
    pass


def test_run_one_missing_venv_raises_friendly(tmp_path):
    # No venv\Scripts\python.exe -> actionable message, not a raw
    # bootstrap traceback. env_status normally gates this; it is the
    # backstop, so it must point at the in-app first-run setup / repair.
    with pytest.raises(RuntimeError) as e:
        P._run_one(mode="pertrack", audio=tmp_path / "a.m4a",
                   out_dir=tmp_path, venv_dir=tmp_path / "venv",
                   meta={}, progress_cb=_noop, cancel_event=None,
                   register_proc=_noop)
    msg = str(e.value)
    assert "isn't installed" in msg
    assert "reopen Transcribe" in msg
    assert "%LOCALAPPDATA%" in msg
    assert "setup.cmd" not in msg          # the script no longer exists


class _FailedProbe:
    returncode = 3
    stdout = ""
    stderr = ""


def test_preflight_cuda_message_is_actionable(tmp_path, monkeypatch):
    # Simulate torch.cuda.is_available() == False without a GPU:
    # ffmpeg present, the cuda probe subprocess exits nonzero.
    monkeypatch.setattr(P.shutil, "which", lambda _n: "ffmpeg")
    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: _FailedProbe())
    with pytest.raises(P.PreflightError) as e:
        P.preflight_pertrack(tmp_path)
    msg = str(e.value)
    assert "NVIDIA GPU" in msg
    assert "CUDA 12.8" in msg
    assert "%LOCALAPPDATA%" in msg
    assert "setup.cmd" not in msg          # the script no longer exists
