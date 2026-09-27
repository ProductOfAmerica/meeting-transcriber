# Working on Transcribe with Claude Code

Guidance for Claude Code sessions in this repo. Auto memory is off here
(`.claude/settings.json`), so lasting guidance goes in this file, which comes
along with every clone.

## Keep the user informed during long work

Before anything that runs for more than a few seconds (installs, builds,
background tests, anything that opens a window), say in one line what is
running, about how long it takes, and whether the user needs to do anything.
When it finishes, lead with the result. Silence during a long run reads as a
crash.

## Commands

- `.\run.cmd` runs the app from source: the window from `.venv-build\`, the
  speech engine from `venv\`. It rebuilds `venv\` when `requirements.lock`
  changes, so never `pip install` into `venv\` by hand, and run anything that
  imports pywebview (a test launcher, say) with `.venv-build\Scripts\python.exe`.
- `.\build.cmd` builds `Transcribe.exe` (also copied to the repo root) and
  `dist\THIRD_PARTY_LICENSES.txt`; `.\build.cmd env` only prepares
  `.venv-build\`.
- From a tool, cmd here skips the current folder when it looks up a command
  (`NoDefaultCurrentDirectoryInExePath` is set), so call `.\run.cmd` and
  `.\build.cmd`. Set `CI=1` so a failure doesn't wait at `pause`, and send the
  output to a file with stdin from NUL, as in this PowerShell line:
  `$env:CI = '1'; cmd /c '.\run.cmd > "%TEMP%\transcribe-run.log" 2>&1 < NUL'`.
  It returns as soon as the window's process starts; the window shows a
  moment later and stays open.
- To change a dependency, edit `requirements.in` or `requirements-build.in`
  and rerun the `uv pip compile` command at the top of its lock; uv keeps the
  lock's other pins. Nothing lints or formats the code.

## Architecture

- `backend\` holds all the Python, `ui\` the page (`index.html`, `app.js`,
  `app.css`), `tests\` the pytest suite, and `licenses\` the license texts
  that `Transcribe.spec` adds to `THIRD_PARTY_LICENSES.txt`. `venv\`,
  `.venv-build\`, `models\`, `runtime\`, `logs\`, `hf\`, `build\` and `dist\`
  are generated and ignored.
- The window: `backend\bootstrap.py` (one instance, visible startup errors)
  runs `backend\app.py`, which shows `ui\index.html` in pywebview. The page
  calls methods of `Api` in `app.py`, and `Api._emit` pushes events to
  `window.__on(channel, payload)` in `ui\app.js`.
- A job: `backend\pipeline.py` picks per-participant or mixed mode from the
  chosen file's folder, then runs one `backend\runner.py` process in the
  speech environment (`venv\` from source,
  `%LOCALAPPDATA%\Transcribe\envs\<key>\` for the exe) inside a Windows job
  object (`backend\procs.py`), so it dies with the window. The runner decodes
  with ffmpeg, transcribes with Parakeet through onnx-asr, diarizes mixed
  recordings with pyannote, and reports one JSON object per stdout line (the
  protocol is in its docstring). `backend\transcript.py` turns the words into
  the transcript, which `pipeline.py` writes to `~\Transcripts` by default.
- The exe is a thin launcher: on first run, `backend\firstrun.py` installs
  Python, ffmpeg, the speech environment and the models under
  `%LOCALAPPDATA%\Transcribe` (the layout is in its docstring). Pins live in
  `requirements.lock`, `backend\firstrun.py` (Python, ffmpeg, and the GPU,
  driver and disk thresholds) and `backend\models.py` (the models).
- Module-level imports in `backend\` come only from the standard library and
  `backend` itself, plus pywebview in `app.py`. The window's environment and
  the exe have no numpy, torch or ONNX Runtime, and the speech environment
  has no pywebview. The runner imports its libraries inside functions, which
  also lets the tests in `.venv-build\` import it.
- From the checkout, the exe bundles only `ui\`, `backend\` and
  `requirements.lock`, and runs the runner from a copy of `backend\` alone. A
  source run sees the whole checkout, so a file read from anywhere else works
  from source and breaks only in the exe.
- The page can call every method of `Api` whose name has no leading
  underscore. Those methods take no file paths from the page, and nothing on
  `Api` holds the Hugging Face token.

## Tests

- `.venv-build\Scripts\python.exe -m pytest -q` is the suite CI runs on every
  push. It needs no GPU. `tests\conftest.py` points the app's `settings.json`,
  `logs\`, `hf\` and transcripts folder at temporary paths, and
  `tests\fake_runner.py` stands in for the runner in the pipeline and process
  tests.
- Tests that need the speech runtime skip in `.venv-build\`, so CI never runs
  them. After changing `requirements.lock` or how `backend\runner.py` calls
  its libraries, run
  `venv\Scripts\python.exe -m pytest -q tests\test_api_guard.py tests\test_runner.py`
  (`.\run.cmd` builds `venv\`), then transcribe the test clip on the GPU
  through `venv\`:

  ```powershell
  .venv-build\Scripts\python.exe -c "import tempfile; from backend import firstrun, pipeline, procs; d = tempfile.TemporaryDirectory(); s = procs.Supervisor(); s.begin(); r = pipeline.run_job(mode='pertrack', audio=['backend/assets/smoke.wav'], out_dir=d.name, venv_dir='venv', code_dir='.', models_root='models', ffmpeg=firstrun.ffmpeg_dir('.') / 'ffmpeg.exe', supervisor=s, progress_cb=print); print(open(r['output_path'], encoding='utf-8').read())"
  ```

  The transcript should read "Alice was beginning to get very tired of
  sitting by her sister on the bank". This skips diarization, which needs a
  mixed recording and a Hugging Face token.

## UI tests

- Screenshot only the app's own window, with PrintWindow
  (PW_RENDERFULLCONTENT = 2), never a region of the screen. The user keeps
  working while tests run, and screen captures picked up their other windows
  and the lock screen.
- Drive the page over DevTools or UI Automation rather than the real mouse
  and keyboard. Say so first when a test has to move the pointer. For
  DevTools, start the app in the background with
  `.venv-build\Scripts\python.exe -c "import webview; webview.settings['REMOTE_DEBUGGING_PORT'] = 9222; from backend.app import main; main()"`;
  `http://127.0.0.1:9222/json/list` then gives the page's WebSocket URL. This
  launcher skips `backend\bootstrap.py` and its single-instance check.
