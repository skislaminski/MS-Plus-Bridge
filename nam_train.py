"""Training NAM models into Compact ones, for an N2Z Bank (the NAM tab of `bridge.py --workshop`).

It uses nam2zoom.app's own trainer as it is installed: its Python, its PyTorch environment (set up
once in nam2zoom.app), its training DI and its cache. It runs `python -m nam2zoom adapt` as
nam2zoom.app 1.2.3 does (macos-port-source: AppState.swift, Backend.swift, Subprocess.swift), with
two differences:
- It calls the app's bundled Python with the environment's packages on PYTHONPATH, instead of the
  environment's own interpreter link. That link breaks when the app is moved, and needs no repair
  this way.
- It reads the progress from stdout, where Lightning writes it.
A model the quality check accepts goes where nam2zoom.app puts it (Converted_NAM/Compact). Nothing
of nam2zoom is copied or changed. macOS only: nam2zoom.app is a Mac app."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

BUNDLE_ID = "io.github.nam2zoom.desktop"
EPOCHS = 100                      # nam2zoom.app's own number (about 7.5 min per model on an M1)
STOP_SECONDS = 3.0                # SIGTERM to the whole group, SIGKILL after this (as nam2zoom.app)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
EPOCH = re.compile(r"Epoch (\d+):\s*(\d+)%")
ESR = re.compile(r"Held-out ESR ([0-9.eE+-]+), correlation ([0-9.eE+-]+)")


def data_root() -> Path:
    """nam2zoom's own folder for its environment, cache and converted models (platforms.data_root)."""
    own = os.environ.get("NAM2ZOOM_DATA_DIR")
    return Path(own).expanduser() if own else Path.home() / "Library/Application Support/nam2zoom"


def find_app() -> Optional[Path]:
    """nam2zoom.app: in Applications, or wherever Spotlight knows it by its bundle id."""
    places = [Path("/Applications/nam2zoom.app"), Path.home() / "Applications/nam2zoom.app"]
    try:
        found = subprocess.run(["mdfind", f"kMDItemCFBundleIdentifier == '{BUNDLE_ID}'"],
                               capture_output=True, text=True, timeout=10).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        found = []
    for place in places + [Path(line) for line in found if line.endswith(".app")]:
        if (place / "Contents/Resources/app/tools/nam2zoom/adapt.py").is_file():
            return place
    return None


class Job:
    def __init__(self, number: int, name: str, source: Path, credit: Optional[dict] = None) -> None:
        self.id, self.name, self.source = number, name, source
        self.credit = credit                   # creator and license of a tone from TONE3000 (tone3000.py)
        self.state = "queued"      # queued, running, review, done, failed, cancelled
        self.stage, self.epoch, self.percent = "Waiting", None, None
        self.esr = self.correlation = None
        self.quality: Optional[str] = None     # "accurate" or "review-required" (QUALITY_RESULT)
        self.error: Optional[str] = None
        self.model: Optional[Path] = None      # what adapt made (in its cache)
        self.preview: Optional[Path] = None    # original.wav and converted.wav, the last 9 s
        self.result: Optional[Path] = None     # the copy in Converted_NAM/Compact
        self.process: Optional[subprocess.Popen] = None
        self.renders = 0

    def view(self) -> dict:
        return {"id": self.id, "name": self.name, "state": self.state, "stage": self.stage, "epoch": self.epoch,
                "epochs": EPOCHS, "percent": self.percent, "esr": self.esr, "correlation": self.correlation,
                "error": self.error, "preview": self.preview is not None,
                "result": self.result.name if self.result else None}

    def read(self, line: str) -> None:
        """One line of adapt's stdout (adapt.py, nam.cli and Lightning's progress bar)."""
        epoch = EPOCH.search(line)
        if epoch:
            self.stage, self.epoch, self.percent = "Training", int(epoch.group(1)) + 1, int(epoch.group(2))
        elif line.startswith("Running: core_render"):
            self.renders += 1
            self.stage = "Playing the original through the model" if self.renders == 1 else "Checking the result"
        elif line.startswith("Starting training"):
            self.stage = "Training"
        elif line.startswith(("Reusing verified completed adaptation", "Cached adaptation")):
            self.stage = "Trained before: taken from the cache"
        elif ESR.search(line):
            self.esr, self.correlation = (float(value) for value in ESR.search(line).groups())
        elif line.startswith("QUALITY_RESULT="):
            quality = json.loads(line.split("=", 1)[1])
            self.esr, self.correlation = quality.get("esr", self.esr), quality.get("correlation", self.correlation)
            self.quality = quality.get("status")
        elif line.startswith("PREVIEW_DIR="):
            self.preview = Path(line.split("=", 1)[1])
        elif line.startswith("READY_MODEL="):
            self.model = Path(line.split("=", 1)[1])


