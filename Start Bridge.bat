@echo off
rem Double-click in Explorer: starts the bridge and opens the interface in the browser, with the
rem tabs of the workshop beside it (Installer, IR Loader, KeyWave, NAM: --workshop).
cd /d "%~dp0"
".venv\Scripts\python.exe" -c "import mido, rtmidi, yaml, construct" >nul 2>nul
if errorlevel 1 (
  echo Setting up the Python environment...
  rem an environment whose Python runs keeps it: only what is missing is added
  ".venv\Scripts\python.exe" -c "" >nul 2>nul
  if errorlevel 1 (
    rem none yet, or its Python was uninstalled: made anew (--clear: without the old packages)
    where py >nul 2>nul
    if errorlevel 1 (python -m venv --clear .venv) else (py -3 -m venv --clear .venv)
    if errorlevel 1 goto failed
  )
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" bridge.py --open --workshop %*
echo.
echo The bridge has stopped.
pause
exit /b

:failed
echo.
echo Setup failed. Install Python 3.11 or newer from python.org and try again.
pause
