"""Input detection and the mono/per-track run paths.

Import-safe: no side effects at import.
"""
from __future__ import annotations

from pathlib import Path

MODEL = "large-v3"               # accuracy ceiling within WhisperX; bump here only
COMPUTE_TYPE = "float16"
# Passed explicitly to model.transcribe(): without it WhisperX falls back to
# DataLoader batch_size=None (one VAD segment per forward pass) -- far slower,
# chunky bar, audible per-batch GPU coil whine. 16 is comfortable for large-v3
# on a 24GB GPU and faster than the old whisperx.exe CLI default of 8.
BATCH_SIZE = 16

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus"}
MEDIA_EXTS = AUDIO_EXTS | {".mp4", ".mkv", ".mov", ".webm"}


class PreflightError(Exception):
    """A required runtime prerequisite is missing."""


class PerTrackNotVerified(Exception):
    """Per-participant input detected but the timeline assumption is unverified.

    See the spec's Open items / lock-in. The per-track path stays gated until a
    real per-participant recording resolves the shared-timeline question.
    """


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


import json as _json
import os
import re
import shutil
import subprocess
import threading

from . import transcript as _T

# Windows: keep child consoles (the runner / the torch probe) from flashing
# their own black window. 0 on non-Windows where the flag does not exist.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def build_env(venv_dir: Path, base_env=None) -> dict:
    env = dict(os.environ if base_env is None else base_env)
    venv_dir = Path(venv_dir)
    sp = venv_dir / "Lib" / "site-packages" / "nvidia"
    cudnn = str(sp / "cudnn" / "bin")
    cublas = str(sp / "cublas" / "bin")
    # The frozen build manages its own ffmpeg next to venv\
    # (home/runtime/ffmpeg). Harmless in dev: that dir does not exist,
    # so system ffmpeg on PATH still resolves.
    ffmpeg = str(venv_dir.parent / "runtime" / "ffmpeg")
    env["PATH"] = os.pathsep.join(
        [cudnn, cublas, ffmpeg, env.get("PATH", "")])
    # The runner flushes each JSON line itself, but keep the child fully
    # unbuffered as defense against any library buffering stdout.
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _runner_cmd(venv_dir: Path, mode: str, audio, out_dir):
    py = Path(venv_dir) / "Scripts" / "python.exe"
    return [str(py), "-m", "backend.runner", "--mode", mode,
            "--audio", str(audio), "--out-dir", str(out_dir)]


def _run_one(*, mode, audio, out_dir, venv_dir, meta, progress_cb,
             cancel_event, register_proc):
    """Spawn backend.runner for ONE file and stream its JSON event protocol.

    Returns the produced JSON Path. Raises RuntimeError on runner-reported
    error / nonzero exit / cancel / missing output. `meta` is merged into
    every progress_cb call (carries track/tracks/name for per-track)."""
    venv_dir = Path(venv_dir)
    root = venv_dir.parent               # dir containing the `backend` package
    if not (venv_dir / "Scripts" / "python.exe").exists():
        raise RuntimeError(
            "The transcription environment isn't installed yet. Close and "
            "reopen Transcribe to run first-time setup. If this persists, "
            "delete the Transcribe folder in %LOCALAPPDATA% and reopen "
            "Transcribe.")
    proc = subprocess.Popen(
        _runner_cmd(venv_dir, mode, audio, out_dir),
        cwd=str(root), env=build_env(venv_dir),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, creationflags=_NO_WINDOW)
    register_proc(proc)

    def _cancelled():
        return cancel_event is not None and cancel_event.is_set()

    state = {"json": None, "error": None}

    def _drain_out(stream):
        for line in stream:
            if _cancelled():
                return
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
            except ValueError:
                continue                 # library noise on stdout; ignore
            ev = obj.get("ev")
            if ev == "phase":
                progress_cb(obj.get("phase"), "", {**meta, "pct": None})
            elif ev == "pct":
                progress_cb(obj.get("phase"), "",
                            {**meta, "pct": obj.get("pct")})
            elif ev == "done":
                state["json"] = obj.get("json")
            elif ev == "error":
                state["error"] = obj.get("msg") or "runner error"
        stream.close()

    def _drain_err(stream):
        for _ in stream:                 # drain so the pipe can't fill; noise
            if _cancelled():
                return
        stream.close()

    t_out = threading.Thread(target=_drain_out, args=(proc.stdout,),
                             daemon=True)
    t_err = threading.Thread(target=_drain_err, args=(proc.stderr,),
                             daemon=True)
    t_out.start()
    t_err.start()
    proc.wait()
    t_out.join(timeout=5)
    t_err.join(timeout=5)
    if _cancelled():
        raise RuntimeError("Cancelled.")
    if state["error"]:                   # prefer the runner's own message
        raise RuntimeError(state["error"])
    if proc.returncode != 0:
        raise RuntimeError(
            f"the transcription runner exited {proc.returncode}.")
    if not state["json"] or not Path(state["json"]).exists():
        raise RuntimeError("the transcription runner produced no JSON.")
    return Path(state["json"])


