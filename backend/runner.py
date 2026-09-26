"""Transcribes audio with NVIDIA Parakeet TDT 0.6B v2 (onnx-asr on ONNX
Runtime, CUDA); for a mixed recording, labels speakers with pyannote. Emits
a structured progress stream.

Spawned by backend.pipeline, one process per job:

    python -m backend.runner --mode {pertrack|mono} --audio A [--audio B ...]
        --out-dir T --asr-model-dir D --vad-model-dir D [--ffmpeg F]

pertrack takes one --audio per participant; mono takes exactly one.

Protocol: one compact JSON object per stdout line (flushed):
  {"ev":"phase","phase":P}              load_model | transcribe |
                                        load_diarize | diarize
  {"ev":"pct","phase":P,"pct":f}        0-100, throttled to integer steps
  {"ev":"input","i":k,"n":N}            starting input k of N (1-based)
  {"ev":"result","i":k,"json":path}     words for input k written to path
  {"ev":"done"}
  {"ev":"error","code":C,"msg":text}    C: no_cuda | hf_gate | oom | other

Result JSON: {"words": [{"word", "start", "end"[, "speaker"]}, ...],
"duration": seconds}. Words carry a speaker only in mono.

Import-safe: numpy, onnxruntime, onnx_asr, torch and pyannote load inside
functions, so the protocol and the pure helpers are unit-testable without a
GPU.
"""
from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

from .models import DIARIZATION_MODEL

MODEL_NAME = "nemo-parakeet-tdt-0.6b-v2"
SAMPLE_RATE = 16000

# Voice activity detection feeding the model. onnx-asr's defaults (30 ms pad,
# split at every 100 ms pause) cut speech off at segment edges: on a real
# 35-minute two-track meeting (measured 2026-09-25) they dropped audio holding
# 68 words WhisperX transcribed; these values dropped audio holding 12, with
# about 3.8 GB peak GPU memory at batch 2. min_silence_duration_ms must stay
# below transcript.GAP_SEC so per-track turns still split at VAD gaps.
VAD_OPTIONS = {"speech_pad_ms": 200, "min_silence_duration_ms": 500,
               "max_speech_duration_s": 30, "batch_size": 2}

# onnx-asr gives each token's start time only. A word ends at its last token's
# start plus this slack (the 75th percentile of the gap to WhisperX-aligned
# word ends on the same meeting, 4,355 matched words), capped by the next
# word's start and the segment end.
WORD_END_SLACK = 0.18
# How far outside a forced-cut overlap to look for repeated words (seconds).
_OVERLAP_SLOP = 0.5

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class NoCuda(Exception):
    """ONNX Runtime could not run the speech model on the GPU."""


class HfGate(Exception):
    """Hugging Face refused the gated diarization model."""


def gate_error(exc) -> bool:
    """True if exc (or what it wraps) is Hugging Face refusing access."""
    try:
        from huggingface_hub.errors import (GatedRepoError, HfHubHTTPError,
                                            RepositoryNotFoundError)
    except ImportError:
        return False
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (GatedRepoError, RepositoryNotFoundError)):
            return True
        if isinstance(exc, HfHubHTTPError):
            status = getattr(getattr(exc, "response", None), "status_code",
                             None)
            if status in (401, 403):
                return True
        exc = exc.__cause__ or exc.__context__
    return False


def emit(obj) -> None:
    try:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()
    except OSError:
        pass  # parent is gone (cancel or crash); the job object ends us next


def emit_phase(phase: str) -> None:
    emit({"ev": "phase", "phase": phase})


def make_pct_emitter(phase: str):
    """cb(float 0-100) -> {"ev":"pct"} events, one per rounded integer."""
    last = [-1]

    def cb(p):
        try:
            v = float(p)
        except (TypeError, ValueError):
            return
        if not math.isfinite(v):
            return
        r = round(v)
        if r != last[0]:
            last[0] = r
            emit({"ev": "pct", "phase": phase, "pct": v})

    return cb


def ffmpeg_cmd(ffmpeg, path) -> list:
    """Decode any media file to 16 kHz mono signed 16-bit PCM on stdout."""
    return [str(ffmpeg), "-nostdin", "-threads", "0", "-i", str(path),
            "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le",
            "-ar", str(SAMPLE_RATE), "-"]


