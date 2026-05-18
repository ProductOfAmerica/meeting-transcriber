# Transcribe

A Windows desktop app that turns a Zoom recording into a clean,
speaker-labeled, LLM-ready text transcript. Pick a recording, click Go, get
a `.transcript.txt` next to it.

It runs WhisperX (large-v3) locally on your NVIDIA GPU. Audio never leaves
your machine. Network is used only on the first run (to install the engine
and download models) and, for one mode only, a Hugging Face model gate
(see Diarization below).

## Get started

1. Download **`Transcribe.exe`**.
2. Double-click it. Windows SmartScreen will warn that it is unsigned the
   first time: click **More info → Run anyway**.
3. The first run shows a one-time setup screen. Click **Install now**. It
   downloads a private Python runtime + ffmpeg and builds the ~8&nbsp;GB
   transcription environment into `%LOCALAPPDATA%\Transcribe`, with a live
   progress bar. This takes several minutes and needs an internet
   connection.
4. When setup finishes the normal app appears. **Every later launch is
   instant and works offline.**

The exe is just a launcher. It writes nothing next to itself, so it is
fine to run it straight from your Downloads folder. To uninstall, delete
`%LOCALAPPDATA%\Transcribe` (and the exe). To repair a broken install,
delete that folder and reopen the exe.

The only prerequisite that cannot be installed for you is hardware: an
**NVIDIA GPU with a recent driver** (see Requirements).

## Two modes (picked automatically)

- **Per-participant** (recommended, most accurate speaker labels): point it
  at a Zoom meeting folder that contains an **`Audio Record`** subfolder with
  one audio file per participant. Each track is one known speaker, so labels
  are exact. Needs **no token**.
- **Single mixed file**: point it at one combined audio file. WhisperX
  transcribes it and pyannote guesses who spoke when. Labels are
  machine-assigned (`SPEAKER_00/01/...`, rename afterward) and approximate.
  This mode needs a free Hugging Face token (see Diarization).

## Requirements

- **Windows 10 or 11** (64-bit).
- **An NVIDIA GPU with a recent driver** (CUDA 12.8 runtime). This app is
  NVIDIA-only; there is no CPU fallback by design. ~10GB+ free VRAM is
  comfortable for `large-v3`. On a smaller card, lower `BATCH_SIZE` in
  [`backend/pipeline.py`](backend/pipeline.py) (default `16`) if you hit
  out-of-memory. Update your driver from nvidia.com if setup's GPU check
  fails.
- **Internet for the first run only**: ~160&nbsp;MB runtime (portable
  Python + ffmpeg) + ~5&nbsp;GB of Python packages, then ~3&nbsp;GB of
  models the first time you transcribe. After that it is fully offline.
- **~8&nbsp;GB free disk** in `%LOCALAPPDATA%` for the environment.

Python and ffmpeg do **not** need to be installed on the machine; the app
installs its own private copies. You do not run any script.

## Diarization (single mixed file only)

The per-participant path needs none of this. For a single mixed audio file,
speaker separation uses the gated model
`pyannote/speaker-diarization-community-1`:

1. Create a free account at https://huggingface.co
2. Open
   https://huggingface.co/pyannote/speaker-diarization-community-1
   and accept the access conditions (the gate).
3. Create a **read** token at https://huggingface.co/settings/tokens
4. In a terminal: `setx HF_TOKEN your_token_here`
5. **Open a new terminal/Explorer** (so the new variable is visible), then
   launch `Transcribe.exe`.

The token is read from the environment only. It is never written to a file
or shown by the app.

## How it works

`Transcribe.exe` is a small [pywebview](https://pywebview.flowrl.com/) shell
(`backend/app.py` + `ui/`). On first run `backend/firstrun.py` builds a
private environment under `%LOCALAPPDATA%\Transcribe` (a pinned,
sha256-verified portable CPython and ffmpeg, then a `venv` from the pinned
`requirements.txt`). When you start a job it spawns
`venv\Scripts\python.exe -m backend.runner` as a subprocess
(`backend/pipeline.py`), which imports WhisperX directly and streams
structured progress events back to the UI. Transcripts are assembled by
`backend/transcript.py`. The heavy ML stack is deliberately kept out of the
exe (see `Transcribe.spec`) so the launcher stays small and starts fast.

Per-participant tracks share one clock. Each track is split into turns on
that speaker's own pauses and the turns are interleaved by start time, so a
back-channel ("yeah", "right") on one track never fragments another
speaker's sentence.

## Troubleshooting

Failures show an actionable message in the app. Common ones:

- **Setup fails partway** — usually a dropped connection. Click **Try
  again** (it resumes; it will not re-download what it already has). If it
  keeps failing, delete `%LOCALAPPDATA%\Transcribe` and reopen the exe.
- **"This app needs an NVIDIA GPU with a recent driver (CUDA 12.8
  runtime)..."** — no NVIDIA GPU, or the driver is too old for CUDA 12.8.
  Update the NVIDIA driver from nvidia.com, delete
  `%LOCALAPPDATA%\Transcribe`, and run setup again.
- **"Download integrity check FAILED"** — the pinned Python/ffmpeg
  download did not match its expected hash (corruption or a moved asset).
  Retry; if it persists the pinned URL needs updating (see below).
- **"The transcription environment isn't installed yet"** — close and
  reopen Transcribe to run first-time setup.
- **"HF_TOKEN is not set"** (single mixed file only) — follow the
  Diarization steps above; remember to open a new terminal after `setx`.
- **Hugging Face 401/403** — the pyannote gate was not accepted, or the
  token lacks read scope. Re-check steps 2 and 3 in Diarization.
- **CUDA out of memory** — lower `BATCH_SIZE` in
  [`backend/pipeline.py`](backend/pipeline.py).

## Building from source (developers)

End users do not need this. To build the exe yourself:

```
git clone <this repo>
cd whisperx-work
py -3.11 -m venv venv
venv\Scripts\python.exe -m pip install -U pip
venv\Scripts\python.exe -m pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu128
venv\Scripts\python.exe -m pip install pyinstaller==6.20.0
build.cmd
```

`build.cmd` produces `Transcribe.exe`. Run the unit tests with
`venv\Scripts\python.exe -m pytest -q` (no GPU needed). Running from source
(`python -m backend.bootstrap`) uses this in-tree `venv\` directly and
skips the first-run installer.

The runtime pins live in `requirements.txt` (the WhisperX stack) and in
`backend/firstrun.py` (`PY_URL`/`PY_SHA256`, `FFMPEG_URL`/`FFMPEG_SHA256`).
All are deliberate; bumping any of them is a conscious action (then run
`tests/test_api_guard.py` + `tests/test_firstrun.py` and a real GPU run).

## License

MIT, see [`LICENSE`](LICENSE). Third-party credits, including the
downloaded portable Python, ffmpeg, and the gated pyannote model, are in
[`NOTICE.md`](NOTICE.md). This project installs and downloads (does not
bundle) its dependencies.