- One Transcribe runs at a time: `backend\bootstrap.py` holds a named mutex
  that the exe and source runs share. If the user has Transcribe open, a
  launch through `.\run.cmd` or the exe shows "Transcribe is already
  running." and exits, so ask them to close it first.
- Test installs of the frozen exe go into a throwaway `LOCALAPPDATA`, deleted
  afterwards (`backend\firstrun.py` reads it):
  `$env:LOCALAPPDATA = "$env:TEMP\transcribe-home"; Start-Process .\Transcribe.exe`.
  A fresh install used about 16 GB at its peak (measured 2026-09-26).
- Close test runs with the app's own Close button, so pywebview removes its
  temporary WebView2 profile; a forced kill leaves a `%TEMP%\tmp*` folder
  with an `EBWebView` inside. UI Automation can press it. WebView2 exposes
  the page only after the first query, hence the retry:

  ```powershell
  Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
  $AE = [Windows.Automation.AutomationElement]; $PC = [Windows.Automation.PropertyCondition]
  $win = $AE::RootElement.FindAll('Children', $PC::new($AE::NameProperty, 'Transcribe')) |
      Where-Object { (Get-Process -Id $_.Current.ProcessId).ProcessName -in 'pythonw', 'python', 'Transcribe' } |
      Select-Object -First 1
  $close = $PC::new($AE::NameProperty, 'Close')
  for ($i = 0; $i -lt 40 -and -not ($btn = $win.FindFirst('Descendants', $close)); $i++) { Start-Sleep -Milliseconds 250 }
  $btn.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
  ```

## Pull requests and releases

- Branch off `main`, push, and open a pull request; the user merges it.
  After a merge, sync `main` and delete the merged branch.
- Tag a release only when the user says so. CI (`.github/workflows/release.yml`)
  builds the tag and creates a draft release. Run the draft's exe through a
  fresh install on a PC with an NVIDIA GPU; the user publishes it.