def load_audio(ffmpeg, path):
    import numpy as np
    proc = subprocess.run(ffmpeg_cmd(ffmpeg, path), capture_output=True,
                          creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"ffmpeg could not decode {Path(path).name}: "
                           + (detail.splitlines()[-1] if detail else
                              f"exit code {proc.returncode}"))
    return np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768.0


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", word.lower())


def words_from_segments(segments) -> list:
    """Words with absolute times from onnx-asr timestamped VAD segments.

    Each segment has start, end, tokens, and timestamps (token start times
    relative to the segment). A token that begins with a space starts a new
    word. onnx-asr splits speech longer than max_speech_duration_s with a
    padded overlap, so both chunks can transcribe the words at the cut, with
    slightly different times. The words at the start of the later chunk that
    repeat the end of the earlier one (by text) are dropped.
    """
    words = []
    prev_end = None
    for seg in segments:
        start, end = float(seg.start), float(seg.end)
        seg_words = []
        for tok, t in zip(seg.tokens or (), seg.timestamps or ()):
            at = start + float(t)
            if not seg_words or tok.startswith(" "):
                seg_words.append({"word": tok.strip(), "start": at, "last": at})
            else:
                seg_words[-1]["word"] += tok
                seg_words[-1]["last"] = at
        seg_words = [w for w in seg_words if _norm(w["word"])]
        if prev_end is not None and start < prev_end:     # forced-cut overlap
            tail = [_norm(w["word"]) for w in words
                    if w["start"] >= start - _OVERLAP_SLOP]
            head = [_norm(w["word"]) for w in seg_words
                    if w["start"] <= prev_end + _OVERLAP_SLOP]
            k = next((k for k in range(min(len(tail), len(head)), 0, -1)
                      if tail[-k:] == head[:k]), 0)
            seg_words = seg_words[k:]
        for i, w in enumerate(seg_words):
            nxt = seg_words[i + 1]["start"] if i + 1 < len(seg_words) else end
            words.append({"word": w["word"], "start": round(w["start"], 3),
                          "end": round(min(nxt, w["last"] + WORD_END_SLACK,
                                           end), 3)})
        prev_end = end
    words.sort(key=lambda w: w["start"])
    return words


def classify(exc) -> tuple:
    """(code, user-facing message) for a failure."""
    text = str(exc)
    if isinstance(exc, NoCuda):
        return "no_cuda", (
            "This app needs an NVIDIA GPU, and the speech model could not "
            "start on it. Update your NVIDIA driver from nvidia.com and try "
            "again. (" + text + ")")
    if isinstance(exc, HfGate):
        return "hf_gate", (
            "Hugging Face refused the speaker-detection model. On the "
            "recording card, check the Hugging Face token (Change) and that "
            "its account accepted the conditions at huggingface.co/"
            + DIARIZATION_MODEL + ". (" + text + ")")
    low = text.lower()
    if ("outofmemory" in type(exc).__name__.lower() or "out of memory" in low
            or "failed to allocate memory" in low):
        return "oom", (
            "The GPU ran out of memory. Close other programs that use the "
            "GPU and try again. (" + text + ")")
    return "other", f"{type(exc).__name__}: {text}"


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="backend.runner")
    ap.add_argument("--mode", required=True, choices=("pertrack", "mono"))
    ap.add_argument("--audio", required=True, action="append")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--asr-model-dir", required=True)
    ap.add_argument("--vad-model-dir", required=True)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    return ap.parse_args(argv)


def _torch_lib() -> Path:
    spec = importlib.util.find_spec("torch")
    if spec is None or not spec.submodule_search_locations:
        raise NoCuda("PyTorch, which supplies the CUDA libraries, is missing "
                     "from the transcription environment")
    return Path(list(spec.submodule_search_locations)[0]) / "lib"


def ort_sessions(obj, _depth=0, _seen=None) -> list:
    """Every onnxruntime.InferenceSession reachable from an onnx-asr object."""
    import onnxruntime as ort
    seen = set() if _seen is None else _seen
    if id(obj) in seen or _depth > 5:
        return []
    seen.add(id(obj))
    if isinstance(obj, ort.InferenceSession):
        return [obj]
    if isinstance(obj, dict):
        children = list(obj.values())
    elif isinstance(obj, (list, tuple)):
        children = list(obj)
    elif hasattr(obj, "__dict__"):
        children = list(vars(obj).values())
    else:
        children = []
    found = []
    for child in children:
        found += ort_sessions(child, _depth + 1, seen)
    return found


