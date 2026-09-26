# Transcribe

Transcribe is a Windows desktop app that turns Zoom recordings into clean,
speaker-labeled text transcripts that are easy to paste into an LLM.

Everything runs on your own PC and NVIDIA GPU. Speech recognition uses
NVIDIA's Parakeet TDT 0.6B v2 model (English) through ONNX Runtime; speaker
detection for single mixed recordings uses pyannote's
speaker-diarization-community-1. Your audio never leaves your machine.

It works best with Zoom meetings recorded with one audio track per
participant: every line of the transcript then carries the speaker's name.

## Requirements

- Windows 10 or 11, 64-bit, with the Microsoft Edge WebView2 Runtime. It is
  built into Windows 11; if it is missing, Transcribe offers Microsoft's
  download page.
- An NVIDIA GTX 16 or RTX 20 series GPU or newer, with at least 6 GB of
  memory. GTX 10 series and older cards are not supported, and neither are
  4 GB cards such as the GTX 1650 or many laptop RTX 2050 and 3050 models.
  There is no CPU mode.
- NVIDIA driver 525 or newer.
- About 17 GB of free disk space during setup; about 11 GB once installed.
- An internet connection for the first-time setup.
- No administrator rights: everything installs into your user profile.

Setup checks the GPU, the driver and the free disk space before it downloads
anything.

## Quick start

1. Download `Transcribe.exe` from the [Releases page][releases].
2. Double-click it. If Windows SmartScreen appears, choose **More info**, then
   **Run anyway** (the exe is not code-signed).
3. Click **Install now**. Setup installs its runtime into
   `%LOCALAPPDATA%\Transcribe`. It takes several minutes (about 7 on a fast
   connection) and ends by transcribing a short test clip on your GPU.
4. Click **Choose a recording** and pick a file (see below).
5. Click **Transcribe**.
6. The transcript is saved to `C:\Users\<you>\Transcripts` as
   `<recording>.transcript.txt`; transcribing the same recording again adds
   `(2)`, `(3)` and so on instead of replacing it. **Copy transcript** puts it
   on the clipboard, ready to paste into an LLM.

To save transcripts in another folder, put
`{"last_output_dir": "D:\\Transcripts"}` in
`%LOCALAPPDATA%\Transcribe\settings.json` and start Transcribe again.

## Which file to pick

### Best: a Zoom meeting with one audio track per participant

Zoom's local recording can save a separate audio file for each participant (an
option in Zoom's recording settings). Such a meeting folder has an
`Audio Record` subfolder. Pick any media file in the meeting folder, or a
track inside `Audio Record`: Transcribe finds all the tracks, transcribes each
one, and labels every line with the participant's name. No Hugging Face token
is needed.

### Also supported: one mixed recording

Pick a single audio or video file (`.m4a`, `.mp3`, `.wav`, `.mp4` and other
common formats). Transcribe detects who spoke when and labels the speakers
`SPEAKER_00`, `SPEAKER_01`, and so on. This needs a free Hugging Face token.

## Hugging Face token (mixed recordings only)

The speaker-detection model is free but gated: its authors ask you to accept
their conditions with a Hugging Face account. When you pick a mixed recording,
the recording card walks you through it:

1. **Accept the model's terms** opens the model page. Sign in or create a free
   account, then accept.
2. **Create a read token** opens your Hugging Face token settings. Create a
   token of type Read.
3. Paste the token and click **Save**. Transcribe checks it with Hugging Face
   and stores it in Windows Credential Manager (as `Transcribe/huggingface`).
   It never shows the token again or writes it to its own files or logs.

**Change** replaces the saved token and **Forget the saved token** removes it.
Without a saved token, an `HF_TOKEN` environment variable is used instead.

## Offline use

After setup, per-participant recordings work without internet access. A mixed
recording needs internet access the first time only, to download the speaker
model (32 MB) with your token.

## What gets installed

Everything goes under `%LOCALAPPDATA%\Transcribe`:

