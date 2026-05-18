import pytest
from pathlib import Path
from backend import writeprobe as W


def test_writable_dir_passes(tmp_path):
    W.probe_writable(tmp_path)  # no exception


def test_missing_dir_raises(tmp_path):
    with pytest.raises(W.ProbeError):
        W.probe_writable(tmp_path / "does-not-exist")


def test_file_path_raises(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(W.ProbeError):
        W.probe_writable(f)