def _load_asr(asr_dir: Path, vad_dir: Path):
    import onnxruntime as ort
    ort.set_default_logger_severity(3)   # warnings arrive as UTF-16 noise
    # CUDA and cuDNN come from torch's lib folder (see requirements.in).
    # preload_dlls loads the CUDA runtime and cuDNN; cuDNN later looks up
    # NVRTC (also in that folder) by name, so the folder goes on PATH too.
    lib = _torch_lib()
    os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
    ort.preload_dlls(directory=str(lib))
    import onnx_asr
    # onnx-asr loads an existing model folder without touching the network.
    model = onnx_asr.load_model(MODEL_NAME, asr_dir,
                                providers=["CUDAExecutionProvider"])
    sessions = ort_sessions(model)
    # ORT always appends the CPU provider; if CUDA failed to start it is
    # missing from the front of the list instead of raising.
    if not sessions or any(s.get_providers()[0] != "CUDAExecutionProvider"
                           for s in sessions):
        raise NoCuda("ONNX Runtime could not start CUDA")
    vad = onnx_asr.load_vad("silero", vad_dir,
                            providers=["CPUExecutionProvider"])
    return model.with_vad(vad, **VAD_OPTIONS).with_timestamps()


def _diarize(audio) -> list:
    """(start, end, speaker) turns, one speaker at a time, from pyannote."""
    import torch
    from pyannote.audio import Pipeline
    try:
        pipeline = Pipeline.from_pretrained(
            DIARIZATION_MODEL, token=os.environ.get("HF_TOKEN"))
    except Exception as exc:
        if gate_error(exc):
            raise HfGate(str(exc)) from exc
        raise
    if pipeline is None:
        raise HfGate(f"could not load {DIARIZATION_MODEL}")
    pipeline.to(torch.device("cuda"))
    emit_phase("diarize")
    pct = make_pct_emitter("diarize")

    def hook(step_name, step_artifact=None, file=None, total=None,
             completed=None):
        # "embeddings" is the long step that reports completed/total.
        if step_name == "embeddings" and total:
            pct(100.0 * (completed or 0) / total)

    output = pipeline({"waveform": torch.from_numpy(audio)[None],
                       "sample_rate": SAMPLE_RATE}, hook=hook)
    return [(seg.start, seg.end, speaker) for seg, _, speaker in
            output.exclusive_speaker_diarization.itertracks(yield_label=True)]


def _transcribe(asr, audio, duration) -> list:
    emit_phase("transcribe")
    pct = make_pct_emitter("transcribe")
    segments = []
    for seg in asr.recognize(audio, sample_rate=SAMPLE_RATE):
        segments.append(seg)
        if duration:
            pct(min(100.0, 100.0 * seg.end / duration))
    return words_from_segments(segments)


def _run(args) -> None:
    if args.mode == "mono" and len(args.audio) != 1:
        raise ValueError("mono takes exactly one --audio")
    emit_phase("load_model")
    asr = _load_asr(Path(args.asr_model_dir), Path(args.vad_model_dir))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(args.audio)
    for i, path in enumerate(args.audio, 1):
        emit({"ev": "input", "i": i, "n": n})
        audio = load_audio(args.ffmpeg, path)
        duration = len(audio) / SAMPLE_RATE
        words = _transcribe(asr, audio, duration)
        if args.mode == "mono":
            del asr                     # free the GPU before diarization
            gc.collect()
            emit_phase("load_diarize")
            from .transcript import assign_speakers
            words = assign_speakers(words, _diarize(audio))
        result = {"words": words, "duration": round(duration, 3)}
        js = out_dir / f"{i:03d}.json"
        js.write_text(json.dumps(result), encoding="utf-8")
        emit({"ev": "result", "i": i, "json": str(js)})
    emit({"ev": "done"})


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        _run(args)
    except Exception as exc:  # surface everything to the UI as one error
        code, msg = classify(exc)
        emit({"ev": "error", "code": code, "msg": msg})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
