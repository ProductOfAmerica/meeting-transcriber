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
    """The input is always a picked file (pywebview's folder dialog is
    unusable on Windows). If it sits in a meeting folder with an Audio Record
    set, or inside that set, the job is per-track."""
    path = Path(path)
    if not path.is_file():
        return {"mode": "ask", "audio": [], "reason": "not a file"}
    if path.suffix.lower() not in MEDIA_EXTS:
        return {"mode": "ask", "audio": [],
                "reason": f"unsupported file type {path.suffix}"}
    parent = path.parent
    meeting_dir = (parent.parent
                   if parent.name.strip().lower() == "audio record"
                   else parent)
    tracks = _find_audio_record(meeting_dir)
    if tracks:
        return {"mode": "pertrack", "audio": tracks,
                "reason": "per-participant recording (Audio Record found "
                          "via the chosen file's folder)"}
    return {"mode": "mono", "audio": [path], "reason": "single media file"}


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


def track_names(tracks, magic=None) -> list:
    """Speaker labels for per-participant tracks, one per track and all
    different: a second track named Alice becomes "Alice (2)"."""
    names = []
    for track in tracks:
        base = speaker_name_from_track(Path(track).name, magic)
        name, n = base, 1
        while name in names:
            n += 1
            name = f"{base} ({n})"
        names.append(name)
    return names


def write_transcript(out_dir, name, text) -> Path:
    """Write <name>.transcript.txt in out_dir, or "<name> (2).transcript.txt"
    and so on: never over an existing file."""
    n = 1
    while True:
        suffix = "" if n == 1 else f" ({n})"
        path = Path(out_dir) / f"{name}{suffix}.transcript.txt"
        try:
            with open(path, "x", encoding="utf-8") as fh:
                fh.write(text)
            return path
        except FileExistsError:
            n += 1


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


def preflight(mode, venv_dir, models_root, ffmpeg, hf_token=None) -> None:
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
            "run setup again. (From source: python -m backend.devsetup)")
    if not models.present(models_root):
        raise PreflightError(
            "The speech model files are missing. Close and reopen Transcribe "
            "to run setup again. (From source: python -m backend.devsetup)")
    if mode == "mono" and not hf_token:
        raise PreflightError(
            "Mixed recordings need a Hugging Face token for speaker "
            "detection. Add one on the recording card, then try again.")


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
        info["speakers"] = track_names(det["audio"], derive_magic(meeting))
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


def run_job(*, mode, audio, out_dir, venv_dir, code_dir, models_root, ffmpeg,
            supervisor: procs.Supervisor, progress_cb, log_path=None,
            hf_token=None, hf_home=None) -> dict:
    """Transcribe with one runner process for the whole job, then write the
    transcript into out_dir. code_dir holds the backend package the runner
    imports; hf_token is needed for mono only; hf_home is where the runner's
    Hugging Face downloads go. Raises procs.Cancelled, PreflightError,
    RunnerError, or RuntimeError. Returns stats with output_path."""
    tracks = [Path(p) for p in (audio or [])]
    if not tracks:
        raise RuntimeError("No audio files were given.")
    if mode == "mono" and len(tracks) != 1:
        raise RuntimeError("A mixed recording is exactly one file.")
    preflight(mode, venv_dir, models_root, ffmpeg, hf_token)
    env = build_env()
    env.pop("HF_TOKEN", None)           # only the token passed in, only for mono
    if mode == "mono":
        env["HF_TOKEN"] = hf_token
    if hf_home is not None:
        env["HF_HOME"] = str(hf_home)
    out_dir = Path(out_dir)
    names = []
    if mode == "pertrack":
        meeting_dir = tracks[0].parent.parent   # Audio Record -> meeting folder
        names = track_names(tracks, derive_magic(meeting_dir))

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
            cwd=Path(code_dir), env=env, on_stdout=on_stdout, sink=sink)
        if state["error"]:
            raise RunnerError(*state["error"])
        if (rc != 0 or not state["done"]
                or sorted(state["results"]) != list(range(1, len(tracks) + 1))):
            raise RuntimeError(
                f"The speech engine stopped unexpectedly (exit code {rc}).\n\n"
                "Last output:\n" + (sink.tail() or "(none)"))
        results = [json.loads(Path(state["results"][i]).read_text(
            encoding="utf-8")) for i in range(1, len(tracks) + 1)]
    finally:
        sink.close()
        shutil.rmtree(work, ignore_errors=True)
    per_track = [_T.words_from_result(r) for r in results]
    # Per-participant tracks share one timeline: the longest is the meeting.
    duration = max(r.get("duration") or 0 for r in results) or None

    if mode == "mono":
        text, stats = _T.build_transcript(
            per_track[0], language="en", source_name=tracks[0].name,
            mode="mono", duration=duration)
        stats["output_path"] = str(write_transcript(out_dir, tracks[0].stem,
                                                    text))
        return stats

    progress_cb("Merging tracks", "",
                {"track": len(tracks), "tracks": len(tracks), "name": ""})
    turns = []
    for speaker, words in zip(names, per_track):
        turns += _T.turns_from_track(words, speaker)
    turns.sort(key=lambda t: t["start"])
    source = meeting_dir.name
    text, stats = _T.build_transcript_from_turns(
        turns, language="en", source_name=source, mode="pertrack",
        duration=duration)
    stats["output_path"] = str(write_transcript(out_dir, source, text))
    return stats
