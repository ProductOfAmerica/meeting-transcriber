"""First-run environment bootstrap for the frozen single-exe build.

Import-safe by contract (same rule as transcript.py/runner.py): importing
this module performs no I/O, no network, reads no argv, writes nothing.
Every side effect lives in functions that backend.app calls explicitly,
and only on the frozen path. Source/dev runs never touch this.

The frozen exe is a thin launcher. On first run it builds a private
environment under %LOCALAPPDATA%\\Transcribe:

    Transcribe/
      backend/                 extracted from the exe (the runner subprocess
      requirements.txt           imports backend.* with cwd here)
      runtime/python/python.exe  pinned portable CPython 3.11 (has venv+pip)
      runtime/ffmpeg/ffmpeg.exe  pinned static ffmpeg
      venv/                      built here by the portable python
      venv/.ready                sentinel: requirements hash + schema

Python and ffmpeg are DOWNLOADED on first run, not bundled in the exe:
PyInstaller onefile re-extracts every bundled byte to a temp dir on every
launch, so bundling ~140MB would slow every cold start forever. First-run
internet is mandatory regardless (~5GB of wheels + ~3GB of models). Every
later launch is instant and fully offline.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path

# --- Pinned runtime payloads --------------------------------------------
# DELIBERATE pins. Verified 2026-05-17 two independent ways: the GitHub
# release-asset API `digest` field AND the projects' own published
# checksums (python-build-standalone SHA256SUMS). A new exe build may bump
# these; _verify_sha256 fails LOUD on any mismatch (corruption, MITM, a
# silently moved asset) and never falls back silently.
PY_URL = ("https://github.com/astral-sh/python-build-standalone/releases/"
          "download/20260510/cpython-3.11.15%2B20260510-x86_64-pc-windows-"
          "msvc-install_only.tar.gz")
PY_SHA256 = "c0d6d9da1286640790c07f32c74516486c4ccd170a65952eebb3e125c34e6c67"

FFMPEG_URL = ("https://github.com/GyanD/codexffmpeg/releases/download/"
              "8.1.1/ffmpeg-8.1.1-essentials_build.zip")
FFMPEG_SHA256 = "6f58ce889f59c311410f7d2b18895b33c03456463486f3b1ebc93d97a0f54541"

# Bump when the bootstrap layout changes so old homes rebuild cleanly.
SCHEMA = "1"

# Ordered first-run phases; the UI maps these to rows 1:1.
PHASES = ("prepare", "fetch_python", "fetch_ffmpeg",
          "make_venv", "pip", "verify")

_PYPI_CU128 = "https://download.pytorch.org/whl/cu128"


def app_home() -> Path:
    """Fixed per-user install root. Env-driven so tests can redirect it."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "Transcribe"


def py_exe(home: Path) -> Path:
    return Path(home) / "runtime" / "python" / "python.exe"


def ffmpeg_exe(home: Path) -> Path:
    return Path(home) / "runtime" / "ffmpeg" / "ffmpeg.exe"


def venv_py(home: Path) -> Path:
    return Path(home) / "venv" / "Scripts" / "python.exe"


def _sentinel(home: Path) -> Path:
    return Path(home) / "venv" / ".ready"


def requirements_hash(req_bytes: bytes) -> str:
    """Stable environment id: schema + sha256(requirements.txt). A changed
    requirements file (new exe build) or a SCHEMA bump invalidates an old
    venv so it is rebuilt instead of silently mismatching."""
    return f"{SCHEMA}:{hashlib.sha256(req_bytes).hexdigest()}"


def is_ready(home: Path, want_hash: str) -> bool:
    """True only if every payload exists AND the sentinel matches the
    current requirements hash. A half-finished or cancelled bootstrap, or
    a new exe whose deps changed, reads as not-ready -> re-offer setup."""
    home = Path(home)
    for p in (home / "backend", home / "requirements.txt",
              py_exe(home), ffmpeg_exe(home), venv_py(home)):
        if not p.exists():
            return False
    try:
        return _sentinel(home).read_text(encoding="utf-8").strip() == want_hash
    except OSError:
        return False


def _verify_sha256(path: Path, want: str) -> None:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    got = h.hexdigest()
    if got != want:
        raise RuntimeError(
            "Download integrity check FAILED for " + path.name + ".\n"
            "expected sha256 " + want + "\n"
            "got      sha256 " + got + "\n"
            "The pinned download was moved, corrupted, or tampered with. "
            "Not proceeding.")


def _cancelled(ev) -> bool:
    return ev is not None and ev.is_set()


def _download(url, dest: Path, want_sha, on_pct=None, cancel_event=None):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(
        url, headers={"User-Agent": "Transcribe-setup"})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            while True:
                if _cancelled(cancel_event):
                    raise RuntimeError("Cancelled.")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total and on_pct:
                    on_pct(done * 100.0 / total)
    _verify_sha256(tmp, want_sha)
    tmp.replace(dest)


def _safe_members(tf: tarfile.TarFile, base: Path):
    base = base.resolve()
    for m in tf.getmembers():
        target = (base / m.name).resolve()
        if base not in target.parents and target != base:
            raise RuntimeError(
                "Refusing unsafe archive member: " + m.name)
        yield m


def _extract_python(archive: Path, home: Path) -> None:
    rt = Path(home) / "runtime"
    target = rt / "python"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    rt.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        try:
            tf.extractall(rt, filter="data")          # py>=3.11.4
        except TypeError:
            tf.extractall(rt, members=_safe_members(tf, rt))
    if not py_exe(home).exists():
        raise RuntimeError(
            "The portable Python archive did not contain "
            "python/python.exe as expected. Not proceeding.")


