import os
import sys
import threading
import time
from pathlib import Path

import pytest

from backend import models, procs
from backend import pipeline as P

FAKE = str(Path(__file__).with_name("fake_runner.py"))


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


def test_build_env_sets_child_vars_and_keeps_base():
    env = P.build_env(base_env={"PATH": "C:\\existing", "KEEP": "1"})
    assert env["PATH"] == "C:\\existing" and env["KEEP"] == "1"
    assert env["PYTHONUNBUFFERED"] == "1"   # runner child fully unbuffered
    assert env["PYTHONUTF8"] == "1"         # child writes what we decode
    assert env["PYANNOTE_METRICS_ENABLED"] == "0"
    assert env["HF_HUB_DISABLE_TELEMETRY"] == "1"


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
    assert P.speaker_name_from_track(
        "audioElliotRybak,CFP11721414883.m4a",
        "1721414883") == "Elliot Rybak, CFP"
    assert P.speaker_name_from_track(
        "audioLeeT21721414883.m4a", "1721414883") == "Lee T"


def test_speaker_name_from_track_fallbacks():
    # magic unknown: still strips audio prefix + trailing digits
    assert P.speaker_name_from_track("audioAlice11721414883.m4a", None) == "Alice"
    # nothing parseable -> the stem
    assert P.speaker_name_from_track("weird.m4a", None) == "weird"


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


# --- preflight -----------------------------------------------------------

def _runtime(tmp_path, *, venv=True, ffmpeg=True, model_files=True):
    """A fake install: venv python, ffmpeg, model folders."""
    v = tmp_path / "rt" / "venv"
    if venv:
        (v / "Scripts").mkdir(parents=True)
        (v / "Scripts" / "python.exe").write_bytes(b"")
    ff = tmp_path / "rt" / "ffmpeg.exe"
    if ffmpeg:
        ff.parent.mkdir(parents=True, exist_ok=True)
        ff.write_bytes(b"")
    m = tmp_path / "rt" / "models"
    if model_files:
        for spec in models.ALL:
            spec.dir(m).mkdir(parents=True)
    return v, m, ff


def test_preflight_missing_venv_points_at_setup(tmp_path):
    v, m, ff = _runtime(tmp_path, venv=False)
    with pytest.raises(P.PreflightError) as e:
        P.preflight("pertrack", v, m, ff)
    assert "isn't installed" in str(e.value)
    assert "%LOCALAPPDATA%" in str(e.value)


def test_preflight_missing_ffmpeg(tmp_path):
    v, m, ff = _runtime(tmp_path, ffmpeg=False)
    with pytest.raises(P.PreflightError) as e:
        P.preflight("pertrack", v, m, ff)
    assert "ffmpeg" in str(e.value)
    with pytest.raises(P.PreflightError):
        P.preflight("pertrack", v, m, None)


def test_preflight_missing_models(tmp_path):
    v, m, ff = _runtime(tmp_path, model_files=False)
    with pytest.raises(P.PreflightError) as e:
        P.preflight("pertrack", v, m, ff)
    assert "model" in str(e.value)


def test_preflight_mono_needs_hf_token(tmp_path, monkeypatch):
    v, m, ff = _runtime(tmp_path)
    monkeypatch.setenv("HF_TOKEN", "hf_from_the_environment_is_not_used")
    P.preflight("pertrack", v, m, ff)          # per-track never needs it
    with pytest.raises(P.PreflightError) as e:
        P.preflight("mono", v, m, ff)
    assert "Hugging Face token" in str(e.value)
    P.preflight("mono", v, m, ff, "hf_test")


def test_runner_cmd_lists_every_track_and_model_dirs(tmp_path):
    cmd = P.runner_cmd(tmp_path / "venv", "pertrack",
                       [Path("a.m4a"), Path("b.m4a")], tmp_path / "out",
                       tmp_path / "models", Path("ff.exe"))
    assert cmd[1:4] == ["-m", "backend.runner", "--mode"]
    assert [cmd[i + 1] for i, x in enumerate(cmd) if x == "--audio"] == [
        "a.m4a", "b.m4a"]
    assert cmd[cmd.index("--asr-model-dir") + 1] == str(
        models.PARAKEET.dir(tmp_path / "models"))
    assert cmd[cmd.index("--ffmpeg") + 1] == "ff.exe"


# --- run_job end to end through the real Supervisor + fake runner ---------

win_only = pytest.mark.skipif(sys.platform != "win32",
                              reason="job objects are Windows-only")


