"""First-run install of the private runtime for the frozen single-exe build.

Import-safe by contract: importing this module performs no I/O, no network,
reads no argv, writes nothing. Every side effect lives in functions that
backend.app calls explicitly, and only on the frozen path. Source/dev runs
never touch this.

The frozen exe is a thin launcher. Everything it installs lives under
%LOCALAPPDATA%\\Transcribe, named after what built it, so a new exe never
runs stale code or a mismatched environment:

    Transcribe/
      code/<key>/backend/         runner code copied out of this exe
      envs/<key>/                 venv built from requirements.lock;
                                  .installed after pip, .ready after the
                                  GPU test
      runtime/python-<key>/       pinned portable CPython 3.11
      runtime/ffmpeg-<key>/       pinned static ffmpeg
      models/<name>-<revision>/   pinned speech models (backend.models)
      logs/                       setup-*.log, run-*.log
      downloads/, pip-cache/      used during setup, removed after
      trash/                      old folders on their way out

A key is the first 12 hex digits of a sha256 over what built the folder.
Folders appear at their final name only when complete: built in a temp
sibling and renamed (code, runtime, models), or marked last (the venv, which
cannot move once built). Old folders are collected after a successful setup
and at launch.

Python and ffmpeg are downloaded on first run, not bundled in the exe:
PyInstaller onefile re-extracts every bundled byte on every launch, so
bundling them would slow every start.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
import zipfile
from pathlib import Path

from . import fetch
from . import fsutil
from . import models
from . import pipeline
from .procs import Cancelled

# --- Pinned runtime payloads --------------------------------------------
# DELIBERATE pins. Verified 2026-05-17 two independent ways: the GitHub
# release-asset API `digest` field AND the projects' own published
# checksums (python-build-standalone SHA256SUMS). fetch.download fails LOUD
# on any mismatch (corruption, MITM, a silently moved asset).
PY_URL = ("https://github.com/astral-sh/python-build-standalone/releases/"
          "download/20260510/cpython-3.11.15%2B20260510-x86_64-pc-windows-"
          "msvc-install_only.tar.gz")
PY_SHA256 = "c0d6d9da1286640790c07f32c74516486c4ccd170a65952eebb3e125c34e6c67"

FFMPEG_URL = ("https://github.com/GyanD/codexffmpeg/releases/download/"
              "8.1.1/ffmpeg-8.1.1-essentials_build.zip")
FFMPEG_SHA256 = "6f58ce889f59c311410f7d2b18895b33c03456463486f3b1ebc93d97a0f54541"

# Bump when the layout changes so old installs rebuild cleanly.
SCHEMA = "2"

# PyTorch 2.8.0's CUDA 12.8 wheels dropped compute capability 5.0 to 7.0
# (PyTorch 2.8.0 release notes), so 7.5 (GTX 16 / RTX 20 series) is the floor.
MIN_COMPUTE = (7, 5)
# Measured 2026-09-25: about 3.8 GB peak for speech recognition and 2.6 GB
# for diarization (run one after the other). 6 GB cards report ~6144 MiB.
MIN_VRAM_MIB = 5800
# NVIDIA's CUDA Compatibility guide (minor version compatibility, read
# 2026-09-26): CUDA 12.x runs on driver 525 or newer. Anything between that and
# a working setup is left to the final GPU test.
MIN_DRIVER = (525, 0)

# Free space setup needs, from a fresh install on 2026-09-26 that sampled the
# drive's free space: the pip phase peaked 15.3 GiB above its start (the
# environment, pip's download cache and its temporary files), the models then
# added 2.3 GiB after pip freed its temporary files, and Python plus ffmpeg
# took 0.4 GiB. Rounded up, plus a margin.
_GIB = 2 ** 30
_SPACE_ENV = 15.5 * _GIB
_SPACE_MODELS = 2.5 * _GIB
_SPACE_RUNTIME = 0.5 * _GIB
_SPACE_MARGIN = 1 * _GIB

# backend/assets/smoke.wav is 33.05 s to 38.45 s of chapter 1 of LibriVox's
# public-domain "Alice's Adventures in Wonderland (Version 7)", read by Craig
# Franklin (archive.org/details/alicesadventuresinwonderland_2005_librivox):
# "Alice was beginning to get very tired of sitting by her sister on the bank".
SMOKE_WORDS = ("alice", "beginning", "tired", "sitting", "sister", "bank")

# The previous release's layout, removed after the new one is ready.
LEGACY = ("venv", "backend", "requirements.txt", "runtime/python",
          "runtime/ffmpeg", "_dl")

_KEY = r"[0-9a-f]{12}"
_GC_GROUPS = {"code": _KEY, "envs": _KEY,
              "runtime": rf"(?:python|ffmpeg)-{_KEY}",
              "models": rf"[A-Za-z0-9._-]+-{_KEY}"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SetupError(Exception):
    """Setup cannot continue; the message is written for the user."""


def app_home() -> Path:
    """Fixed per-user install root. Env-driven so tests can redirect it."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "Transcribe"


