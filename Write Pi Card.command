#!/bin/zsh
# Double-click in Finder with the Raspberry Pi's SD card in this computer: writes the bridge and
# its settings onto the card and ejects it (export.py, see README "On a Raspberry Pi").
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "Start the bridge once with “Start Bridge.command” first: it sets up what this needs."
else
  .venv/bin/python export.py
fi
echo
read -r "?Press Return to close this window."
