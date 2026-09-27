---
name: run-transcribe
description: Start, drive and close the Transcribe window from a tool, test the exe in a throwaway install, or check the speech engine on the GPU. Use when running the app, testing the UI in the real window, confirming a change in the real app, or after changing requirements.lock or backend\runner.py.
---

# Run and check Transcribe

PowerShell commands, run from the repo root, each checked on 2026-09-27.
The rules in CLAUDE.md's UI tests section apply throughout.

## Start the app from a tool

```powershell
$env:CI = '1'; cmd /c '.\run.cmd > "%TEMP%\transcribe-run.log" 2>&1 < NUL'
```

It returns as soon as the window's process starts; the window shows a moment
later and stays open. The log holds run.cmd's output. Only one Transcribe runs
at a time (a named mutex in `backend\bootstrap.py`, shared by the exe and
source runs): if the user has it open, the launch shows "Transcribe is already
running." and exits, so ask them to close it first.

## Drive the page over DevTools

Start the app in the background; the command runs until the window closes:

```powershell
.venv-build\Scripts\python.exe -c "import webview; webview.settings['REMOTE_DEBUGGING_PORT'] = 9222; from backend.app import main; main()"
```

`http://127.0.0.1:9222/json/list` then gives the page's WebSocket URL. This
launcher skips `backend\bootstrap.py` and its single-instance check.

## Test the exe in a throwaway install

`backend\firstrun.py` reads `LOCALAPPDATA`, so point it at an empty folder:

```powershell
New-Item -ItemType Directory "$env:TEMP\transcribe-home" | Out-Null; $env:LOCALAPPDATA = "$env:TEMP\transcribe-home"; Start-Process .\Transcribe.exe
```

Delete the folder afterwards. Windows adds empty `Microsoft\Windows\Caches`
folders to it even when the app writes nothing.

## Close the window

Press the page's Close button through UI Automation, so pywebview removes its
temporary WebView2 profile. WebView2 exposes the page only after the first
query, hence the retry:

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

Afterwards no `%TEMP%\tmp*` folder with an `EBWebView` inside should be new.

## Check the speech engine on the GPU

Transcribe the test clip through `venv\`, as setup's own check does (about
5 s here):

```powershell
.venv-build\Scripts\python.exe -c "import tempfile; from backend import firstrun, pipeline, procs; d = tempfile.TemporaryDirectory(); s = procs.Supervisor(); s.begin(); r = pipeline.run_job(mode='pertrack', audio=['backend/assets/smoke.wav'], out_dir=d.name, venv_dir='venv', code_dir='.', models_root='models', ffmpeg=firstrun.ffmpeg_dir('.') / 'ffmpeg.exe', supervisor=s, progress_cb=print); print(open(r['output_path'], encoding='utf-8').read())"
```

The transcript should read "Alice was beginning to get very tired of sitting
by her sister on the bank". This skips diarization, which needs a mixed
recording and a Hugging Face token.