def key(*parts) -> str:
    """12 hex digits naming what built a folder."""
    h = hashlib.sha256()
    for part in parts:
        b = part if isinstance(part, bytes) else str(part).encode()
        h.update(len(b).to_bytes(8, "big"))
        h.update(b)
    return h.hexdigest()[:12]


def ffmpeg_dir(home) -> Path:
    """Where the pinned ffmpeg lives under an install, or a source checkout."""
    return Path(home) / "runtime" / f"ffmpeg-{FFMPEG_SHA256[:12]}"


def tree_key(folder) -> str:
    """Key over every file in a folder (paths and bytes), ignoring caches."""
    folder = Path(folder)
    parts = []
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder)
        if (p.is_dir() or "__pycache__" in rel.parts
                or p.suffix == ".pyc"):
            continue
        parts += [rel.as_posix(), p.read_bytes()]
    return key(*parts)


class Layout:
    """Where this exe build's code, environment, runtime and models live."""

    def __init__(self, home, bundle):
        self.home, self.bundle = Path(home), Path(bundle)
        self.lock = self.bundle / "requirements.lock"
        self.code = self.home / "code" / tree_key(self.bundle / "backend")
        # Line endings depend on the checkout that built the exe, not the lock.
        lock = self.lock.read_bytes().replace(b"\r\n", b"\n")
        self.env = self.home / "envs" / key(lock, PY_SHA256, SCHEMA)
        self.python_dir = self.home / "runtime" / f"python-{PY_SHA256[:12]}"
        self.ffmpeg_dir = ffmpeg_dir(self.home)
        self.models = self.home / "models"
        self.logs = self.home / "logs"

    @property
    def python_exe(self) -> Path:
        return self.python_dir / "python.exe"

    @property
    def ffmpeg_exe(self) -> Path:
        return self.ffmpeg_dir / "ffmpeg.exe"

    @property
    def venv_python(self) -> Path:
        return self.env / "Scripts" / "python.exe"

    @property
    def smoke_wav(self) -> Path:
        return self.code / "backend" / "assets" / "smoke.wav"

    def keep(self) -> set:
        return ({self.code, self.env, self.python_dir, self.ffmpeg_dir}
                | {spec.dir(self.models) for spec in models.ALL})

    def ready(self) -> bool:
        return ((self.env / ".ready").is_file()
                and self.venv_python.exists() and self.python_exe.exists()
                and self.ffmpeg_exe.exists() and models.present(self.models))


# --- folders ---------------------------------------------------------------

def _partial(final: Path) -> Path:
    return final.parent / f".{final.name}.partial-{uuid.uuid4().hex[:8]}"


