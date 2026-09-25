# Transcribe

Transcribe is a Windows desktop app for turning Zoom recordings into clean,
speaker-labeled text transcripts that are easy to paste into an LLM.

It runs WhisperX locally on your NVIDIA GPU. Your audio stays on your
machine. The app is built for meeting recordings, especially Zoom folders
that contain one audio track per participant.

## Who This Is For

Use Transcribe if you want:

- a local transcript instead of a cloud upload
- speaker labels from Zoom participant tracks
- one readable `.transcript.txt` file in `C:\Users\<you>\Transcripts`
- a simple app window instead of a command-line workflow

Transcribe is currently Windows-only and NVIDIA-only. There is no CPU mode.

## Current Project Status

This repository does not store `Transcribe.exe`.

The executable is a build artifact, not source code. It is intentionally
ignored by git, along with `build/` and `dist/`. Non-developers should use a
release build from the [GitHub Releases page][releases] when one is published.
If there is no release asset yet, there is no one-click non-developer install
yet.
Developers can build the launcher from source with `build.cmd`.

## Quick Start for Non-Developers

Before you start, you need:

- Windows 10 or 11, 64-bit
- an NVIDIA GPU with a recent driver
- an internet connection for first-time setup
- about 12 GB of free disk space for Python packages, GPU libraries, and model
  caches

Then:

1. Open the [GitHub Releases page][releases].
2. Download the latest `Transcribe.exe` release asset.
   The exe is not committed to this repository.
3. Double-click `Transcribe.exe`.
4. If Windows SmartScreen appears, choose **More info**, then **Run anyway**.
5. On first launch, click **Install now**.
   Transcribe installs its private runtime into `%LOCALAPPDATA%\Transcribe`.
6. When setup finishes, choose a recording file.
7. Click **Go**.
8. Find the transcript in `C:\Users\<you>\Transcripts`.

The first setup can take several minutes. Later launches are fast. After the
runtime and models are cached, normal transcription works offline.

## What File Should I Pick?

### Best: Zoom Per-Participant Audio

Pick a file from a Zoom meeting folder that contains an `Audio Record`
subfolder. You can pick a participant track inside `Audio Record`, or another
media file in the meeting folder.

Transcribe detects the participant tracks automatically. Each track already
belongs to one person, so speaker names are much more reliable. This mode does
not need a Hugging Face token.

### Supported: One Mixed Recording

Pick a single mixed audio or video file such as `.m4a`, `.mp3`, `.wav`, or
`.mp4`.

Transcribe will run speaker diarization to guess who spoke when. Labels will
look like `SPEAKER_00`, `SPEAKER_01`, and so on. This mode needs a Hugging Face
token because the diarization model is gated.

## Hugging Face Token

Skip this section if you only use Zoom per-participant recordings.

For one mixed recording, speaker diarization uses
`pyannote/speaker-diarization-community-1`.

1. Create a free account at https://huggingface.co.
2. Open https://huggingface.co/pyannote/speaker-diarization-community-1.
3. Accept the model's access conditions.
4. Create a read token at https://huggingface.co/settings/tokens.
5. Open PowerShell and run:

```powershell
setx HF_TOKEN your_token_here
```

Close and reopen Explorer, PowerShell, or any launcher you use before starting
Transcribe. Windows environment variables are only visible to newly opened
processes.

The token is read from the environment. Transcribe does not write it to a
settings file.

## What Gets Installed?

The release exe is a small launcher. On first run it downloads and installs:

- portable Python
- ffmpeg
- the pinned WhisperX runtime from `requirements.txt`
- CUDA runtime libraries needed by the Python packages
- Whisper, alignment, and diarization models as they are first used

Everything goes under:

```text
%LOCALAPPDATA%\Transcribe
```

Nothing is written next to the exe.

To uninstall or force a clean setup, delete:

```text
%LOCALAPPDATA%\Transcribe
```

Then run the release exe again.

## Updates

Transcribe does not silently upgrade WhisperX or its model stack in the
background.

The ML dependencies are pinned in `requirements.txt` because the app calls
WhisperX's Python APIs directly. A new release build with changed pins will
cause the private runtime to rebuild on first launch.

If the app shows that a newer WhisperX package is available, that is a package
update notice. It is not an automatic model download button.

## Troubleshooting

### There Is No `Transcribe.exe` in the Repo

That is expected. Use a published release asset, or build the launcher from
source.

### Setup Fails Partway

Run setup again. It reuses completed downloads where possible.

If it keeps failing, delete `%LOCALAPPDATA%\Transcribe` and start again.

### NVIDIA or CUDA Error

Update your NVIDIA driver, then rerun setup.

This app requires an NVIDIA GPU and the CUDA 12.8 runtime used by the pinned
PyTorch wheels. There is no CPU fallback.

### `HF_TOKEN` Is Missing

This only applies to one mixed recording. Follow the Hugging Face token steps
above, then start Transcribe from a newly opened window.

### CUDA Out of Memory

Lower `BATCH_SIZE` in `backend/pipeline.py`, then rebuild or rerun from source.
The default is tuned for a 24 GB GPU.

## Build the Launcher From Source

This builds the small release launcher. It does not install the multi-GB
WhisperX runtime into your development environment.

Prerequisite: Python 3.11 with the Windows `py` launcher available. Check with
`py -3.11 --version`.

```powershell
git clone https://github.com/ProductOfAmerica/whisperx-meeting-transcriber.git
cd whisperx-meeting-transcriber
py -3.11 -m venv venv
venv\Scripts\python.exe -m pip install -U pip
venv\Scripts\python.exe -m pip install pyinstaller==6.20.0 pywebview==6.2.1
build.cmd
```

The build output is ignored by git:

```text
Transcribe.exe
dist\
build\
```

Ship the single `Transcribe.exe` file from a release. The user's first launch
will install the runtime into `%LOCALAPPDATA%\Transcribe`.

## Run From Source

Use this path only if you are developing or testing the full transcription
stack locally.

```powershell
git clone https://github.com/ProductOfAmerica/whisperx-meeting-transcriber.git
cd whisperx-meeting-transcriber
py -3.11 -m venv venv
venv\Scripts\python.exe -m pip install -U pip
venv\Scripts\python.exe -m pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu128
venv\Scripts\python.exe -m backend.bootstrap
```

Running from source uses the repo's `venv\` directly and skips the first-run
installer.

## Tests

The unit tests do not require a GPU. In a lightweight developer environment:

```powershell
venv\Scripts\python.exe -m pip install pytest pywebview==6.2.1
venv\Scripts\python.exe -m pytest -q
```

The API guard test checks WhisperX only when WhisperX is installed. Before
shipping a dependency bump, run the full test suite in an environment with the
pinned runtime installed, then run a real GPU transcription.

## Maintainer Notes

Important pins live in:

- `requirements.txt` for WhisperX, PyTorch, pyannote, and CUDA package pins
- `backend/firstrun.py` for portable Python and ffmpeg downloads
- `backend/pipeline.py` for model choice and batch size

Treat changes to these files as release changes. They can alter install size,
GPU compatibility, output quality, or runtime behavior.

## License

MIT. See `LICENSE`.

Third-party notices are in `NOTICE.md`.

[releases]: https://github.com/ProductOfAmerica/whisperx-meeting-transcriber/releases
