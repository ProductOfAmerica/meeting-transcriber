# Working on Transcribe with Claude Code

Guidance for Claude Code sessions in this repo. It lives in the repo, not in
per-PC memory, so it comes along with every clone.

## Keep the user informed during long work

Before anything that runs for more than a few seconds (installs, builds,
background tests, anything that opens a window), say in one line what is
running, about how long it takes, and whether the user needs to do anything.
When it finishes, lead with the result. Silence during a long run reads as a
crash.

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
  with an `EBWebView` inside.

## Pull requests and releases

- Branch off `main`, push, and open a pull request; the user merges it.
  After a merge, sync `main` and delete the merged branch.
- Tag a release only when the user says so. CI (`.github/workflows/release.yml`)
  builds the tag and creates a draft release. Run the draft's exe through a
  fresh install on a PC with an NVIDIA GPU; the user publishes it.