def ensure_code(layout: Layout) -> Path:
    """Copy this exe's backend/ into code/<key>/ unless already there."""
    if (layout.code / "backend").is_dir():
        return layout.code
    tmp = _partial(layout.code)
    shutil.copytree(layout.bundle / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    fsutil.move_into_place(tmp, layout.code)
    return layout.code


def _trash(layout: Layout, path: Path) -> None:
    if not path.exists():
        return
    dest = layout.home / "trash" / f"{path.name}-{uuid.uuid4().hex[:8]}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    fsutil.retry(lambda: path.rename(dest))
    try:
        fsutil.rmtree(dest)
    except OSError:
        pass            # stays in trash/; collected next time


def stale_items(layout: Layout) -> list:
    """Old folders in the keyed groups, unfinished temp folders, the legacy
    layout, and setup scratch. Selected by exact name pattern only."""
    keep = layout.keep()
    out = []
    for group, pattern in _GC_GROUPS.items():
        root = layout.home / group
        if not root.is_dir():
            continue
        rx = re.compile(rf"^(?:{pattern})$")
        for entry in os.scandir(root):
            path = Path(entry.path)
            if (entry.is_dir() and path not in keep
                    and (rx.match(entry.name)
                         or entry.name.startswith("."))):
                out.append(path)
    for rel in LEGACY + ("downloads", "pip-cache"):
        path = layout.home / rel
        if path.exists() and path not in keep:
            out.append(path)
    return sorted(set(out))


def collect_garbage(layout: Layout, log=lambda _msg: None) -> list:
    """Remove stale_items(); then prove the folders in use survived."""
    in_use = {p for p in layout.keep() if p.exists()}
    doomed = stale_items(layout)
    if doomed:
        log(f"removing {len(doomed)} old item(s): " + ", ".join(
            str(p.relative_to(layout.home)) for p in doomed))
    for path in doomed:
        _trash(layout, path)
    trash = layout.home / "trash"
    if trash.is_dir():
        for entry in os.scandir(trash):
            try:
                fsutil.rmtree(entry.path)
            except OSError:
                pass
    lost = sorted(p for p in in_use if not p.exists())
    if lost:
        raise RuntimeError(f"cleanup removed folders in use: {lost}")
    return doomed


# --- payloads --------------------------------------------------------------

def _safe_members(tf: tarfile.TarFile, base: Path):
    base = base.resolve()
    for m in tf.getmembers():
        target = (base / m.name).resolve()
        if base not in target.parents and target != base:
            raise RuntimeError(
                "Refusing unsafe archive member: " + m.name)
        yield m


def install_python(layout: Layout, archive: Path) -> None:
    tmp = _partial(layout.python_dir)
    tmp.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tf:
        try:
            tf.extractall(tmp, filter="data")          # py>=3.11.4
        except TypeError:
            tf.extractall(tmp, members=_safe_members(tf, tmp))
    src = tmp / "python"
    if not (src / "python.exe").exists():
        fsutil.rmtree(tmp)
        raise SetupError(
            "The portable Python archive did not contain python/python.exe "
            "as expected. Not proceeding.")
    fsutil.move_into_place(src, layout.python_dir)
    fsutil.rmtree(tmp)


def install_ffmpeg(layout: Layout, archive: Path) -> None:
    tmp = _partial(layout.ffmpeg_dir)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        member = next(
            (n for n in z.namelist()
             if n.lower().replace("\\", "/").endswith("bin/ffmpeg.exe")),
            None)
        if member is None:
            fsutil.rmtree(tmp)
            raise SetupError(
                "The ffmpeg archive did not contain bin/ffmpeg.exe. "
                "Not proceeding.")
        with z.open(member) as src, open(tmp / "ffmpeg.exe", "wb") as out:
            shutil.copyfileobj(src, out)
    fsutil.move_into_place(tmp, layout.ffmpeg_dir)


# --- checks ----------------------------------------------------------------

_NO_GPU = ("No NVIDIA GPU driver was found (nvidia-smi is missing). "
           "Transcribe needs an NVIDIA GTX 16 or RTX 20 series GPU or newer "
           "with at least 6 GB of memory, and its driver from nvidia.com.")


def check_gpu(run=subprocess.run) -> dict:
    """Fail fast, before any download, on a missing or unsupported GPU."""
    cmd = ["nvidia-smi",
           "--query-gpu=name,compute_cap,memory.total,driver_version",
           "--format=csv,noheader,nounits"]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=60,
                creationflags=_NO_WINDOW)
    except FileNotFoundError:
        raise SetupError(_NO_GPU)
    except subprocess.TimeoutExpired:
        raise SetupError("nvidia-smi did not respond. Restart Windows or "
                         "reinstall the NVIDIA driver, then try again.")
    if r.returncode != 0:
        detail = (r.stdout or r.stderr or "").strip().splitlines()
        raise SetupError(
            "The NVIDIA driver could not describe the GPU. Update your "
            "NVIDIA driver from nvidia.com and try again."
            + (f" ({detail[-1]})" if detail else ""))
    gpus = []
    for line in r.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            continue
        name, cc, mib, driver = parts
        try:
            major, minor = (int(x) for x in cc.split("."))
            vram = int(float(mib))
        except ValueError:
            continue
        gpus.append({"name": name, "compute": (major, minor),
                     "vram_mib": vram, "driver": driver})
    if not gpus:
        raise SetupError(_NO_GPU)
    good = [g for g in gpus if g["compute"] >= MIN_COMPUTE
            and g["vram_mib"] >= MIN_VRAM_MIB]
    if not good:
        g = max(gpus, key=lambda g: (g["compute"], g["vram_mib"]))
        raise SetupError(
            f"{g['name']} (compute capability {g['compute'][0]}."
            f"{g['compute'][1]}, {g['vram_mib']} MiB) can't run Transcribe. "
            "It needs an NVIDIA GTX 16 or RTX 20 series GPU or newer with at "
            "least 6 GB of memory.")
    m = re.match(r"(\d+)\.(\d+)", good[0]["driver"])
    if m and (int(m.group(1)), int(m.group(2))) < MIN_DRIVER:
        raise SetupError(
            f"Your NVIDIA driver is version {good[0]['driver']}, and Transcribe "
            f"needs version {MIN_DRIVER[0]} or newer. Update it from nvidia.com, "
            "then try again.")
    return good[0]


