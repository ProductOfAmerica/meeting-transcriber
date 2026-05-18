@echo off
rem Developer build. Produces the thin launcher Transcribe.exe. The
rem frozen exe is self-bootstrapping: on first run it builds its own
rem environment under %LOCALAPPDATA%\Transcribe (see backend/firstrun.py),
rem so the exe does NOT need to sit next to venv\ -- it can be shipped and
rem run from anywhere. This venv\ is only the developer build environment.
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" -m PyInstaller Transcribe.spec --noconfirm --clean
if errorlevel 1 ( echo BUILD FAILED & pause & exit /b 1 )
copy /Y "%~dp0dist\Transcribe.exe" "%~dp0Transcribe.exe" >nul
echo.
echo Built: %~dp0Transcribe.exe
echo Ship that single file. First run installs into %%LOCALAPPDATA%%\Transcribe.
