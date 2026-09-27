# Working on Transcribe with Claude Code

Guidance for Claude Code sessions in this repo. Auto memory is off here
(`.claude/settings.json`), so lasting guidance goes in this file, which comes
along with every clone. The `run-transcribe` skill
(`.claude/skills/run-transcribe/SKILL.md`) holds the commands for starting,
driving and closing the app and for checking the engine on the GPU.

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
  output to a file with stdin from NUL: the tool call then returns once the
  window starts, and the window stays open.
- To change a dependency, edit `requirements.in` or `requirements-build.in`
  and rerun the `uv pip compile` command at the top of its lock; uv keeps the
  lock's other pins. Nothing lints or formats the code.

## Pitfalls

- Module-level imports in `backend\` come only from the standard library and
  `backend` itself, plus pywebview in `app.py`. The window's environment and
  the exe have no numpy, torch or ONNX Runtime, and the speech environment
  has no pywebview. `backend\runner.py` imports its libraries inside
  functions, which also lets the tests in `.venv-build\` import it.
- From the checkout, the exe bundles only `ui\`, `backend\` and
  `requirements.lock`, and runs `backend\runner.py` from a copy of `backend\`
  alone. A source run sees the whole checkout, so a file read from anywhere
  else works from source and breaks only in the exe.
- The page can call every method of `Api` in `backend\app.py` whose name has
  no leading underscore. Those methods take no file paths from the page, and
  nothing on `Api` holds the Hugging Face token.

## Tests

- `.venv-build\Scripts\python.exe -m pytest -q` is the suite CI runs on every
  push. It needs no GPU, and `tests\conftest.py` points the app's
  `settings.json`, `logs\`, `hf\` and transcripts folder at temporary paths.
- Tests that need the speech runtime skip in `.venv-build\`, so CI never runs
  them. After changing `requirements.lock` or how `backend\runner.py` calls
  its libraries, run
  `venv\Scripts\python.exe -m pytest -q tests\test_api_guard.py tests\test_runner.py`
  (`.\run.cmd` builds `venv\`), then the GPU check in `run-transcribe`.
- CI doesn't run the UI flows either. After changing `ui\`, run
  `.venv-build\Scripts\python.exe tests\ui\flows_setup.py` and
  `.venv-build\Scripts\python.exe tests\ui\flows_e2e.py` (headless Edge
  through Playwright, about 2.5 minutes, no GPU). They drive the real page
  against `tests\ui\mock.js`, a stand-in for `Api` and its events. When those
  change, change the mock too; `tests\test_ui_mock.py` catches a method or
  argument count it misses.

## UI tests

- Screenshot only the app's own window, with PrintWindow
  (PW_RENDERFULLCONTENT = 2), never a region of the screen. The user keeps
  working while tests run, and screen captures picked up their other windows
  and the lock screen.
- Drive the page over DevTools or UI Automation rather than the real mouse
  and keyboard. Say so first when a test has to move the pointer.
- Test installs of the frozen exe go into a throwaway `LOCALAPPDATA`, deleted
  afterwards; a fresh install used about 16 GB at its peak (measured
  2026-09-26).
- Close test runs with the app's own Close button, so pywebview removes its
  temporary WebView2 profile; a forced kill leaves a `%TEMP%\tmp*` folder
  with an `EBWebView` inside.

## Pull requests and releases

- Branch off `main`, push, and open a pull request; the user merges it.
  After a merge, sync `main` and delete the merged branch.
- Tag a release only when the user says so. CI (`.github/workflows/release.yml`)
  builds the tag and creates a draft release. Run the draft's exe through a
  fresh install on a PC with an NVIDIA GPU; the user publishes it.