@pytest.fixture
def job(tmp_path, monkeypatch):
    """run_job wired to tests/fake_runner.py in a given mode."""
    (tmp_path / "meeting").mkdir()
    rec = _pertrack_folder(tmp_path / "meeting")
    out = tmp_path / "Transcripts"
    out.mkdir()
    v, m, ff = _runtime(tmp_path)
    seen = {}

    def run(mode, supervisor=None, progress=None):
        def fake_cmd(venv_dir, _mode, audio, out_dir, models_root, ffmpeg):
            seen["work"] = Path(out_dir)
            return [sys.executable, FAKE, mode, str(out_dir), str(len(audio))]
        monkeypatch.setattr(P, "runner_cmd", fake_cmd)
        sup = supervisor or procs.Supervisor()
        sup.begin()
        try:
            return P.run_job(
                mode="pertrack", audio=sorted(rec.glob("*.m4a")),
                out_dir=out, venv_dir=v, code_dir=tmp_path, models_root=m,
                ffmpeg=ff, supervisor=sup, log_path=tmp_path / "run.log",
                progress_cb=progress or (lambda *a: None))
        finally:
            sup.end()
    return run, out, seen, tmp_path


@win_only
def test_run_job_writes_transcript_and_cleans_scratch(job):
    run, out, seen, _ = job
    events = []
    stats = run("ok", progress=lambda stage, _l, meta: events.append(
        (stage, meta.get("track"), meta.get("name"))))
    text = Path(stats["output_path"]).read_text(encoding="utf-8")
    assert "Amy: Hello there." in text          # filler dropped, capitalized
    assert "Bob: Hi." in text
    assert ("transcribe", 1, "Amy") in events and ("transcribe", 2, "Bob") in events
    assert events[-1][0] == "Merging tracks"
    assert not seen["work"].exists()            # per-run scratch removed
    assert sorted(p.name for p in out.iterdir()) == [
        "meeting.transcript.txt"]               # nothing else in the folder


@win_only
def test_run_job_pertrack_strips_an_inherited_token(job, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_inherited_from_the_parent")
    run, _out, _seen, tmp_path = job
    run("ok")
    log = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "fake_runner HF_TOKEN unset" in log


@win_only
def test_run_job_classified_error(job):
    run, *_ = job
    with pytest.raises(P.RunnerError) as e:
        run("error")
    assert e.value.code == "oom"


@win_only
def test_run_job_crash_reports_exit_code_and_tail(job):
    run, _out, seen, tmp_path = job
    with pytest.raises(RuntimeError) as e:
        run("crash")
    assert "exit code 3" in str(e.value) and "boom" in str(e.value)
    assert "boom" in (tmp_path / "run.log").read_text(encoding="utf-8")
    assert not seen["work"].exists()


@win_only
def test_run_job_cancel_mid_run(job):
    run, *_ = job
    sup = procs.Supervisor()
    outcome = {}

    def work():
        try:
            run("hang", supervisor=sup)
        except procs.Cancelled:
            outcome["cancelled"] = True

    t = threading.Thread(target=work)
    t.start()
    time.sleep(1.0)
    sup.cancel()
    t.join(20)
    assert outcome.get("cancelled")


@win_only
def test_run_job_mono_writes_diarized_transcript(tmp_path, monkeypatch):
    v, m, ff = _runtime(tmp_path)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    rec = tmp_path / "Recording.wav"
    rec.write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(P, "runner_cmd", lambda venv, mode, audio, work, *a:
                        [sys.executable, FAKE, "mono", str(work)])
    sup = procs.Supervisor()
    sup.begin()
    try:
        stats = P.run_job(mode="mono", audio=[rec], out_dir=out, venv_dir=v,
                          code_dir=tmp_path, models_root=m, ffmpeg=ff,
                          supervisor=sup, hf_token="hf_test",
                          hf_home=tmp_path / "hf",
                          log_path=tmp_path / "run.log",
                          progress_cb=lambda *a: None)
    finally:
        sup.end()
    log = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert f"fake_runner HF_TOKEN set HF_HOME {tmp_path / 'hf'}" in log
    text = Path(stats["output_path"]).read_text(encoding="utf-8")
    assert Path(stats["output_path"]).name == "Recording.transcript.txt"
    assert "Source: Recording.wav" in text           # real extension
    assert "SPEAKER_00: Morning all." in text
    assert "SPEAKER_01: Hi." in text
    assert stats["speakers"] == ["SPEAKER_00", "SPEAKER_01"]


def test_run_job_mono_takes_one_file(tmp_path):
    with pytest.raises(RuntimeError):
        P.run_job(mode="mono", audio=[tmp_path / "a", tmp_path / "b"],
                  out_dir=tmp_path, venv_dir=tmp_path, code_dir=tmp_path,
                  models_root=tmp_path, ffmpeg=None,
                  supervisor=procs.Supervisor(),
                  progress_cb=lambda *a: None)


def test_run_job_needs_audio(tmp_path):
    with pytest.raises(RuntimeError):
        P.run_job(mode="pertrack", audio=[], out_dir=tmp_path,
                  venv_dir=tmp_path, code_dir=tmp_path,
                  models_root=tmp_path, ffmpeg=None,
                  supervisor=procs.Supervisor(), progress_cb=lambda *a: None)
