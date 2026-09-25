@echo off
rem Developer build. Produces the thin launcher Transcribe.exe. The
rem frozen exe is self-bootstrapping: on first run it builds its own
rem environment under %LOCALAPPDATA%\Transcribe (see backend/firstrun.py),
rem so the exe does NOT need to sit next to venv\ -- it can be shipped and
rem run from anywhere. This venv\ is only the developer build environment.
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Missing developer venv: %~dp0venv
  echo.
  echo Build setup:
  echo   py -3.11 -m venv venv
  echo   venv\Scripts\python.exe -m pip install -U pip
  echo   venv\Scripts\python.exe -m pip install pyinstaller==6.20.0 pywebview==6.2.1
  echo   build.cmd
  echo.
  echo If py is not found, install Python 3.11 with the Windows py launcher.
  pause
  exit /b 1
)
"%PY%" -c "import PyInstaller, webview" >nul 2>nul
if errorlevel 1 (
  echo Developer venv exists, but build dependencies are missing.
  echo.
  echo Run:
  echo   venv\Scripts\python.exe -m pip install pyinstaller==6.20.0 pywebview==6.2.1
  pause
  exit /b 1
)
"%PY%" -m PyInstaller Transcribe.spec --noconfirm --clean
if errorlevel 1 ( echo BUILD FAILED & pause & exit /b 1 )
copy /Y "%~dp0dist\Transcribe.exe" "%~dp0Transcribe.exe" >nul
echo.
echo Built: %~dp0Transcribe.exe
echo Ship that single file. First run installs into %%LOCALAPPDATA%%\Transcribe.