class Trainer:
    """One job after another (one GPU). problem: why it cannot train here, or None."""

    def __init__(self, app: Optional[Path] = None, data: Optional[Path] = None) -> None:
        self.data = Path(data) if data else data_root()
        self.app = Path(app) if app else (find_app() if sys.platform == "darwin" else None)
        self.root = self.app / "Contents/Resources/app" if self.app else None
        arch = "python-arm64" if platform.machine() == "arm64" else "python-x86_64"
        self.python = self.root / "runtime" / arch / "bin/python3" if self.root else None
        # nam2zoom.app's environment for this kind of Mac (Backend.swift): the packages are not interchangeable
        self.venv = self.data / ("nam-train-venv" if platform.machine() == "arm64" else "nam-train-venv-x86_64")
        self.packages = next((self.venv / "lib").glob("python3.*/site-packages"), None)
        self.problem = self._problem()
        self.jobs: list = []
        self.lock = threading.Lock()
        self.work = Path(tempfile.mkdtemp(prefix="nam-train-"))
        self.wake = threading.Event()
        self.stopping = False
        threading.Thread(target=self._run, daemon=True).start()

    def _problem(self) -> Optional[str]:
        if self.root is None:
            return "Training needs nam2zoom.app (a Mac app); it was not found here."
        if not self.python.is_file():
            return f"nam2zoom.app has no Python for this Mac ({self.python.parent.parent.name})."
        if self.packages is None or not (self.venv / "training.ready").is_file():
            return "Set up training once in nam2zoom.app: it installs PyTorch there (about 1 GB)."
        return None

    def command(self, model: Path) -> list:
        return [str(self.python), "-m", "nam2zoom", "adapt", str(model),
                "--training-di", str(self.root / "training/TRAINING_DI.wav"), "--cache", str(self.data / "adapt-cache"),
                "--epochs", str(EPOCHS), "--profile", "compact", "--review-quality"]

    def environment(self) -> dict:
        """As nam2zoom.app builds it (Backend.swift), with the environment's packages on the path."""
        env = {name: os.environ[name] for name in ("HOME", "USER", "LOGNAME", "TMPDIR") if name in os.environ}
        env.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8", PYTHONUTF8="1",
                   PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1",
                   PYTHONPATH=os.pathsep.join((str(self.root / "tools"), str(self.packages))),
                   PYTHONPYCACHEPREFIX=str(self.data / "pycache"), NAM2ZOOM_DATA_DIR=str(self.data),
                   MPLBACKEND="Agg", MPLCONFIGDIR=str(self.data / "nam-mpl-cache"))
        # without it macOS trains on the CPU, several times slower (adapt.py); the GPU from macOS 14 on Apple silicon
        if platform.machine() == "arm64" and int((platform.mac_ver()[0] or "0").split(".")[0]) >= 14:
            env["NAM2ZOOM_TRAINING_DEVICE"] = "mps"
        return env

    def add(self, name: str, data: bytes, credit: Optional[dict] = None) -> Job:
        with self.lock:
            # a name file systems take (TONE3000's can be longer than 255 characters)
            job = Job(len(self.jobs) + 1, Path(name).stem[:100] + ".nam", Path(), credit)
            folder = self.work / str(job.id)
            folder.mkdir(exist_ok=True)               # left behind by an add whose write failed
            job.source = folder / job.name
            job.source.write_bytes(data)
            self.jobs.append(job)
        self.wake.set()
        return job

    def find(self, number) -> Job:
        with self.lock:
            job = next((job for job in self.jobs if job.id == number), None)
        if job is None:
            raise ValueError("No such training.")
        return job

    def cancel(self, number) -> None:
        job = self.find(number)
        if job.state == "queued":
            job.state, job.stage = "cancelled", "Cancelled"
        elif job.state == "running":
            job.state = "cancelled"           # what the run then reports is not taken
            self._stop(job)

    def review(self, number, use: bool) -> None:
        """A model the quality check did not pass outright: listened to, then kept or dropped."""
        job = self.find(number)
        if job.state != "review":
            raise ValueError("Nothing to decide for this training.")
        if use:
            self._keep(job)
        else:
            job.state, job.stage = "cancelled", "Not kept"

    def model(self, number) -> tuple:
        """(file name, bytes) of a trained model that was kept."""
        job = self.find(number)
        if job.result is None:
            raise ValueError("This training has no model (yet).")
        return job.result.name, job.result.read_bytes()

    def preview(self, number, which: str) -> bytes:
        """original.wav or converted.wav: the last 9 seconds of the training DI through the original
        and through the trained model, for the ear to decide."""
        job = self.find(number)
        if which not in ("original", "converted") or job.preview is None:
            raise ValueError("No such preview.")
        return (job.preview / f"{which}.wav").read_bytes()

    def state(self) -> dict:
        with self.lock:
            return {"available": self.problem is None, "problem": self.problem,
                    "jobs": [job.view() for job in self.jobs]}

    def stop(self) -> None:
        self.stopping = True
        with self.lock:
            running = [job for job in self.jobs if job.state == "running"]
        for job in running:
            job.state = "cancelled"
            self._stop(job)
        self.wake.set()
        shutil.rmtree(self.work, ignore_errors=True)

    @staticmethod
    def _stop(job: Job) -> None:
        """The whole process group: adapt, the trainer it starts and core_render. Stopping the trainer
        alone would make adapt score and publish a half-trained model."""
        process = job.process
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(STOP_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _keep(self, job: Job) -> None:
        """Into Converted_NAM/Compact under the model's own name; the same file again is not copied
        twice, another one of the same name becomes "<name> (2).nam" (Backend.swift)."""
        folder = self.data / "Converted_NAM" / "Compact"
        folder.mkdir(parents=True, exist_ok=True)
        data, stem = job.model.read_bytes(), Path(job.name).stem
        number = 1
        while True:
            target = folder / (f"{stem}.nam" if number == 1 else f"{stem} ({number}).nam")
            if not target.exists() or target.read_bytes() == data:
                break
            number += 1
        if not target.exists():
            target.write_bytes(data)
        if job.credit:                       # TONE3000's terms: creator, tone and license stay with what is made of it
            target.with_suffix(".tone3000.json").write_text(json.dumps(job.credit, indent=1), encoding="utf-8")
        job.result, job.state, job.stage = target, "done", "Ready for the bank"

    def _next(self) -> Optional[Job]:
        with self.lock:
            return next((job for job in self.jobs if job.state == "queued"), None)

    def _run(self) -> None:
        while not self.stopping:
            job = self._next()
            if job is None:
                self.wake.wait(1.0)
                self.wake.clear()
                continue
            if self.problem:
                job.state, job.error = "failed", self.problem
                continue
            try:
                self._train(job)
            except Exception as error:                # e.g. Converted_NAM not writable: this job, not the queue
                job.state, job.error = "failed", f"Training failed: {error}"

    @staticmethod
    def _nam2zoom_running() -> bool:
        """Both write the same files in the cache (adapt.py: <key>.json.tmp)."""
        try:
            names = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return "nam2zoom.app/Contents/MacOS/" in names

    def _train(self, job: Job) -> None:
        if self._nam2zoom_running():
            job.state, job.error = "failed", "nam2zoom.app is open: close it first, both train into the same cache."
            return
        with self.lock:
            if job.state != "queued" or self.stopping:   # cancelled while nam2zoom.app was looked for
                return
            job.state, job.stage, job.quality = "running", "Starting", None
        try:
            job.process = subprocess.Popen(self.command(job.source), cwd=self.root, env=self.environment(),
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           start_new_session=True)
        except OSError as error:
            job.state, job.error = "failed", f"nam2zoom's trainer did not start: {error}"
            return
        if job.state == "cancelled" or self.stopping:      # cancelled while it started: _stop saw no process
            self._stop(job)
        errors: list = []
        reader = threading.Thread(target=lambda: errors.extend(job.process.stderr.read().decode("utf-8", "replace")
                                                               .splitlines()), daemon=True)
        reader.start()
        pending = b""
        for chunk in iter(lambda: job.process.stdout.read1(4096), b""):
            pending += chunk
            *lines, pending = re.split(rb"[\r\n]", pending)
            for line in lines:
                try:
                    job.read(ANSI.sub("", line.decode("utf-8", "replace")).strip())
                except (ValueError, TypeError):
                    pass                                     # a line that only looks like one of the above
            # the progress bar is drawn again from the start of its line (\r), so the newest one is still open
            tail = ANSI.sub("", pending.decode("utf-8", "replace")).strip()
            if EPOCH.search(tail):
                job.read(tail)
        code = job.process.wait()
        reader.join(5)                                     # its last lines hold adapt's reason
        if job.state == "cancelled":
            job.stage = "Cancelled"
        elif code != 0 or job.model is None or not job.model.is_file():
            failed = next((line for line in reversed(errors) if line.startswith("nam2zoom: adapt failed:")), None)
            job.state = "failed"
            job.error = (failed.split(":", 2)[2].strip() if failed
                         else (errors[-1] if errors else f"nam2zoom's trainer ended with code {code}."))
        elif job.quality == "accurate":
            self._keep(job)
        else:                                                # review-required: the ear decides
            job.state, job.stage = "review", "Listen and decide: the check did not pass it outright"
