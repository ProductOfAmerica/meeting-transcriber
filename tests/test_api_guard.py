"""Guard: backend/runner.py calls these onnx-asr / ONNX Runtime APIs directly.
A dependency bump (see requirements.txt) that drifts any of them must fail
HERE, fast, with a pointer, instead of during a real transcription.
Skipped in environments without the runtime stack (e.g. the build venv).
"""
import inspect
import typing

import pytest

onnx_asr = pytest.importorskip("onnx_asr")
ort = pytest.importorskip("onnxruntime")

from backend import runner  # noqa: E402

POINTER = ("A pinned runtime API drifted. backend/runner.py relies on it. "
           "Update runner.py, re-pin in requirements.txt, then re-run on a "
           "real GPU.")


def test_load_functions_take_a_local_path_and_providers():
    for fn in (onnx_asr.load_model, onnx_asr.load_vad):
        params = inspect.signature(fn).parameters
        assert {"path", "providers"} <= set(params), f"{fn.__name__}: {POINTER}"


def test_vad_and_timestamps_adapters_exist():
    from onnx_asr import adapters
    assert hasattr(adapters.TextResultsAsrAdapter, "with_vad"), POINTER
    assert hasattr(adapters.SegmentResultsAsrAdapter,
                   "with_timestamps"), POINTER


def test_runner_vad_options_are_known_to_onnx_asr():
    from onnx_asr import adapters
    known = set(typing.get_type_hints(adapters.VadOptions))
    assert set(runner.VAD_OPTIONS) <= known, POINTER


def test_timestamped_segment_result_fields():
    from onnx_asr.vad import TimestampedSegmentResult
    fields = set(TimestampedSegmentResult.__dataclass_fields__)
    assert {"start", "end", "tokens", "timestamps"} <= fields, POINTER


def test_onnxruntime_can_preload_cuda_dlls():
    assert callable(getattr(ort, "preload_dlls", None)), POINTER
