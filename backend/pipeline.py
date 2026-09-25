"""Input detection and the transcription job.

Import-safe: no side effects at import.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from . import models
from . import procs
from . import transcript as _T

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus"}
MEDIA_EXTS = AUDIO_EXTS | {".mp4", ".mkv", ".mov", ".webm"}


class PreflightError(Exception):
    """A required runtime prerequisite is missing."""


class PerTrackNotVerified(Exception):
    """Per-participant input detected but the timeline assumption is unverified.

    See the spec's Open items / lock-in. The per-track path stays gated until a
    real per-participant recording resolves the shared-timeline question.
    """


class RunnerError(RuntimeError):
    """The runner reported a classified failure (code: no_cuda, hf_gate,
    oom, other)."""

    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code


def _find_audio_record(folder: Path):
    for child in folder.iterdir():
        if child.is_dir() and child.name.strip().lower() == "audio record":
            tracks = sorted(p for p in child.iterdir()
                            if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
            if tracks:
                return tracks
    return None


def detect_input(path: Path) -> dict:
    path = Path(path)
    if path.is_file():
        if path.suffix.lower() not in MEDIA_EXTS:
            return {"mode": "ask", "audio": [], "video": None,
                    "reason": f"unsupported file type {path.suffix}"}
        # Input is always a file (pywebview's folder dialog is unusable on
        # Windows). Resolve the meeting folder from the picked file: if it
        # sits in (or is) an Audio Record set, route to per-track.
        parent = path.parent
        meeting_dir = (parent.parent
                       if parent.name.strip().lower() == "audio record"
                       else parent)
        tracks = _find_audio_record(meeting_dir)
        if tracks:
            return {"mode": "pertrack", "audio": tracks, "video": None,
                    "reason": "per-participant recording (Audio Record found "
                              "via the chosen file's folder)"}
        return {"mode": "mono", "audio": [path], "video": None,
                "reason": "single media file"}

    if not path.is_dir():
        return {"mode": "ask", "audio": [], "video": None,
                "reason": "path is neither a file nor a folder"}

    tracks = _find_audio_record(path)
    if tracks:
        return {"mode": "pertrack", "audio": tracks, "video": None,
                "reason": "Audio Record subfolder with per-participant tracks"}

    audios = sorted(p for p in path.iterdir()
                    if p.is_file() and p.suffix.lower() in AUDIO_EXTS
                    and not p.name.lower().endswith(".transcript.txt"))
    videos = sorted(p for p in path.iterdir()
                    if p.is_file() and p.suffix.lower() == ".mp4")
    if len(audios) == 1:
        return {"mode": "mono", "audio": [audios[0]],
                "video": videos[0] if videos else None,
                "reason": "single mixed audio in meeting folder"}
    return {"mode": "ask", "audio": audios, "video": videos[0] if videos else None,
            "reason": "ambiguous or unrecognized layout"}


def build_env(base_env=None) -> dict:
    """Environment for the runner process."""
    env = dict(os.environ if base_env is None else base_env)
    # The runner flushes each JSON line itself, but keep the child fully
    # unbuffered as defense against any library buffering stdout.
    env["PYTHONUNBUFFERED"] = "1"
    # Child output is read as UTF-8; make the child write UTF-8 too.
    env["PYTHONUTF8"] = "1"
    # Local-only app: no usage metrics from the libraries.
    env["PYANNOTE_METRICS_ENABLED"] = "0"
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return env


# Lock-in resolved (real Zoom per-participant recording, 2026-05-16): every
# per-participant track shares one timeline (start_time 0, identical duration),
# so per-word timestamps merge directly. recording.conf does NOT list the
# per-participant tracks, so detection stays filesystem-based. Filenames are
# audio<DisplayName><index><magic>.m4a; the mixed file is audio<magic>.m4a.

_MIXED_RE = re.compile(r"^audio(\d+)$", re.I)


def derive_magic(folder: Path):
    """Magic-number digits from the top-level mixed `audio<magic>.m4a`."""
    folder = Path(folder)
    cands = []
    try:
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                m = _MIXED_RE.match(p.stem)
                if m:
                    cands.append(m.group(1))
    except OSError:
        return None
    cands = sorted(set(cands), key=len, reverse=True)
    return cands[0] if cands else None


def speaker_name_from_track(filename, magic=None) -> str:
    """audioAlice11721414883.m4a + magic 1721414883 -> 'Alice' (best-effort)."""
    stem = Path(filename).stem
    s = stem
    if s.lower().startswith("audio"):
        s = s[5:]
    if magic and s.endswith(magic):
        s = s[: -len(magic)]
    s = s.rstrip("0123456789").strip()  # drop the participant index
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = re.sub(r",(?=\S)", ", ", s)
    return s or stem


def runner_cmd(venv_dir, mode, audio, out_dir, models_root, ffmpeg) -> list:
    py = Path(venv_dir) / "Scripts" / "python.exe"
    cmd = [str(py), "-m", "backend.runner", "--mode", mode,
           "--out-dir", str(out_dir),
           "--asr-model-dir", str(models.PARAKEET.dir(models_root)),
           "--vad-model-dir", str(models.SILERO.dir(models_root)),
           "--ffmpeg", str(ffmpeg)]
    for a in audio:
        cmd += ["--audio", str(a)]
    return cmd


def preflight(mode, venv_dir, models_root, ffmpeg) -> None:
    if not (Path(venv_dir) / "Scripts" / "python.exe").exists():
        raise PreflightError(
            "The transcription environment isn't installed yet. Close and "
            "reopen Transcribe to run first-time setup. If this persists, "
            "delete the Transcribe folder in %LOCALAPPDATA% and reopen "
            "Transcribe.")
    if not ffmpeg or not Path(ffmpeg).exists():
        raise PreflightError(
            "ffmpeg is missing. It is normally installed automatically the "
            "first time you run Transcribe. To repair, delete the "
            "Transcribe folder in %LOCALAPPDATA% and reopen Transcribe to "
            "run setup again.")
    if not models.present(models_root):
        raise PreflightError(
            "The speech model files are missing. Close and reopen Transcribe "
            "to run setup again. (Developers: python -m backend.models)")
    if mode == "mono" and not os.environ.get("HF_TOKEN"):
        raise PreflightError(
            "Mixed recordings need a Hugging Face token for speaker "
            "detection, and HF_TOKEN is not set in this environment. Set it "
            "(setx HF_TOKEN ...), open a new window, and relaunch. The token "
            "is never stored or shown by this app.")


def summarize_input(path) -> dict:
    """Human-facing summary of a picked file for the 'ready' card.

    Returns mode, audio, reason plus: title (meeting folder name for
    per-track, else the file name), speakers (parsed names, per-track only),
    and needs_token (True only for the mono diarization path).
    """
    path = Path(path)
    det = detect_input(path)
    info = {"mode": det["mode"], "audio": det["audio"],
            "reason": det["reason"], "speakers": [],
            "needs_token": False, "title": path.name}
    if det["mode"] == "pertrack" and det["audio"]:
        meeting = det["audio"][0].parent.parent
        magic = derive_magic(meeting)
        info["speakers"] = [speaker_name_from_track(p.name, magic)
                            for p in det["audio"]]
        info["title"] = meeting.name
    elif det["mode"] == "mono":
        info["needs_token"] = True   # mono path diarizes -> HF token needed
    return info


def _event(line):
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) and "ev" in obj else None


def run_job(*, mode, audio, out_dir, venv_dir, models_root, ffmpeg,
            supervisor: procs.Supervisor, progress_cb, log_path=None) -> dict:
    """Transcribe with one runner process for the whole job, then write the
    transcript into out_dir. Raises procs.Cancelled, PreflightError,
    RunnerError, or RuntimeError. Returns stats with output_path."""
    tracks = [Path(p) for p in (audio or [])]
    if not tracks:
        raise RuntimeError("No audio files were given.")
    if mode == "mono" and len(tracks) != 1:
        raise RuntimeError("A mixed recording is exactly one file.")
    preflight(mode, venv_dir, models_root, ffmpeg)
    out_dir = Path(out_dir)
    names = []
    if mode == "pertrack":
        meeting_dir = tracks[0].parent.parent   # Audio Record -> meeting folder
        magic = derive_magic(meeting_dir)
        names = [speaker_name_from_track(t.name, magic) for t in tracks]

    sink = procs.LineSink(log_path)
    state = {"meta": {}, "error": None, "done": False, "results": {}}

    def on_stdout(line):
        obj = _event(line)
        if obj is None:
            sink.note(line)
            return
        ev = obj["ev"]
        if ev == "input":
            i = int(obj.get("i", 0))
            state["meta"] = {"track": i, "tracks": obj.get("n"),
                             "name": names[i - 1] if 0 < i <= len(names)
                             else ""}
        elif ev == "phase":
            progress_cb(obj.get("phase"), "", {**state["meta"], "pct": None})
        elif ev == "pct":
            progress_cb(obj.get("phase"), "",
                        {**state["meta"], "pct": obj.get("pct")})
        elif ev == "result":
            state["results"][int(obj.get("i", 0))] = obj.get("json")
        elif ev == "done":
            state["done"] = True
        elif ev == "error":
            state["error"] = (obj.get("code") or "other",
                              obj.get("msg") or "The speech engine failed.")

    work = Path(tempfile.mkdtemp(prefix="transcribe-"))
    try:
        rc = supervisor.run(
            runner_cmd(venv_dir, mode, tracks, work, models_root, ffmpeg),
            cwd=Path(venv_dir).parent, env=build_env(), on_stdout=on_stdout,
            sink=sink)
        if state["error"]:
            raise RunnerError(*state["error"])
        if (rc != 0 or not state["done"]
                or sorted(state["results"]) != list(range(1, len(tracks) + 1))):
            raise RuntimeError(
                f"The speech engine stopped unexpectedly (exit code {rc}).\n\n"
                "Last output:\n" + (sink.tail() or "(none)"))
        per_track = [_T.words_from_result(json.loads(
            Path(state["results"][i]).read_text(encoding="utf-8")))
            for i in range(1, len(tracks) + 1)]
    finally:
        sink.close()
        shutil.rmtree(work, ignore_errors=True)

    if mode == "mono":
        out_txt = out_dir / f"{tracks[0].stem}.transcript.txt"
        prior = (out_txt.read_text(encoding="utf-8") if out_txt.exists()
                 else None)
        text, stats = _T.build_transcript(
            per_track[0], language="en", source_name=tracks[0].name,
            mode="mono", prior_output=prior)
        out_txt.write_text(text, encoding="utf-8")
        stats["output_path"] = str(out_txt)
        return stats

    progress_cb("Merging tracks", "",
                {"track": len(tracks), "tracks": len(tracks), "name": ""})
    turns = []
    for speaker, words in zip(names, per_track):
        turns += _T.turns_from_track(words, speaker)
    turns.sort(key=lambda t: t["start"])
    source = meeting_dir.name
    out_txt = out_dir / f"{source}.transcript.txt"
    prior = out_txt.read_text(encoding="utf-8") if out_txt.exists() else None
    text, stats = _T.build_transcript_from_turns(
        turns, language="en", source_name=source,
        mode="pertrack", prior_output=prior)
    out_txt.write_text(text, encoding="utf-8")
    stats["output_path"] = str(out_txt)
    return stats
