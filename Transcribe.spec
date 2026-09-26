# PyInstaller spec for Transcribe.exe (windowed, no console, onefile).
#
# The exe is a thin launcher: the pywebview GUI plus ui/ assets. It bundles
# the backend/ sources and requirements.lock as data; on first run
# backend.firstrun copies backend/ to %LOCALAPPDATA%\Transcribe\code\<key>\
# and builds a venv there from requirements.lock. The heavy stack (ONNX
# Runtime, torch, pyannote) runs only in that venv, in the backend.runner
# subprocess, so it is excluded here.
#
# Build:  build.cmd
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(".")


def _tree(src, dest):
    """(file, folder) pairs for a source tree, without bytecode caches."""
    pairs = []
    for folder, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith(".pyc"):
                continue
            rel = os.path.relpath(folder, src)
            pairs.append((os.path.join(folder, name),
                          dest if rel == "." else os.path.join(dest, rel)))
    return pairs


datas = _tree("ui", "ui") + _tree("backend", "backend") + [
    ("requirements.lock", "."),
]
binaries = []
hiddenimports = [
    "backend.app", "backend.pipeline", "backend.appapi",
    "backend.transcript", "backend.writeprobe", "backend.firstrun",
    "backend.procs", "backend.models", "backend.fetch", "backend.fsutil",
    "webview.platforms.winforms", "clr", "proxy_tools",
]
# pywebview + its Windows EdgeChromium backend assets/hooks.
for _pkg in ("webview",):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    ["backend/bootstrap.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # The GUI process must never pull the multi-GB ML stack. mako, pygments,
    # PIL and numpy arrive through bottle's optional template engine (via
    # webview.http) when the build venv also holds the runtime stack.
    excludes=[
        "onnx_asr", "onnxruntime", "torch", "torchaudio", "torchcodec",
        "pyannote", "lightning", "huggingface_hub", "pandas", "scipy",
        "sklearn", "matplotlib", "mako", "pygments", "PIL", "numpy",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

# onefile: everything in EXE, no COLLECT.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Transcribe",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                # windowed -> no cmd window
    disable_windowed_traceback=False,
    icon=os.path.join("ui", "app.ico"),
)
