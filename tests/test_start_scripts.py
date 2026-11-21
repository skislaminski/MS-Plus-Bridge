"""Start Bridge.command and Start Bridge.sh, run against a stand-in for Python.

python3 and the environment's python are small scripts here that log their arguments: that shows
what a start sets up and how it starts the bridge (with the workshop's tabs). Start Bridge.bat is
the same for Windows and is only seen there.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# the environment's python: its imports work once pip has run; bridge.py is only logged
PYTHON = 'echo "python $*" >> "$CALLS"; [ "$1" = -c ] && { [ -f "$READY" ]; exit; }; [ "$1" = -m ] && touch "$READY"; exit 0'
# python3 -m venv .venv: a new environment, with that python in it
PYTHON3 = 'echo "python3 $*" >> "$CALLS"; mkdir -p .venv/bin && cp "$VENV_PYTHON" .venv/bin/python'

CHECK = "python -c import mido, rtmidi, yaml, construct"
SETUP = "python -m pip install --quiet -r requirements.txt"
START = "python bridge.py --open --workshop"


@unittest.skipIf(os.name == "nt", "on Windows it is Start Bridge.bat")
class StartScripts(unittest.TestCase):
    def fresh(self) -> None:
        """A folder with the script, and the stand-ins."""
        self.folder = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        stubs, self.here = self.folder / "bin", self.folder / "bridge"
        stubs.mkdir()
        self.here.mkdir()
        for name, body in (("python3", PYTHON3), ("venv-python", PYTHON)):
            (stubs / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
            (stubs / name).chmod(0o755)
        self.calls, self.ready = self.folder / "calls.txt", self.folder / "ready"
        self.env = dict(os.environ, PATH=f"{stubs}:/usr/bin:/bin", CALLS=str(self.calls), READY=str(self.ready),
                        VENV_PYTHON=str(stubs / "venv-python"))

    def start(self, script: str, *args: str) -> list:
        shell = shutil.which("zsh" if script.endswith(".command") else "bash")
        if shell is None:
            self.skipTest(f"no shell here for {script}")
        shutil.copy(ROOT / script, self.here)
        subprocess.run([shell, str(self.here / script), *args], env=self.env, check=True,
                       capture_output=True, timeout=30)
        return self.calls.read_text(encoding="utf-8").splitlines()

    def environment(self) -> None:
        """One made earlier, without the installer's package."""
        (self.here / ".venv/bin").mkdir(parents=True)
        shutil.copy(self.env["VENV_PYTHON"], self.here / ".venv/bin/python")

    def test_the_first_start_sets_everything_up_and_starts_the_bridge_with_its_tabs(self):
        for script in ("Start Bridge.command", "Start Bridge.sh"):
            with self.subTest(script):
                self.fresh()
                self.assertEqual(self.start(script), ["python3 -m venv .venv", SETUP, START])

    def test_an_environment_that_is_there_only_gets_what_is_missing(self):
        for script in ("Start Bridge.command", "Start Bridge.sh"):
            with self.subTest(script):
                self.fresh()
                self.environment()
                self.assertEqual(self.start(script), [CHECK, SETUP, START])    # its Python stays

    def test_with_everything_there_it_starts_at_once_with_what_it_was_given(self):
        for script in ("Start Bridge.command", "Start Bridge.sh"):
            with self.subTest(script):
                self.fresh()
                self.environment()
                self.ready.touch()
                self.assertEqual(self.start(script, "--listen"), [CHECK, START + " --listen"])


if __name__ == "__main__":
    unittest.main()
