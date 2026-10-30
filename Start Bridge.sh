#!/bin/bash
# Linux: run in a terminal (or make it executable and double-click it in the file manager, "Run in
# terminal"): sets up the Python environment on the first start, then starts the bridge and opens
# the interface in the browser.
cd "$(dirname "$0")" || exit 1
if ! .venv/bin/python -c "import mido, rtmidi, yaml" 2>/dev/null; then
  echo "Setting up the Python environment…"
  if ! python3 -m venv .venv; then
    echo "python3 -m venv failed: on Debian and Ubuntu install it first (sudo apt install python3-venv)."
    exit 1
  fi
  if ! .venv/bin/python -m pip install --quiet -r requirements.txt; then
    echo "Installing the packages failed: python-rtmidi may need a compiler and the ALSA headers"
    echo "(sudo apt install build-essential libasound2-dev), then start this again."
    exit 1
  fi
fi
exec .venv/bin/python bridge.py --open "$@"
