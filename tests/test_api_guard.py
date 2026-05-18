"""Guard: backend/runner.py calls these WhisperX APIs directly. A deliberate
dependency bump (see requirements.txt) that drifts any of them must fail
HERE, fast, with a pointer, instead of during a real transcription.
"""
import inspect

import pytest

whisperx = pytest.importorskip("whisperx")

POINTER = ("A pinned WhisperX-stack API drifted. backend/runner.py relies "
           "on it. Update runner.py to the new signature, re-pin in "
           "requirements.txt, then re-run on a real GPU. See the plan.")


def _has_param(func, name):
    return name in inspect.signature(func).parameters


def test_whisperx_top_level_exports_exist():
    for n in ("load_model", "load_align_model", "align",
              "assign_word_speakers", "load_audio"):
        assert callable(getattr(whisperx, n, None)), f"{n}: {POINTER}"


def test_transcribe_accepts_progress_callback():
    import whisperx.asr as asr
    assert _has_param(
        asr.FasterWhisperPipeline.transcribe, "progress_callback"), POINTER


def test_align_accepts_progress_callback():
    import whisperx.alignment as alignment
    assert _has_param(alignment.align, "progress_callback"), POINTER


def test_diarization_call_accepts_progress_callback():
    from whisperx.diarize import DiarizationPipeline
    assert _has_param(
        DiarizationPipeline.__call__, "progress_callback"), POINTER