- `runtime\`: a portable Python and ffmpeg
- `envs\`: the Python environment with the speech runtime (PyTorch, ONNX
  Runtime, pyannote), installed from `requirements.lock` with every package
  checked against its pinned hash
- `models\`: the Parakeet and Silero VAD models (about 2.3 GB), checked
  against pinned hashes
- `hf\`: the speaker model, after the first mixed recording
- `code\` and `logs\`

Nothing is written next to the exe. A newer `Transcribe.exe` reuses the
environment unless its pinned packages changed, and old folders are removed
automatically.

To uninstall, delete `%LOCALAPPDATA%\Transcribe` and the exe. If you saved a
Hugging Face token, first remove it with **Forget the saved token**, or delete
`Transcribe/huggingface` under Windows Credentials in Credential Manager.

## Troubleshooting

- **Setup fails.** The message names the cause when it can: no NVIDIA GPU, an
  unsupported GPU, an old driver, a full disk, a network error. Click
  **Try again**; completed downloads are reused. The log is in
  `%LOCALAPPDATA%\Transcribe\logs\setup-*.log`.
- **A transcription fails.** Its log is in
  `%LOCALAPPDATA%\Transcribe\logs\run-*.log`.
- **The GPU runs out of memory.** Transcribe needs at least 6 GB of GPU
  memory. Close other programs that use the GPU, such as games or other AI
  tools, and try again.
- **Hugging Face refuses the speaker model.** Make sure the account that
  created the token accepted the model's terms, then use **Change** to paste
  the token again.
- **WebView2 is missing.** Transcribe offers Microsoft's WebView2 Runtime
  download page; install it, then start Transcribe again.
- **Start over.** Delete `%LOCALAPPDATA%\Transcribe` and run the exe again.

## Build from source

Needs Python 3.11 with the Windows `py` launcher (the python.org installer
includes it).

```powershell
git clone https://github.com/ProductOfAmerica/meeting-transcriber.git
cd meeting-transcriber
build.cmd
```

On first use, `build.cmd` creates `.venv-build\` from the hashed
`requirements-build.lock`. It then builds `Transcribe.exe` (also copied to the
repo root) and `dist\THIRD_PARTY_LICENSES.txt`. The exe installs its runtime
on first run, like a release build.

## Run from source

For development. Needs a supported GPU and about 11 GB of disk space.

```powershell
py -3.11 -m venv venv
venv\Scripts\python.exe -m pip install --require-hashes --no-deps -r requirements.lock
venv\Scripts\python.exe -m pip install pywebview==6.2.1 pytest==9.1.1
venv\Scripts\python.exe -m backend.devsetup
venv\Scripts\python.exe -m backend.bootstrap
```

`backend.devsetup` downloads the pinned models and ffmpeg into `models\` and
`runtime\`. Running from source skips the first-run installer.

## Tests

```powershell
.venv-build\Scripts\python.exe -m pytest -q
```

The tests need no GPU. `tests\test_api_guard.py` checks the pinned speech
runtime's APIs and runs only where that runtime is installed (the source-run
`venv\`).

## Maintainer notes

- `requirements.in` holds the runtime's top-level pins and compiles to the
  hashed `requirements.lock`; `requirements-build.in` holds the build and test
  tools and compiles to `requirements-build.lock`. Each lock starts with the
  `uv` command that produced it.
- `Transcribe.spec` classifies every package in the build lock as shipped or
  build-only and fails the build if build-only code would be bundled. It also
  writes `THIRD_PARTY_LICENSES.txt` and fails if any bundled file has no
  license attribution; license texts the packages do not include are in
  `licenses\`.
- Other pins: the portable Python, ffmpeg, and the GPU, driver and disk
  thresholds in `backend\firstrun.py`; the speech models and the speaker
  model's revision in `backend\models.py`.
- Every push builds and tests on GitHub Actions. To release, push a tag such
  as `v0.1.0`: the workflow creates a draft release with `Transcribe.exe` and
  `THIRD_PARTY_LICENSES.txt`. Run the draft's exe through a fresh install on a
  PC with an NVIDIA GPU, then publish it.

## License

MIT; see `LICENSE`. Third-party notices are in `NOTICE.md`, and each release
includes `THIRD_PARTY_LICENSES.txt` for the components bundled in the exe.

[releases]: https://github.com/ProductOfAmerica/meeting-transcriber/releases