def space_needed(layout: Layout) -> int:
    """Bytes of free space the rest of setup needs on the home's drive."""
    need = 0
    if not (layout.env / ".installed").is_file():
        need += _SPACE_ENV          # setup's peak; the models fit in after it
    elif not models.present(layout.models):
        need += _SPACE_MODELS
    if not (layout.python_exe.exists() and layout.ffmpeg_exe.exists()):
        need += _SPACE_RUNTIME
    return int(need + _SPACE_MARGIN) if need else 0


def check_space(layout: Layout, disk_usage=shutil.disk_usage) -> None:
    """Fail before downloading anything if the drive is too full."""
    need = space_needed(layout)
    if not need:
        return
    free = disk_usage(layout.home).free
    if free < need:
        drive = layout.home.drive or str(layout.home.anchor)
        raise SetupError(
            f"Setup needs about {need / _GIB:.0f} GB of free space on {drive}, "
            f"and it has {free / _GIB:.1f} GB. Free up some space, then try "
            "again.")


def hint(output: str) -> str:
    """A cause, only when the output shows it."""
    low = output.lower()
    if any(s in low for s in ("no space left", "errno 28",
                              "not enough space on the disk")):
        return "The disk is full. Free some space and try again."
    if any(s in low for s in ("connectionerror", "read timed out",
                              "proxyerror", "name resolution",
                              "connection aborted", "connection reset",
                              "temporary failure", "network is unreachable",
                              "urlopen error")):
        return "The network failed. Check your internet connection and try again."
    if "these packages do not match the hashes" in low:
        return ("A downloaded package did not match its pinned hash. Try "
                "again; if it repeats, the download source changed.")
    return ""


def _check(rc: int, what: str, sink) -> None:
    if rc != 0:
        tail = sink.tail()
        cause = hint(tail)
        raise SetupError(
            f"Setup failed while {what} (exit code {rc})."
            + (f" {cause}" if cause else "")
            + "\n\nLast output:\n" + (tail or "(none)"))


_GPU_PROBE = """\
import json, torch, pyannote.audio
ok = torch.cuda.is_available()
info = {"ok": ok}
if ok:
    info["name"] = torch.cuda.get_device_name(0)
    info["cc"] = list(torch.cuda.get_device_capability(0))
    info["x"] = float((torch.ones(4, device="cuda") * 2).sum())
print(json.dumps(info))
"""


