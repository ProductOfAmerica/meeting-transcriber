# PyInstaller spec for the Transcribe GUI shell (windowed, no console).
#
# Bundles ONLY the lightweight pywebview shell + ui/ assets. whisperx/
# torch are deliberately excluded: the GUI never imports them; the heavy
# ML runs in backend/runner.py, which pipeline.py spawns as a separate
# `venv\Scripts\python.exe -m backend.runner` subprocess. So the exe is a
# thin launcher that must sit in whisperx-work/ next to venv/ and the
# backend/ source (backend.app resolves ROOT from sys.executable when
# frozen; ui/ is read from sys._MEIPASS).
#
# Build:  venv\Scripts\pyinstaller.exe Transcribe.spec --noconfirm
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(".")

datas = [
    ("ui", "ui"),                  # index.html/app.css/app.js/app.ico
    # The first-run installer extracts these out of _MEIPASS into the
    # app home: the venv-python runner subprocess imports backend.* with
    # cwd there, and the bootstrap pip install reads requirements.txt.
    ("backend", "backend"),
    ("requirements.txt", "."),
]
binaries = []
hiddenimports = [
    "backend.app", "backend.pipeline", "backend.appapi",
    "backend.transcript", "backend.writeprobe", "backend.firstrun",
    "backend.procs", "backend.models",
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
    # The GUI process must never pull the multi-GB ML stack.
    excludes=[
        "onnx_asr", "onnxruntime", "torch", "torchaudio", "torchcodec",
        "pyannote", "lightning", "huggingface_hub", "pandas", "scipy",
        "sklearn", "matplotlib",
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
