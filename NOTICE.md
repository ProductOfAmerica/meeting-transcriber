# Third-party notices

This project is licensed under the MIT License (see `LICENSE`).

It does **not** bundle or redistribute any of the components below. On
first run the app downloads a portable Python and ffmpeg (pinned and
sha256-verified) and then `pip`-installs the Python libraries from PyPI
(and the PyTorch CUDA index) into a private `venv` under
`%LOCALAPPDATA%\Transcribe`. Each component remains under its own license,
listed here for credit.

## Downloaded runtime (fetched on first run, not bundled)

| Component | Source | License |
|---|---|---|
| Portable CPython 3.11 | [astral-sh/python-build-standalone](https://github.com/astral-sh/python-build-standalone) (`...-x86_64-pc-windows-msvc-install_only.tar.gz`) | Python-2.0 (PSF), plus its own bundled components (OpenSSL, etc.) under their respective licenses, as documented by that project |
| ffmpeg | [GyanD/codexffmpeg](https://github.com/GyanD/codexffmpeg) (`ffmpeg-*-essentials_build.zip`, a static build) | **GPLv3** (FFmpeg). Source and build scripts: https://www.gyan.dev/ffmpeg/builds/ and https://ffmpeg.org/ |

`ffmpeg.exe` is invoked only as a separate subprocess; it is not linked
into this app. Shipping it alongside an MIT program is mere aggregation:
the GPL applies to ffmpeg itself (source available at the links above),
not to this project, which remains MIT. The exact pinned versions and
their verified sha256 hashes are in `backend/firstrun.py`.

## Python libraries (installed by `requirements.txt`)

| Library | License |
|---|---|
| WhisperX | BSD-2-Clause |
| faster-whisper | MIT |
| CTranslate2 | MIT |
| PyTorch (torch) | BSD-3-Clause |
| torchaudio | BSD-3-Clause |
| Hugging Face Transformers | Apache-2.0 |
| huggingface_hub | Apache-2.0 |
| pyannote.audio | MIT |
| NumPy | BSD-3-Clause |
| pywebview | BSD-3-Clause |

## Build tool (not shipped, not linked into the app)

| Tool | License | Note |
|---|---|---|
| PyInstaller | GPLv2 **with a bootloader exception** | Used only by `build.cmd` to build `Transcribe.exe`. The exception explicitly permits using PyInstaller to freeze and distribute applications under any license, so it places no licensing obligation on `Transcribe.exe` or this project. |

## Speaker-diarization model (single-mixed-file path only)

The single-mixed-file diarization path downloads
**`pyannote/speaker-diarization-community-1`** from Hugging Face at runtime.

- License: **CC-BY-4.0**
- **Gated**: requires a free Hugging Face account, accepting the model's
  conditions on its model page, and a read token in `HF_TOKEN`.
- The per-participant path (Zoom "Audio Record" folders) does **not** use
  this model and needs no token.

This model is not redistributed by this repository; it is fetched directly
from Hugging Face by the end user under their own account.
