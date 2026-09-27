@echo off
rem Runs Transcribe from source, in one command. The window runs from
rem .venv-build\, with the packages Transcribe.exe bundles ("build.cmd env"
rem prepares it). The speech engine runs from venv\, installed from
rem requirements.lock with the same pip flags as the exe's first-run setup, plus
rem pytest for tests\test_api_guard.py. venv\ is rebuilt whenever
rem requirements.lock changes, so don't install anything else into it.
rem backend.devsetup downloads the pinned models and ffmpeg when they're missing.
rem Settings, logs and downloads stay in this checkout, not in
rem %LOCALAPPDATA%\Transcribe.
setlocal
cd /d "%~dp0"

call "%~dp0build.cmd" env
if errorlevel 1 exit /b 1

set "APY=.venv-build\Scripts\python.exe"
set "APYW=.venv-build\Scripts\pythonw.exe"
set "VENV=venv"
set "VPY=venv\Scripts\python.exe"

rem venv\ keeps copies of the locks it was completed from. Compare them ignoring
rem line endings: uv writes the locks with LF and a git checkout rewrites them
rem with CRLF, and a byte compare (fc /b, as in build.cmd) would then rebuild
rem several GB for nothing.
set "SAME=import sys; r = lambda p: open(p, 'rb').read().replace(b'\r\n', b'\n'); sys.exit(r(sys.argv[1]) != r(sys.argv[2]))"
if not exist "%VPY%" goto :rebuild
"%APY%" -c "%SAME%" requirements.lock "%VENV%\requirements.lock" 2>nul
if errorlevel 1 goto :rebuild
"%APY%" -c "%SAME%" requirements-build.lock "%VENV%\requirements-build.lock" 2>nul
if errorlevel 1 goto :pytest
goto :models

:rebuild
echo Installing the speech environment in %VENV%\ from requirements.lock (several GB, takes minutes) ...
rem A Python running from the env keeps its launcher open, and deleting the env
rem under it would remove every file it doesn't hold. Stop instead.
if exist "%VENV%\Scripts\python.exe" 2>nul (>>"%VENV%\Scripts\python.exe" (call )) || goto :inuse
if exist "%VENV%\Scripts\pythonw.exe" 2>nul (>>"%VENV%\Scripts\pythonw.exe" (call )) || goto :inuse
if exist "%VENV%" rmdir /s /q "%VENV%"
rem rmdir reports no error when it can't delete everything.
if exist "%VENV%" goto :inuse
if defined TRANSCRIBE_BUILD_PYTHON (
  "%TRANSCRIBE_BUILD_PYTHON%" -m venv "%VENV%"
) else (
  py -3.11 -m venv "%VENV%"
)
if errorlevel 1 goto :nopython
"%VPY%" -c "import sys; sys.exit(sys.version_info[:2] != (3, 11))"
if errorlevel 1 goto :wrongpython
rem The same install as the exe's first-run setup (backend\firstrun.py).
"%VPY%" -m pip install --disable-pip-version-check --require-hashes --no-deps --only-binary :all: -r requirements.lock
if errorlevel 1 goto :fail

:pytest
rem pytest, pluggy and iniconfig, hash-checked from the build lock. pytest's
rem other dependencies are pinned in requirements.lock as well; pip check
rem proves the two agree.
echo Installing pytest in %VENV%\ ...
"%VPY%" -c "import re, sys; t = open(sys.argv[1]).read(); b = re.findall(r'^(?:pytest|pluggy|iniconfig)==.*?(?=^\S|\Z)', t, re.M | re.S); len(b) == 3 or sys.exit('expected pytest, pluggy and iniconfig in ' + sys.argv[1]); open(sys.argv[2], 'w').write(''.join(b))" requirements-build.lock "%VENV%\pytest.req"
if errorlevel 1 goto :fail
"%VPY%" -m pip install --disable-pip-version-check --require-hashes --no-deps --only-binary :all: -r "%VENV%\pytest.req"
if errorlevel 1 goto :fail
"%VPY%" -m pip check
if errorlevel 1 goto :fail
rem Copied last, so an unfinished install is redone next time.
copy /y requirements.lock "%VENV%\requirements.lock" >nul
if errorlevel 1 goto :fail
copy /y requirements-build.lock "%VENV%\requirements-build.lock" >nul
if errorlevel 1 goto :fail

:models
echo Checking the speech models and ffmpeg ...
"%APY%" -m backend.devsetup
if errorlevel 1 goto :fail

echo Starting Transcribe ...
start "" "%APYW%" -m backend.bootstrap
if errorlevel 1 goto :fail
exit /b 0

:nopython
echo.
echo Python 3.11 was not found. Install it from python.org with the "py launcher"
echo option, or set TRANSCRIBE_BUILD_PYTHON to a Python 3.11 python.exe.
goto :fail

:wrongpython
echo.
echo The speech environment needs Python 3.11 (TRANSCRIBE_BUILD_PYTHON or py -3.11).
goto :fail

:inuse
echo.
echo %VENV%\ is in use. Close Transcribe and anything else running from it
echo (tests, a Python console), then try again.
goto :fail

:fail
echo RUN FAILED
if not defined CI pause
exit /b 1