def preflight_mono(venv_dir: Path) -> None:
    if not os.environ.get("HF_TOKEN"):
        raise PreflightError(
            "HF_TOKEN is not set in this environment. Set it (setx HF_TOKEN "
            "...), open a new window, and relaunch. The token is never stored "
            "or shown by this app.")
    if shutil.which("ffmpeg") is None and not (
            Path(venv_dir).parent / "runtime" / "ffmpeg"
            / "ffmpeg.exe").exists():
        raise PreflightError(
            "ffmpeg is missing. It is normally installed automatically the "
            "first time you run Transcribe. To repair, delete the "
            "Transcribe folder in %LOCALAPPDATA% and reopen Transcribe to "
            "run setup again.")
    py = Path(venv_dir) / "Scripts" / "python.exe"
    probe = subprocess.run(
        [str(py), "-c", "import torch,sys;sys.exit(0 if "
         "torch.cuda.is_available() else 3)"],
        env=build_env(venv_dir), capture_output=True, text=True,
        creationflags=_NO_WINDOW)
    if probe.returncode != 0:
        raise PreflightError(
            "This app needs an NVIDIA GPU with a recent driver (CUDA 12.8 "
            "runtime). torch.cuda.is_available() returned False. Likely "
            "causes: no NVIDIA GPU in this machine, the GPU driver is too old "
            "for CUDA 12.8, or the environment is incomplete. Update your "
            "NVIDIA driver from nvidia.com. To rebuild the environment, "
            "delete the Transcribe folder in %LOCALAPPDATA% and reopen "
            "Transcribe to run setup again.")


def _drop_intermediates(out_dir: Path, stem: str) -> None:
    """The deliverable is one *.transcript.txt; drop WhisperX's own
    per-stem outputs so the folder isn't littered."""
    for ext in (".json", ".srt", ".vtt", ".tsv", ".txt"):
        try:
            (Path(out_dir) / f"{stem}{ext}").unlink()
        except OSError:
            pass


def run_mono(*, audio: Path, out_dir: Path, venv_dir: Path,
             progress_cb, cancel_event, register_proc):
    venv_dir = Path(venv_dir)
    out_dir = Path(out_dir)
    preflight_mono(venv_dir)
    jp = _run_one(mode="mono", audio=audio, out_dir=out_dir,
                  venv_dir=venv_dir, meta={}, progress_cb=progress_cb,
                  cancel_event=cancel_event, register_proc=register_proc)
    data = _json.loads(jp.read_text(encoding="utf-8"))
    words = _T.words_from_whisperx_json(data)
    stem = Path(audio).stem
    out_txt = out_dir / f"{stem}.transcript.txt"
    prior = out_txt.read_text(encoding="utf-8") if out_txt.exists() else None
    text, stats = _T.build_transcript(
        words, language=data.get("language", "?"),
        source_name=f"{stem}.m4a", mode="mono", prior_output=prior)
    out_txt.write_text(text, encoding="utf-8")
    _drop_intermediates(out_dir, stem)
    stats["output_path"] = str(out_txt)
    return stats


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


