@echo off
rem Builds Transcribe.exe, the thin launcher (see Transcribe.spec). One command:
rem the build tools live in .venv-build\, created from requirements-build.lock on
rem first use and again whenever the lock changes. The exe installs its own
rem runtime under %LOCALAPPDATA%\Transcribe on first run, so it can be shipped
rem and run from anywhere.
setlocal
cd /d "%~dp0"
set "LOCK=requirements-build.lock"
set "VENV=.venv-build"
set "VPY=.venv-build\Scripts\python.exe"

rem Reuse the build env only when it was completed from this exact lock.
fc /b "%LOCK%" "%VENV%\%LOCK%" >nul 2>nul
if not errorlevel 1 goto :build

echo Creating the build environment in %VENV%\ ...
if exist "%VENV%" rmdir /s /q "%VENV%"
if defined TRANSCRIBE_BUILD_PYTHON (
  "%TRANSCRIBE_BUILD_PYTHON%" -m venv "%VENV%"
) else (
  py -3.11 -m venv "%VENV%"
)
if errorlevel 1 goto :nopython
"%VPY%" -c "import sys; sys.exit(sys.version_info[:2] != (3, 11))"
if errorlevel 1 goto :wrongpython

rem proxy-tools ships only as source. Building it without isolation needs the
rem lock's own setuptools (70.1 and later build wheels themselves), so that pin
rem goes in first; isolation would fetch unpinned build tools instead. No
rem pip cache, so that build really happens here, from the hash-checked source.
"%VPY%" -c "import re, sys; t = open(sys.argv[1]).read(); m = re.search(r'^setuptools==.*?(?=^\S|\Z)', t, re.M | re.S); m or sys.exit('no setuptools pin in ' + sys.argv[1]); open(sys.argv[2], 'w').write(m.group(0))" "%LOCK%" "%VENV%\setuptools.req"
if errorlevel 1 goto :fail
"%VPY%" -m pip install --disable-pip-version-check --no-cache-dir --require-hashes --no-deps -r "%VENV%\setuptools.req"
if errorlevel 1 goto :fail
"%VPY%" -m pip install --disable-pip-version-check --no-cache-dir --require-hashes --no-deps --no-build-isolation -r "%LOCK%"
if errorlevel 1 goto :fail
"%VPY%" -m pip check
if errorlevel 1 goto :fail
copy /y "%LOCK%" "%VENV%\%LOCK%" >nul
if errorlevel 1 goto :fail

:build
"%VPY%" -m PyInstaller Transcribe.spec --noconfirm --clean
if errorlevel 1 goto :fail
copy /y "dist\Transcribe.exe" "Transcribe.exe" >nul
if errorlevel 1 goto :fail
echo.
echo Built: %~dp0Transcribe.exe
echo Ship that single file with dist\THIRD_PARTY_LICENSES.txt.
exit /b 0

:nopython
echo.
echo Python 3.11 was not found. Install it from python.org with the "py launcher"
echo option, or set TRANSCRIBE_BUILD_PYTHON to a Python 3.11 python.exe.
goto :fail

:wrongpython
echo.
echo The build environment needs Python 3.11 (TRANSCRIBE_BUILD_PYTHON or py -3.11).
goto :fail

:fail
echo BUILD FAILED
if not defined CI pause
exit /b 1
