# Working on Transcribe with Claude Code

Guidance for Claude Code sessions in this repo. It lives in the repo, not in
per-PC memory, so it comes along with every clone.

## Keep the user informed during long work

Before anything that runs for more than a few seconds (installs, builds,
background tests, anything that opens a window), say in one line what is
running, about how long it takes, and whether the user needs to do anything.
When it finishes, lead with the result. Silence during a long run reads as a
crash.

## Running from source

- `.\run.cmd` runs the app from source: the window from `.venv-build\`, the
  speech engine from `venv\`. It rebuilds `venv\` when `requirements.lock`
  changes, so never `pip install` into `venv\` by hand, and run anything that
  imports pywebview (a test launcher, say) with `.venv-build\Scripts\python.exe`.
- From a tool, cmd here skips the current folder when it looks up a command
  (`NoDefaultCurrentDirectoryInExePath` is set), so call `.\run.cmd` and
  `.\build.cmd`. Set `CI=1` so a failure doesn't wait at `pause`, and send the
  output to a file with stdin from NUL: the tool call then returns once the
  window starts, and the window stays open.

## UI tests

- Screenshot only the app's own window, with PrintWindow
  (PW_RENDERFULLCONTENT = 2), never a region of the screen. The user keeps
  working while tests run, and screen captures picked up their other windows
  and the lock screen.
- Drive the page over DevTools (`webview.settings["REMOTE_DEBUGGING_PORT"]`
  in a test launcher) or UI Automation rather than the real mouse and
  keyboard. Say so first when a test has to move the pointer.
- Test installs of the frozen exe go into a throwaway `LOCALAPPDATA`, deleted
  afterwards; a fresh install used about 16 GB at its peak (measured
  2026-09-26).
- Close test runs with the app's own Close button, so pywebview removes its
  temporary WebView2 profile; a forced kill leaves a `%TEMP%\tmp*` folder
  with an `EBWebView` inside. UI Automation can press it: search the
  Transcribe window for a Button named Close. WebView2 exposes the page only
  after the first query, so retry the search.

## Pull requests and releases

- Branch off `main`, push, and open a pull request; the user merges it.
  After a merge, sync `main` and delete the merged branch.
- Tag a release only when the user says so. CI (`.github/workflows/release.yml`)
  builds the tag and creates a draft release. Run the draft's exe through a
  fresh install on a PC with an NVIDIA GPU; the user publishes it.
