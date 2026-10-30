"""Tests against simulated devices. No real MIDI port is opened and nothing reaches a real pedal.

Run them from the project folder:  .venv/bin/python -m unittest
"""
import os

# The synth effects as the bridge itself defines them (zoom_sysex.py), not as the table of the
# pedal's effect files says they are installed right now: the tests do not depend on the pedal.
os.environ["MS_PLUS_BRIDGE_SYNTHS"] = "builtin"
