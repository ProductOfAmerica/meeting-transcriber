"""Pure-logic tests for the first-run install. No network, no GPU, no real
Python/ffmpeg downloads."""
import hashlib
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import fetch, fsutil, models
from backend import firstrun as F


# --- app_home ----------------------------------------------------------

def test_app_home_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LA"))
    assert F.app_home() == tmp_path / "LA" / "Transcribe"


def test_app_home_fallback_without_localappdata(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert F.app_home() == Path.home() / "AppData" / "Local" / "Transcribe"


# --- keys and layout ---------------------------------------------------

def test_key_is_short_deterministic_and_part_sensitive():
    k = F.key(b"lock", "py", "2")
    assert len(k) == 12 and int(k, 16) >= 0
    assert k == F.key(b"lock", "py", "2")
    assert k != F.key(b"lock", "py", "3")
    assert F.key(b"ab", b"c") != F.key(b"a", b"bc")     # boundaries count


def test_tree_key_ignores_caches_and_sees_changes(tmp_path):
    src = tmp_path / "backend"
    (src / "__pycache__").mkdir(parents=True)
    (src / "a.py").write_text("x = 1", encoding="utf-8")
    k1 = F.tree_key(src)
    (src / "__pycache__" / "a.cpython-311.pyc").write_bytes(b"junk")
    assert F.tree_key(src) == k1
    (src / "a.py").write_text("x = 2", encoding="utf-8")
    assert F.tree_key(src) != k1


def _bundle(tmp_path, lock=b"torch==1\n", code="print(1)"):
    b = tmp_path / "bundle"
    (b / "backend" / "assets").mkdir(parents=True, exist_ok=True)
    (b / "backend" / "runner.py").write_text(code, encoding="utf-8")
    (b / "backend" / "assets" / "smoke.wav").write_bytes(b"RIFF")
    (b / "requirements.lock").write_bytes(lock)
    return b


def _complete(layout):
    """Fake a finished install for a layout."""
    for p in (layout.python_exe, layout.ffmpeg_exe, layout.venv_python):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
    (layout.env / ".installed").write_text("x", encoding="utf-8")
    (layout.env / ".ready").write_text("x", encoding="utf-8")
    for spec in models.ALL:
        spec.dir(layout.models).mkdir(parents=True, exist_ok=True)
    F.ensure_code(layout)


def test_layout_keys_follow_lock_and_code(tmp_path):
    home = tmp_path / "home"
    a = F.Layout(home, _bundle(tmp_path))
    b = F.Layout(home, _bundle(tmp_path, lock=b"torch==2\n"))
    assert a.env != b.env and a.code == b.code        # new deps, same code
    c = F.Layout(home, _bundle(tmp_path, lock=b"torch==2\n", code="print(2)"))
    assert c.env == b.env and c.code != b.code        # runner-only change
    assert len(a.env.name) == 12 and a.env.parent == home / "envs"


def test_env_key_ignores_lock_line_endings(tmp_path):
    lf = F.Layout(tmp_path / "home", _bundle(tmp_path / "a", lock=b"a==1\nb==2\n"))
    crlf = F.Layout(tmp_path / "home",
                    _bundle(tmp_path / "b", lock=b"a==1\r\nb==2\r\n"))
    assert lf.env == crlf.env


def test_ready_needs_every_piece(tmp_path):
    layout = F.Layout(tmp_path / "home", _bundle(tmp_path))
    assert not layout.ready()
    _complete(layout)
    assert layout.ready()
    (layout.env / ".ready").unlink()                   # cancelled or half install
    assert not layout.ready()


def test_missing_lock_is_an_oserror(tmp_path):
    b = _bundle(tmp_path)
    (b / "requirements.lock").unlink()
    with pytest.raises(OSError):
        F.Layout(tmp_path / "home", b)


def test_ensure_code_copies_once_without_caches(tmp_path):
    b = _bundle(tmp_path)
    (b / "backend" / "__pycache__").mkdir()
    (b / "backend" / "__pycache__" / "x.pyc").write_bytes(b"c")
    layout = F.Layout(tmp_path / "home", b)
    assert F.ensure_code(layout) == layout.code
    assert (layout.code / "backend" / "runner.py").exists()
    assert (layout.code / "backend" / "assets" / "smoke.wav").exists()
    assert not (layout.code / "backend" / "__pycache__").exists()
    (layout.code / "backend" / "marker").write_text("kept")
    F.ensure_code(layout)                              # already there: no-op
    assert (layout.code / "backend" / "marker").exists()
    assert not [p for p in layout.code.parent.iterdir()
                if p.name.startswith(".")]             # no temp left behind


# --- folder helpers -----------------------------------------------------

def test_move_into_place_keeps_existing_final(tmp_path):
    final = tmp_path / "final"
    final.mkdir()
    (final / "old").write_text("old")
    tmp = tmp_path / ".final.partial"
    tmp.mkdir()
    (tmp / "new").write_text("new")
    fsutil.move_into_place(tmp, final)
    assert (final / "old").exists() and not (final / "new").exists()
    assert not tmp.exists()


def test_retry_survives_transient_windows_errors(monkeypatch):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            err = PermissionError("busy")
            err.winerror = 32                          # sharing violation
            raise err
        return "ok"
    monkeypatch.setattr(fsutil.time, "sleep", lambda _s: None)
    assert fsutil.retry(flaky) == "ok" and len(calls) == 3


def test_retry_does_not_hide_real_errors(monkeypatch):
    monkeypatch.setattr(fsutil.time, "sleep", lambda _s: None)

    def missing():
        raise FileNotFoundError("gone")
    with pytest.raises(FileNotFoundError):
        fsutil.retry(missing)


def test_rmtree_clears_read_only_files(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    f = d / "ro.txt"
    f.write_text("x")
    f.chmod(0o444)
    fsutil.rmtree(d)
    assert not d.exists()
    fsutil.rmtree(d)                                   # missing: fine


# --- garbage collection -------------------------------------------------

def test_collect_garbage_removes_only_stale_keyed_and_legacy(tmp_path):
    layout = F.Layout(tmp_path / "home", _bundle(tmp_path))
    _complete(layout)
    home = layout.home
    old = [home / "code" / "0123456789ab", home / "envs" / "ba9876543210",
           home / "runtime" / "python-aaaaaaaaaaaa",
           home / "models" / "parakeet-tdt-0.6b-v2-bbbbbbbbbbbb",
           home / "models" / ".silero-vad-cccccccccccc.partial",
           home / "venv", home / "backend", home / "runtime" / "python",
           home / "runtime" / "ffmpeg", home / "_dl", home / "downloads",
           home / "pip-cache"]
    for p in old:
        p.mkdir(parents=True, exist_ok=True)
    (home / "requirements.txt").write_text("legacy")
    unrelated = [home / "code" / "notes", home / "envs" / "README.txt",
                 home / "settings.json", home / "logs"]
    unrelated[0].mkdir()
    unrelated[1].write_text("x")
    unrelated[2].write_text("{}")
    unrelated[3].mkdir()
    removed = F.collect_garbage(layout)
    assert sorted(removed) == sorted(old + [home / "requirements.txt"])
    assert all(not p.exists() for p in removed)
    assert all(p.exists() for p in unrelated)
    assert layout.ready()
    assert not any((home / "trash").iterdir())


def test_collect_garbage_on_a_fresh_home_is_a_no_op(tmp_path):
    layout = F.Layout(tmp_path / "home", _bundle(tmp_path))
    assert F.collect_garbage(layout) == []


# --- GPU check ------------------------------------------------------------

def _smi(stdout="", rc=0, exc=None):
    def run(cmd, **_kw):
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, rc, stdout, "")
    return run


def test_check_gpu_accepts_supported_card():
    g = F.check_gpu(_smi("NVIDIA GeForce RTX 4090, 8.9, 23028, 610.88\n"))
    assert g["compute"] == (8, 9) and g["vram_mib"] == 23028


def test_check_gpu_picks_any_supported_card():
    g = F.check_gpu(_smi("Old Card, 6.1, 8192, 610.88\n"
                         "NVIDIA GeForce RTX 3060, 8.6, 12288, 610.88\n"))
    assert g["name"] == "NVIDIA GeForce RTX 3060"


def test_check_gpu_rejects_old_and_small_cards():
    with pytest.raises(F.SetupError) as e:
        F.check_gpu(_smi("NVIDIA GeForce GTX 1080, 6.1, 8192, 610.88\n"))
    assert "GTX 16" in str(e.value) and "6.1" in str(e.value)
    with pytest.raises(F.SetupError) as e:
        F.check_gpu(_smi("NVIDIA GeForce GTX 1650, 7.5, 4096, 610.88\n"))
    assert "6 GB" in str(e.value)


def test_check_gpu_rejects_a_driver_too_old_for_cuda_12():
    with pytest.raises(F.SetupError) as e:
        F.check_gpu(_smi("NVIDIA GeForce RTX 3060, 8.6, 12288, 516.94\n"))
    assert "516.94" in str(e.value) and "525" in str(e.value)


def test_check_gpu_driver_floor_passes_newer_or_unreadable_versions():
    for driver in ("528.33", "610.88", "N/A"):
        F.check_gpu(_smi(f"NVIDIA GeForce RTX 3060, 8.6, 12288, {driver}\n"))


def test_check_gpu_without_driver_or_on_failure():
    with pytest.raises(F.SetupError) as e:
        F.check_gpu(_smi(exc=FileNotFoundError()))
    assert "nvidia-smi is missing" in str(e.value)
    with pytest.raises(F.SetupError) as e:
        F.check_gpu(_smi("Field compute_cap is not a valid field", rc=2))
    assert "Update your NVIDIA driver" in str(e.value)
    with pytest.raises(F.SetupError):
        F.check_gpu(_smi("garbage\n"))


# --- free space -------------------------------------------------------------

def test_space_needed_follows_what_is_missing(tmp_path):
    layout = F.Layout(tmp_path / "home", _bundle(tmp_path))
    fresh = F.space_needed(layout)
    assert fresh == int(F._SPACE_ENV + F._SPACE_RUNTIME + F._SPACE_MARGIN)
    _complete(layout)
    assert F.space_needed(layout) == 0
    for spec in models.ALL:
        spec.dir(layout.models).rmdir()            # environment done, models not
    assert F.space_needed(layout) == int(F._SPACE_MODELS + F._SPACE_MARGIN)


def test_check_space_stops_early_on_a_full_drive(tmp_path):
    layout = F.Layout(tmp_path / "home", _bundle(tmp_path))

    def disk(free):
        return lambda _path: SimpleNamespace(free=free)
    with pytest.raises(F.SetupError) as e:
        F.check_space(layout, disk_usage=disk(5 * F._GIB))
    assert "17 GB of free space" in str(e.value) and "5.0 GB" in str(e.value)
    F.check_space(layout, disk_usage=disk(40 * F._GIB))


def test_check_space_measures_a_home_that_does_not_exist_yet(tmp_path):
    layout = F.Layout(tmp_path / "LA" / "Transcribe", _bundle(tmp_path))
    seen = []

    def disk(path):
        seen.append(path)
        return SimpleNamespace(free=40 * F._GIB)
    F.check_space(layout, disk_usage=disk)
    assert seen == [tmp_path]                    # the nearest folder that exists
    assert not (tmp_path / "LA").exists()        # asking created nothing


# --- causes -----------------------------------------------------------------

def test_hint_names_a_cause_only_when_shown():
    assert "disk" in F.hint("OSError: [Errno 28] No space left on device")
    assert "network" in F.hint("ReadTimeoutError: Read timed out.")
    assert "hash" in F.hint("THESE PACKAGES DO NOT MATCH THE HASHES from the "
                            "requirements file")
    assert F.hint("ERROR: something else") == ""


# --- sha256 gate --------------------------------------------------------

def test_verify_sha256_accepts_correct(tmp_path):
    p = tmp_path / "blob"
    p.write_bytes(b"hello")
    fetch.verify_sha256(p, hashlib.sha256(b"hello").hexdigest())


def test_verify_sha256_rejects_tampered(tmp_path):
    p = tmp_path / "blob"
    p.write_bytes(b"tampered")
    with pytest.raises(RuntimeError) as e:
        fetch.verify_sha256(p, hashlib.sha256(b"original").hexdigest())
    assert "integrity check FAILED" in str(e.value)


# --- archive installs ---------------------------------------------------

def _targz(tmp_path, members):
    arc = tmp_path / "a.tar.gz"
    with tarfile.open(arc, "w:gz") as tf:
        for name, data in members.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return arc


def test_install_python_places_runtime(tmp_path):
    layout = F.Layout(tmp_path / "home", _bundle(tmp_path))
    arc = _targz(tmp_path, {"python/python.exe": b"PY",
                            "python/Lib/os.py": b"#"})
    F.install_python(layout, arc)
    assert layout.python_exe.read_bytes() == b"PY"
    assert not [p for p in layout.python_dir.parent.iterdir()
                if p.name.startswith(".")]


def test_install_python_rejects_archive_without_python(tmp_path):
    layout = F.Layout(tmp_path / "home", _bundle(tmp_path))
    arc = _targz(tmp_path, {"python/NOTHING": b"x"})
    with pytest.raises(F.SetupError) as e:
        F.install_python(layout, arc)
    assert "python/python.exe" in str(e.value)
    assert not layout.python_dir.exists()


def test_safe_members_blocks_traversal(tmp_path):
    arc = _targz(tmp_path, {"../escape": b"evil"})
    base = tmp_path / "home" / "runtime"
    base.mkdir(parents=True)
    with tarfile.open(arc, "r:gz") as tf:
        with pytest.raises(RuntimeError) as e:
            list(F._safe_members(tf, base))
    assert "unsafe archive member" in str(e.value)


def test_install_ffmpeg_picks_bin_member(tmp_path):
    layout = F.Layout(tmp_path / "home", _bundle(tmp_path))
    arc = tmp_path / "f.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("ffmpeg-8.1.1-essentials_build/bin/ffmpeg.exe", b"FF")
        z.writestr("ffmpeg-8.1.1-essentials_build/README.txt", b"r")
    F.install_ffmpeg(layout, arc)
    assert layout.ffmpeg_exe.read_bytes() == b"FF"


def test_install_ffmpeg_rejects_archive_without_binary(tmp_path):
    layout = F.Layout(tmp_path / "home", _bundle(tmp_path))
    arc = tmp_path / "f.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("ffmpeg/doc/x.txt", b"x")
    with pytest.raises(F.SetupError) as e:
        F.install_ffmpeg(layout, arc)
    assert "bin/ffmpeg.exe" in str(e.value)
    assert not layout.ffmpeg_dir.exists()
