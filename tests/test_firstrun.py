"""Pure-logic tests for the first-run bootstrap. No network, no GPU,
no real Python/ffmpeg downloads: only the import-safe helpers."""
import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from backend import firstrun as F


# --- app_home ----------------------------------------------------------

def test_app_home_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LA"))
    assert F.app_home() == tmp_path / "LA" / "Transcribe"


def test_app_home_fallback_without_localappdata(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert F.app_home() == Path.home() / "AppData" / "Local" / "Transcribe"


# --- requirements_hash -------------------------------------------------

def test_requirements_hash_schema_prefixed_and_deterministic():
    a = F.requirements_hash(b"torch==1\n")
    assert a == F.requirements_hash(b"torch==1\n")
    assert a.startswith(F.SCHEMA + ":")
    assert a != F.requirements_hash(b"torch==2\n")   # content-sensitive


# --- is_ready ----------------------------------------------------------

def _make_home(tmp_path, want):
    home = tmp_path / "Transcribe"
    (home / "backend").mkdir(parents=True)
    (home / "requirements.txt").write_text("x", encoding="utf-8")
    F.py_exe(home).parent.mkdir(parents=True)
    F.py_exe(home).write_text("x", encoding="utf-8")
    F.ffmpeg_exe(home).parent.mkdir(parents=True)
    F.ffmpeg_exe(home).write_text("x", encoding="utf-8")
    F.venv_py(home).parent.mkdir(parents=True)
    F.venv_py(home).write_text("x", encoding="utf-8")
    (home / "venv" / ".ready").write_text(want, encoding="utf-8")
    return home


def test_is_ready_true_when_complete(tmp_path):
    home = _make_home(tmp_path, "1:abc")
    assert F.is_ready(home, "1:abc") is True


def test_is_ready_false_on_hash_mismatch(tmp_path):
    home = _make_home(tmp_path, "1:OLD")
    assert F.is_ready(home, "1:NEW") is False   # new exe / changed deps


def test_is_ready_false_when_sentinel_missing(tmp_path):
    home = _make_home(tmp_path, "1:abc")
    (home / "venv" / ".ready").unlink()
    assert F.is_ready(home, "1:abc") is False   # cancelled/half install


def test_is_ready_false_when_a_payload_missing(tmp_path):
    home = _make_home(tmp_path, "1:abc")
    F.ffmpeg_exe(home).unlink()
    assert F.is_ready(home, "1:abc") is False


# --- sha256 gate -------------------------------------------------------

def test_verify_sha256_accepts_correct(tmp_path):
    p = tmp_path / "blob"
    p.write_bytes(b"hello")
    F._verify_sha256(p, hashlib.sha256(b"hello").hexdigest())   # no raise


def test_verify_sha256_rejects_tampered(tmp_path):
    p = tmp_path / "blob"
    p.write_bytes(b"tampered")
    with pytest.raises(RuntimeError) as e:
        F._verify_sha256(p, hashlib.sha256(b"original").hexdigest())
    assert "integrity check FAILED" in str(e.value)


# --- archive extraction ------------------------------------------------

def _targz(tmp_path, members):
    arc = tmp_path / "a.tar.gz"
    with tarfile.open(arc, "w:gz") as tf:
        for name, data in members.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return arc


def test_extract_python_places_runtime(tmp_path):
    arc = _targz(tmp_path, {"python/python.exe": b"PY",
                            "python/Lib/os.py": b"#"})
    home = tmp_path / "home"
    F._extract_python(arc, home)
    assert F.py_exe(home).read_bytes() == b"PY"


def test_extract_python_rejects_archive_without_python(tmp_path):
    arc = _targz(tmp_path, {"python/NOTHING": b"x"})
    with pytest.raises(RuntimeError) as e:
        F._extract_python(arc, tmp_path / "home")
    assert "python/python.exe" in str(e.value)


def test_safe_members_blocks_traversal(tmp_path):
    arc = _targz(tmp_path, {"../escape": b"evil"})
    base = tmp_path / "home" / "runtime"
    base.mkdir(parents=True)
    with tarfile.open(arc, "r:gz") as tf:
        with pytest.raises(RuntimeError) as e:
            list(F._safe_members(tf, base))
    assert "unsafe archive member" in str(e.value)


def test_extract_ffmpeg_picks_bin_member(tmp_path):
    arc = tmp_path / "f.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("ffmpeg-8.1.1-essentials_build/bin/ffmpeg.exe", b"FF")
        z.writestr("ffmpeg-8.1.1-essentials_build/README.txt", b"r")
    home = tmp_path / "home"
    F._extract_ffmpeg(arc, home)
    assert F.ffmpeg_exe(home).read_bytes() == b"FF"


def test_extract_ffmpeg_rejects_archive_without_binary(tmp_path):
    arc = tmp_path / "f.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("ffmpeg/doc/x.txt", b"x")
    with pytest.raises(RuntimeError) as e:
        F._extract_ffmpeg(arc, tmp_path / "home")
    assert "bin/ffmpeg.exe" in str(e.value)


# --- materialize -------------------------------------------------------

def test_materialize_copies_backend_and_requirements(tmp_path):
    src = tmp_path / "bundle"
    (src / "backend").mkdir(parents=True)
    (src / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (src / "backend" / "__pycache__").mkdir()
    (src / "backend" / "__pycache__" / "x.pyc").write_text("c")
    (src / "requirements.txt").write_text("torch==1", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    F._materialize(src, home)
    assert (home / "backend" / "__init__.py").exists()
    assert not (home / "backend" / "__pycache__").exists()   # ignored
    assert (home / "requirements.txt").read_text(
        encoding="utf-8") == "torch==1"


def test_materialize_raises_without_bundled_backend(tmp_path):
    src = tmp_path / "bundle"
    src.mkdir()
    with pytest.raises(RuntimeError) as e:
        F._materialize(src, tmp_path / "home")
    assert "backend" in str(e.value)