def _extract_ffmpeg(archive: Path, home: Path) -> None:
    dest = ffmpeg_exe(home)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        member = next(
            (n for n in z.namelist()
             if n.lower().replace("\\", "/").endswith("bin/ffmpeg.exe")),
            None)
        if member is None:
            raise RuntimeError(
                "The ffmpeg archive did not contain bin/ffmpeg.exe. "
                "Not proceeding.")
        with z.open(member) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)


def _materialize(src_dir: Path, home: Path) -> None:
    """Copy the exe-bundled backend/ + requirements.txt out to the home so
    the venv-python runner subprocess (cwd=home) can import backend.*."""
    src_dir, home = Path(src_dir), Path(home)
    bsrc = src_dir / "backend"
    if not bsrc.is_dir():
        raise RuntimeError(
            "Bundled backend/ not found in the exe. The build is broken.")
    shutil.copytree(bsrc, home / "backend", dirs_exist_ok=True,
                     ignore=shutil.ignore_patterns("__pycache__"))
    rsrc = src_dir / "requirements.txt"
    if not rsrc.is_file():
        raise RuntimeError(
            "Bundled requirements.txt not found in the exe. Broken build.")
    shutil.copyfile(rsrc, home / "requirements.txt")


def _run(cmd, cwd, no_window=0, register_proc=None, cancel_event=None,
         on_line=None):
    proc = subprocess.Popen(
        [str(c) for c in cmd], cwd=str(cwd),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, creationflags=no_window)
    if register_proc:
        register_proc(proc)
    for line in proc.stdout:
        if _cancelled(cancel_event):
            try:
                proc.kill()
            finally:
                raise RuntimeError("Cancelled.")
        line = line.rstrip()
        if line and on_line:
            on_line(line)
    proc.stdout.close()
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(
            "A setup step failed (exit %d): %s. Check your internet "
            "connection and try setup again. If it keeps failing, delete "
            "the Transcribe folder in %%LOCALAPPDATA%% and retry."
            % (rc, " ".join(str(c) for c in cmd[:3])))


def bootstrap(home, src_dir, want_hash, *, emit, cancel_event=None,
              register_proc=None, no_window=0, env_builder=None):
    """Run the ordered first-run phases. `emit(phase, pct_or_None,
    msg_or_None)` drives the existing progress protocol. Restart-safe:
    any failure or cancel leaves no .ready, so the next launch re-offers
    and partial work (a half venv) is wiped on retry."""
    home = Path(home)
    cache = home / "_dl"

    def ck():
        if _cancelled(cancel_event):
            raise RuntimeError("Cancelled.")

    emit("prepare", None, "preparing")
    home.mkdir(parents=True, exist_ok=True)
    _materialize(src_dir, home)
    ck()

    emit("fetch_python", 0.0, "downloading Python runtime")
    if not py_exe(home).exists():
        pkg = cache / "python.tar.gz"
        _download(PY_URL, pkg, PY_SHA256,
                  lambda p: emit("fetch_python", p, None), cancel_event)
        ck()
        _extract_python(pkg, home)
    ck()

    emit("fetch_ffmpeg", 0.0, "downloading ffmpeg")
    if not ffmpeg_exe(home).exists():
        fz = cache / "ffmpeg.zip"
        _download(FFMPEG_URL, fz, FFMPEG_SHA256,
                  lambda p: emit("fetch_ffmpeg", p, None), cancel_event)
        ck()
        _extract_ffmpeg(fz, home)
    ck()

    emit("make_venv", None, "creating the environment")
    venv = home / "venv"
    if venv.exists() and not venv_py(home).exists():
        shutil.rmtree(venv, ignore_errors=True)      # wipe a partial venv
    if not venv_py(home).exists():
        _run([py_exe(home), "-m", "venv", venv], home, no_window,
             register_proc, cancel_event)
    ck()

    emit("pip", None, "installing dependencies (~5 GB, several minutes)")
    vpy = venv_py(home)
    _run([vpy, "-m", "pip", "install", "-U", "pip"], home, no_window,
         register_proc, cancel_event,
         on_line=lambda ln: emit("pip", None, ln))
    _run([vpy, "-m", "pip", "install", "-r", home / "requirements.txt",
          "--extra-index-url", _PYPI_CU128], home, no_window,
         register_proc, cancel_event,
         on_line=lambda ln: emit("pip", None, ln))
    ck()

    emit("verify", None, "verifying the GPU")
    env = env_builder(venv) if env_builder else None
    probe = subprocess.run(
        [str(vpy), "-c", "import torch,sys;sys.exit(0 if "
         "torch.cuda.is_available() else 3)"],
        cwd=str(home), env=env, capture_output=True, text=True,
        creationflags=no_window)
    if probe.returncode != 0:
        raise RuntimeError(
            "Setup installed, but this app needs an NVIDIA GPU with a "
            "recent driver (CUDA 12.8 runtime). torch.cuda.is_available() "
            "returned False. Likely causes: no NVIDIA GPU in this machine, "
            "or the GPU driver is too old for CUDA 12.8. Update your NVIDIA "
            "driver from nvidia.com, then run setup again.")

    _sentinel(home).write_text(want_hash, encoding="utf-8")
    shutil.rmtree(cache, ignore_errors=True)