def smoke_test(layout: Layout, supervisor, sink) -> None:
    """Prove the real stack works on this GPU: torch and pyannote load and
    compute on CUDA, and the speech model transcribes a known sentence."""
    lines = []
    rc = supervisor.run([layout.venv_python, "-c", _GPU_PROBE],
                        cwd=layout.home, env=pipeline.build_env(),
                        on_stdout=lines.append, sink=sink)
    info = {}
    for line in reversed(lines):
        try:
            info = json.loads(line)
            break
        except ValueError:
            continue
    if rc != 0 or not info.get("ok"):
        raise SetupError(
            "The GPU test failed: PyTorch could not use CUDA on this GPU. "
            "Update your NVIDIA driver from nvidia.com and try again."
            "\n\nLast output:\n" + (sink.tail() or "(none)"))
    if tuple(info.get("cc") or (0, 0)) < MIN_COMPUTE or info.get("x") != 8.0:
        raise SetupError(
            f"{info.get('name')} (compute capability "
            f"{'.'.join(map(str, info.get('cc') or []))}) failed the GPU "
            "test. Transcribe needs an NVIDIA GTX 16 or RTX 20 series GPU "
            "or newer.")
    out = Path(tempfile.mkdtemp(prefix="transcribe-smoke-"))
    try:
        stats = pipeline.run_job(
            mode="pertrack", audio=[layout.smoke_wav], out_dir=out,
            venv_dir=layout.env, code_dir=layout.code,
            models_root=layout.models, ffmpeg=layout.ffmpeg_exe,
            supervisor=supervisor, progress_cb=lambda *a: None)
        body = Path(stats["output_path"]).read_text(
            encoding="utf-8").split("---", 1)[-1].lower()
    finally:
        fsutil.rmtree(out)
    heard = [w for w in SMOKE_WORDS if w in body]
    if len(heard) < 3:
        raise SetupError(
            "The speech model ran on the GPU but did not recognize the test "
            f"sentence (matched {len(heard)} of {len(SMOKE_WORDS)} words).")


# --- the setup ---------------------------------------------------------------

def bootstrap(layout: Layout, *, emit, supervisor, sink) -> None:
    """Run the ordered setup phases. emit(phase, pct_or_None, msg_or_None)
    drives the progress rows. Restart-safe: an interrupted phase leaves only
    temp folders or an unmarked venv, which the next run discards."""
    def cancelled():
        return supervisor.cancelled

    def ck():
        if supervisor.cancelled:
            raise Cancelled()

    emit("gpu_check", None, "checking the GPU")
    gpu = check_gpu()
    sink.note(f"GPU: {gpu}")
    ck()

    emit("prepare", None, "preparing")
    layout.home.mkdir(parents=True, exist_ok=True)
    check_space(layout)
    ensure_code(layout)
    downloads = layout.home / "downloads"
    ck()

    emit("fetch_python", 0.0, "downloading Python")
    if not layout.python_exe.exists():
        archive = downloads / "python.tar.gz"
        fetch.download(PY_URL, archive, PY_SHA256,
                       lambda p: emit("fetch_python", p, None), cancelled)
        install_python(layout, archive)
    ck()

    emit("fetch_ffmpeg", 0.0, "downloading ffmpeg")
    if not layout.ffmpeg_exe.exists():
        archive = downloads / "ffmpeg.zip"
        fetch.download(FFMPEG_URL, archive, FFMPEG_SHA256,
                       lambda p: emit("fetch_ffmpeg", p, None), cancelled)
        install_ffmpeg(layout, archive)
    ck()

    installed = layout.env / ".installed"
    if not installed.is_file():
        emit("make_venv", None, "creating the environment")
        _trash(layout, layout.env)          # an unfinished earlier attempt
        _check(supervisor.run([layout.python_exe, "-m", "venv", layout.env],
                              cwd=layout.home, sink=sink),
               "creating the environment", sink)
        emit("pip", None, "installing dependencies (several GB)")
        env = pipeline.build_env()
        env["PIP_CACHE_DIR"] = str(layout.home / "pip-cache")
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        env["PIP_NO_INPUT"] = "1"

        def pip_line(line):
            sink.note(line)
            if line.strip():
                emit("pip", None, line.strip()[:90])

        _check(supervisor.run(
            [layout.venv_python, "-m", "pip", "install", "--require-hashes",
             "--no-deps", "--only-binary", ":all:", "-r", layout.lock],
            cwd=layout.home, env=env, on_stdout=pip_line, sink=sink),
            "installing dependencies", sink)
        _check(supervisor.run([layout.venv_python, "-m", "pip", "check"],
                              cwd=layout.home, env=env, sink=sink),
               "checking the installed dependencies", sink)
        installed.write_text(layout.env.name, encoding="utf-8")
    ck()

    emit("fetch_models", 0.0, "downloading the speech model")
    models.fetch(layout.models, on_pct=lambda p: emit("fetch_models", p, None),
                 cancelled=cancelled)
    ck()

    emit("verify", None, "testing the GPU")
    smoke_test(layout, supervisor, sink)
    (layout.env / ".ready").write_text(layout.env.name, encoding="utf-8")
    collect_garbage(layout, sink.note)
