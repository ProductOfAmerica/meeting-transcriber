"""Runs WhisperX as a library and emits a structured progress stream.

Spawned as a subprocess by backend.pipeline, one audio file per call:

    python -m backend.runner --mode {mono|pertrack} --audio <p> --out-dir <d>

Protocol: one compact JSON object per stdout line (flushed):
  {"ev":"phase","phase":<P>}            phase start (indeterminate)
  {"ev":"pct","phase":<P>,"pct":<f>}    determinate phases only, throttled
  {"ev":"done","json":"<abs path>"}
  {"ev":"error","msg":"<text>"}

Determinate phases (carry pct): transcribe, align, diarize. The rest
(load_model, vad, load_align, load_diarize, write) have no usable hook in
WhisperX 3.8.6 and stay indeterminate -- that is honest, not lazy.

whisperx/torch are imported lazily inside `_run`, so this module imports
(for arg/emit/json/error unit tests) without a GPU or heavy deps.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

# Canonical phase vocabulary -- shared contract with ui/app.js.
PHASES = ("load_model", "vad", "transcribe", "load_align", "align",
          "load_diarize", "diarize", "write")
DETERMINATE = frozenset({"transcribe", "align", "diarize"})


def emit(obj) -> None:
    try:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()
    except OSError:
        pass  # parent killed us mid-write (cancel); nothing to do


def emit_phase(phase: str) -> None:
    emit({"ev": "phase", "phase": phase})


def make_pct_emitter(phase: str, announce: bool = False):
    """progress_callback(float 0-100) -> throttled {"ev":"pct"} events.

    Emits only when the rounded integer changes. If `announce`, the first
    call also emits the phase (used for transcribe: VAD runs first inside
    transcribe() with no hook, so we surface "transcribe" only once real
    ASR progress starts)."""
    last = [-1]
    started = [False]

    def cb(p):
        if announce and not started[0]:
            started[0] = True
            emit_phase(phase)
        try:
            v = float(p)
        except (TypeError, ValueError):
            return
        if not math.isfinite(v):       # whisperx could hand us NaN/inf
            return
        r = round(v)
        if r != last[0]:
            last[0] = r
            emit({"ev": "pct", "phase": phase, "pct": v})

    return cb


def gate_hint(exc) -> str:
    """Friendly message when the pyannote HF gate rejects us; else str(exc)."""
    s = str(exc)
    low = s.lower()
    if any(k in low for k in ("401", "403", "gated", "unauthorized",
                              "forbidden", "pyannote")):
        return ("Hugging Face rejected the diarization model. Set a valid "
                "HF_TOKEN and accept the gate at huggingface.co/pyannote/"
                "speaker-diarization-community-1 with that token's account. "
                "(" + s + ")")
    return s


def build_result_json(result, language: str) -> dict:
    """Shape consumed by backend.transcript.words_from_whisperx_json:
    {segments, word_segments, language}. `result` is what align() /
    assign_word_speakers() returned (already carries both lists)."""
    return {
        "segments": result.get("segments", []),
        "word_segments": result.get("word_segments", []),
        "language": language,
    }


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="backend.runner")
    ap.add_argument("--mode", required=True, choices=("mono", "pertrack"))
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out-dir", required=True)
    return ap.parse_args(argv)


def _run(mode: str, audio: str, out_dir: str) -> None:
    # Heavy imports kept here so the module stays unit-testable without a GPU.
    import whisperx
    from .pipeline import MODEL, COMPUTE_TYPE, BATCH_SIZE

    device = "cuda"
    audio_path = Path(audio)
    out_dir = Path(out_dir)

    emit_phase("load_model")
    audio_data = whisperx.load_audio(str(audio_path))
    model = whisperx.load_model(MODEL, device, compute_type=COMPUTE_TYPE)

    emit_phase("vad")  # VAD runs inside transcribe(); no hook -> indeterminate
    result = model.transcribe(
        audio_data, batch_size=BATCH_SIZE,
        progress_callback=make_pct_emitter("transcribe", announce=True))
    language = result.get("language") or "en"

    emit_phase("load_align")
    align_model, align_meta = whisperx.load_align_model(language, device)
    emit_phase("align")
    result = whisperx.align(
        result["segments"], align_model, align_meta, audio_data, device,
        progress_callback=make_pct_emitter("align"))

    if mode == "mono":
        emit_phase("load_diarize")
        from whisperx.diarize import DiarizationPipeline
        dia = DiarizationPipeline(
            token=os.environ.get("HF_TOKEN"), device=device)
        emit_phase("diarize")
        # No min/max speakers: the app dropped speaker-count input long ago,
        # so pyannote auto-detects (matches the prior stripped behavior).
        diarize_df = dia(
            audio_data, progress_callback=make_pct_emitter("diarize"))
        result = whisperx.assign_word_speakers(diarize_df, result)

    emit_phase("write")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / (audio_path.stem + ".json")
    json_path.write_text(
        json.dumps(build_result_json(result, language)), encoding="utf-8")
    emit({"ev": "done", "json": str(json_path)})


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        _run(args.mode, args.audio, args.out_dir)
    except Exception as exc:  # surface everything to the UI as one error
        emit({"ev": "error", "msg": gate_hint(exc)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