def preflight_pertrack(venv_dir: Path) -> None:
    # No HF_TOKEN: per-track does not diarize.
    if shutil.which("ffmpeg") is None and not (
            Path(venv_dir).parent / "runtime" / "ffmpeg"
            / "ffmpeg.exe").exists():
        raise PreflightError(
            "ffmpeg is missing. It is normally installed automatically the "
            "first time you run Transcribe. To repair, delete the "
            "Transcribe folder in %LOCALAPPDATA% and reopen Transcribe to "
            "run setup again.")
    py = Path(venv_dir) / "Scripts" / "python.exe"
    probe = subprocess.run(
        [str(py), "-c", "import torch,sys;sys.exit(0 if "
         "torch.cuda.is_available() else 3)"],
        env=build_env(venv_dir), capture_output=True, text=True,
        creationflags=_NO_WINDOW)
    if probe.returncode != 0:
        raise PreflightError(
            "This app needs an NVIDIA GPU with a recent driver (CUDA 12.8 "
            "runtime). torch.cuda.is_available() returned False. Likely "
            "causes: no NVIDIA GPU in this machine, the GPU driver is too old "
            "for CUDA 12.8, or the environment is incomplete. Update your "
            "NVIDIA driver from nvidia.com. To rebuild the environment, "
            "delete the Transcribe folder in %LOCALAPPDATA% and reopen "
            "Transcribe to run setup again.")


def run_pertrack(*, audio, out_dir, venv_dir,
                  progress_cb=lambda *a: None, cancel_event=None,
                  register_proc=lambda p: None):
    tracks = [Path(p) for p in (audio or [])]
    if not tracks:
        raise RuntimeError("No per-participant audio tracks were given.")
    venv_dir = Path(venv_dir)
    preflight_pertrack(venv_dir)
    out_dir = Path(out_dir)
    meeting_dir = tracks[0].parent.parent  # Audio Record -> meeting folder
    magic = derive_magic(meeting_dir)

    def _cancelled():
        return cancel_event is not None and cancel_event.is_set()

    track_words = []
    lang_seen = "?"
    total = len(tracks)
    for idx, tr in enumerate(tracks, 1):
        if _cancelled():
            raise RuntimeError("Cancelled.")
        speaker = speaker_name_from_track(tr.name, magic)
        meta = {"track": idx, "tracks": total, "name": speaker}
        jp = _run_one(mode="pertrack", audio=tr, out_dir=out_dir,
                      venv_dir=venv_dir, meta=meta, progress_cb=progress_cb,
                      cancel_event=cancel_event, register_proc=register_proc)
        data = _json.loads(jp.read_text(encoding="utf-8"))
        lang_seen = data.get("language", lang_seen)
        track_words.append((speaker, _T.words_from_whisperx_json(data)))
        _drop_intermediates(out_dir, tr.stem)

    progress_cb("Merging tracks", "",
                {"track": total, "tracks": total, "name": ""})
    turns = []
    for speaker, words in track_words:
        turns += _T.turns_from_track(words, speaker)
    turns.sort(key=lambda t: t["start"])
    source = meeting_dir.name
    out_txt = out_dir / f"{source}.transcript.txt"
    prior = out_txt.read_text(encoding="utf-8") if out_txt.exists() else None
    text, stats = _T.build_transcript_from_turns(
        turns, language=lang_seen, source_name=source,
        mode="pertrack", prior_output=prior)
    out_txt.write_text(text, encoding="utf-8")
    stats["output_path"] = str(out_txt)
    return stats


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
