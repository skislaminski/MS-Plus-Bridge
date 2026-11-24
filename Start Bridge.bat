@echo off
rem Double-click in Explorer: starts the bridge and opens the interface in the browser, with the
rem tabs of the workshop beside it (Installer, IR Loader, KeyWave, NAM: --workshop).
cd /d "%~dp0"
".venv\Scripts\python.exe" -c "import mido, rtmidi, yaml, construct" >nul 2>nul
if not errorlevel 1 goto run

echo Setting up the Python environment...
rem python-rtmidi (MIDI) comes ready-made for Windows only up to Python 3.12 (1.5.8); with a newer
rem Python pip would have to compile it, which needs Visual Studio's C++ compiler. So the newest
rem installed of 3.12 ... 3.9 makes the environment; a newer Python may stay installed beside it.
set "PYTHON="
for %%v in (3.12 3.11 3.10 3.9) do if not defined PYTHON (py -%%v -c "" >nul 2>nul && set "PYTHON=py -%%v")
if not defined PYTHON (where py >nul 2>nul && set "PYTHON=py -3")
if not defined PYTHON set "PYTHON=python"
rem an environment whose Python runs and is not newer than 3.12 keeps it: only what is missing is added;
rem otherwise (none yet, its Python uninstalled, or a newer one) it is made anew, without the old packages
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info[:2] > (3, 12))" >nul 2>nul
if errorlevel 1 (
  %PYTHON% -m venv --clear .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 goto failed

:run
".venv\Scripts\python.exe" bridge.py --open --workshop %*
echo.
echo The bridge has stopped.
pause
exit /b

:failed
echo.
echo Setup failed. Install Python 3.12 from python.org (a newer Python can stay installed) and
echo start again: the MIDI package python-rtmidi comes ready-made for Windows only up to 3.12.
pause
