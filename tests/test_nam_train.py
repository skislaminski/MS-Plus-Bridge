"""Training NAM models into Compact ones (nam_train.py) against a stand-in for nam2zoom.app.

The stand-in has the same layout, takes the same command and prints what nam2zoom 1.2.3's adapt
prints, in the same order and on the same streams. It is quick, and it starts a child process the
way adapt starts its trainer, so that a cancel can be seen to stop the whole group."""
import json
import os
import platform
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

import bridge
import nam_train
import workshop
from tests.nam_models import compact_nam
from tests.sim import SimulatedCase

FAKE_ADAPT = r'''
import json, os, subprocess, sys, time
args = sys.argv[1:]
source, cache = args[1], args[args.index("--cache") + 1]
spec = json.load(open(source))["fake"]
work = os.path.join(cache, "work-" + os.path.basename(source)[:8])
os.makedirs(work, exist_ok=True)
print("Preparing teacher pair in " + work, flush=True)
print("Running: core_render --slim 1.0 teacher", flush=True)
print("Starting training; CPU thread limits: OMP=2, MKL=2.", flush=True)
for epoch in range(3):            # Lightning redraws its bar with \r and moves the cursor up
    sys.stdout.write("\rEpoch %d:  50%%|#####     | 30/60 [00:02<00:02, 14.43it/s]\x1b[A" % epoch)
    sys.stdout.flush()
if spec["mode"] == "slow":        # the trainer: a child in the same group, still running
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    open(spec["pidfile"], "w").write(str(child.pid))
    time.sleep(120)
if spec["mode"] == "fail":
    print("nam2zoom: adapt failed: teacher output clips at 1.0", file=sys.stderr)
    sys.exit(2)
print("\nRunning: core_render student", flush=True)
print("Held-out ESR 0.01896, correlation 0.9906", flush=True)
status = "accurate" if spec["mode"] == "accurate" else "review-required"
print("QUALITY_RESULT=" + json.dumps({"status": status, "esr": 0.019 if status == "accurate" else 0.071,
                                      "max_esr": 0.05, "correlation": 0.99}), flush=True)
preview = os.path.join(work, "preview")
os.makedirs(preview, exist_ok=True)
for name in ("original", "converted"):
    open(os.path.join(preview, name + ".wav"), "w").write(name)
print("PREVIEW_DIR=" + preview, flush=True)
model = os.path.join(work, "model.nam")
open(model, "w").write(spec["model"])
print("Adapted model ready: " + model, flush=True)
print("READY_MODEL=" + model, flush=True)
'''


def source(mode: str, seed: int = 0, **extra) -> bytes:
    """A .nam to train (a WaveNet at 48 kHz, as nam2zoom sees it), telling the stand-in what to do and
    which Compact model to report."""
    return json.dumps({"architecture": "WaveNet", "sample_rate": 48000, "config": {}, "weights": [],
                       "fake": dict(mode=mode, model=compact_nam(seed).decode(), **extra)}).encode()


def fake_app(folder: Path) -> tuple:
    """(nam2zoom.app, its data folder) as nam_train expects them, with the stand-in as its adapt."""
    app = folder / "nam2zoom.app"
    root = app / "Contents/Resources/app"
    (root / "tools/nam2zoom").mkdir(parents=True)
    (root / "tools/nam2zoom/__init__.py").write_text("")
    (root / "tools/nam2zoom/adapt.py").write_text("")
    (root / "tools/nam2zoom/__main__.py").write_text(FAKE_ADAPT)
    (root / "training").mkdir()
    (root / "training/TRAINING_DI.wav").write_bytes(b"")
    for arch in ("python-arm64", "python-x86_64"):
        (root / "runtime" / arch / "bin").mkdir(parents=True)
        (root / "runtime" / arch / "bin/python3").symlink_to(os.path.realpath(os.sys.executable))
    data = folder / "data"
    (data / "nam-train-venv/lib/python3.12/site-packages").mkdir(parents=True)
    (data / "nam-train-venv/training.ready").write_text("ready")
    return app, data


class Training(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        app, self.data = fake_app(self.folder)
        patcher = mock.patch.object(nam_train.Trainer, "_nam2zoom_running", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.trainer = nam_train.Trainer(app, self.data)
        self.addCleanup(self.trainer.stop)

    def wait(self, job, states=("done", "failed", "review", "cancelled"), seconds=20.0):
        deadline = time.monotonic() + seconds
        while job.state not in states:
            self.assertLess(time.monotonic(), deadline, f"still {job.state}")
            time.sleep(0.02)

    def test_a_model_that_passes_goes_where_nam2zoom_puts_it(self):
        self.assertIsNone(self.trainer.problem)
        job = self.trainer.add("Ampeg SVT (A2).nam", source("accurate"))
        self.wait(job)
        view = job.view()
        self.assertEqual((view["state"], view["epoch"], view["percent"], view["esr"]), ("done", 3, 50, 0.019))
        self.assertEqual(view["result"], "Ampeg SVT (A2).nam")
        kept = self.data / "Converted_NAM/Compact/Ampeg SVT (A2).nam"
        self.assertEqual(kept.read_bytes(), compact_nam(0))
        self.assertEqual(self.trainer.model(job.id), ("Ampeg SVT (A2).nam", compact_nam(0)))
        self.assertEqual(self.trainer.preview(job.id, "converted"), b"converted")

    def test_the_same_name_with_another_model_is_kept_beside_it(self):
        for seed in (0, 0, 1):
            self.wait(self.trainer.add("Amp.nam", source("accurate", seed)))
        self.assertEqual(sorted(path.name for path in (self.data / "Converted_NAM/Compact").iterdir()),
                         ["Amp (2).nam", "Amp.nam"])

    def test_one_the_check_does_not_pass_waits_for_the_ear(self):
        kept, dropped = self.trainer.add("Keep.nam", source("review")), self.trainer.add("Drop.nam", source("review", 1))
        for job in (kept, dropped):
            self.wait(job)
            self.assertEqual((job.state, job.esr), ("review", 0.071))
        self.trainer.review(kept.id, True)
        self.trainer.review(dropped.id, False)
        self.assertEqual((kept.state, dropped.state), ("done", "cancelled"))
        self.assertEqual([path.name for path in (self.data / "Converted_NAM/Compact").iterdir()], ["Keep.nam"])
        with self.assertRaises(ValueError):
            self.trainer.review(kept.id, True)                     # decided already

    def test_its_reason_when_it_fails(self):
        job = self.trainer.add("Loud.nam", source("fail"))
        self.wait(job)
        self.assertEqual((job.state, job.error), ("failed", "teacher output clips at 1.0"))

    def test_a_cancel_stops_the_whole_group(self):
        pidfile = self.folder / "trainer.pid"
        job = self.trainer.add("Slow.nam", source("slow", pidfile=str(pidfile)))
        deadline = time.monotonic() + 20
        while not pidfile.exists() or not pidfile.read_text():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.02)
        self.assertEqual(job.epoch, 3)                             # read while it runs
        trainer_pid = int(pidfile.read_text())
        self.trainer.cancel(job.id)
        self.wait(job)
        self.assertEqual(job.state, "cancelled")
        deadline = time.monotonic() + 5
        while True:                                                 # the trainer went with adapt
            try:
                os.kill(trainer_pid, 0)
            except ProcessLookupError:
                break
            self.assertLess(time.monotonic(), deadline, "the trainer kept running")
            time.sleep(0.05)
        self.assertEqual(list((self.data / "Converted_NAM").glob("*/*")) if (self.data / "Converted_NAM").exists() else [], [])

    def test_the_command_and_environment_are_nam2zoom_apps(self):
        command = self.trainer.command(Path("/x/Amp.nam"))
        self.assertEqual(command[1:5], ["-m", "nam2zoom", "adapt", "/x/Amp.nam"])
        for flag, value in (("--epochs", "100"), ("--profile", "compact"), ("--cache", str(self.data / "adapt-cache"))):
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertIn("--review-quality", command)
        env = self.trainer.environment()
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep),
                         [str(self.trainer.root / "tools"), str(self.data / "nam-train-venv/lib/python3.12/site-packages")])
        self.assertEqual(env["NAM2ZOOM_DATA_DIR"], str(self.data))
        if platform.machine() == "arm64" and int(platform.mac_ver()[0].split(".")[0] or 0) >= 14:
            self.assertEqual(env["NAM2ZOOM_TRAINING_DEVICE"], "mps")          # else macOS trains on the CPU

    def test_why_it_cannot_train(self):
        shutil.rmtree(self.data / "nam-train-venv")
        self.assertIn("Set up training once in nam2zoom.app", nam_train.Trainer(self.folder / "nam2zoom.app", self.data).problem)
        nowhere = nam_train.Trainer(self.folder / "missing.app", self.data)
        self.addCleanup(nowhere.stop)
        job = nowhere.add("Amp.nam", source("accurate"))
        self.wait(job)
        self.assertEqual(job.state, "failed")
        self.assertIn("nam2zoom.app", job.error)

    def test_on_an_intel_mac_in_the_environment_nam2zoom_app_made_for_it(self):
        (self.data / "nam-train-venv").rename(self.data / "nam-train-venv-x86_64")
        with mock.patch("platform.machine", return_value="x86_64"):
            intel = nam_train.Trainer(self.folder / "nam2zoom.app", self.data)
        self.addCleanup(intel.stop)
        self.assertIsNone(intel.problem)
        self.assertEqual((intel.python.parent.parent.name, intel.packages.name), ("python-x86_64", "site-packages"))

    def test_a_model_not_written_or_not_kept_stops_only_itself(self):
        with mock.patch.object(Path, "write_bytes", side_effect=OSError(28, "No space left on device")), \
                self.assertRaises(OSError):
            self.trainer.add("Amp.nam", source("accurate"))
        (self.data / "Converted_NAM").write_text("")       # a file where its folder belongs
        lost = self.trainer.add("x" * 300 + ".nam", source("accurate"))    # a name longer than file systems take
        self.wait(lost)
        self.assertEqual((lost.state, lost.name), ("failed", "x" * 100 + ".nam"))
        self.assertIn("Training failed", lost.error)
        (self.data / "Converted_NAM").unlink()
        job = self.trainer.add("Amp.nam", source("accurate", 1))
        self.wait(job)
        self.assertEqual(job.state, "done")

    def test_a_cancel_while_it_starts_is_kept(self):
        looking, go = threading.Event(), threading.Event()
        with mock.patch.object(nam_train.Trainer, "_nam2zoom_running", side_effect=lambda: looking.set() or go.wait(5) and False):
            job = self.trainer.add("Amp.nam", source("accurate"))
            self.assertTrue(looking.wait(5))
            self.trainer.cancel(job.id)
            go.set()
            after = self.trainer.add("Next.nam", source("accurate", 1))
            self.wait(after)
        self.assertEqual((job.state, after.state), ("cancelled", "done"))
        self.assertEqual([path.name for path in (self.data / "Converted_NAM/Compact").iterdir()], ["Next.nam"])

    def test_not_while_nam2zoom_app_trains(self):
        with mock.patch.object(nam_train.Trainer, "_nam2zoom_running", return_value=True):
            job = self.trainer.add("Amp.nam", source("accurate"))
            self.wait(job)
        self.assertEqual((job.state, job.error), ("failed", "nam2zoom.app is open: close it first, both train into the same cache."))


class ThroughTheWorkshop(SimulatedCase):
    """What the NAM page asks: nam/train, its state, the model, the previews, review and cancel."""

    def setUp(self) -> None:
        super().setUp()
        app, self.data = fake_app(self.folder)
        patcher = mock.patch.object(nam_train.Trainer, "_nam2zoom_running", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = workshop.start(str(bridge.INSTALLER_DATA))
        self.addCleanup(self.app.stop)
        self.app.trainer = nam_train.Trainer(app, self.data)

    def call(self, path: str, body=None, headers=None) -> tuple:
        request = urllib.request.Request(self.app.url + path, data=body, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=10) as reply:
                raw = reply.read()
                return reply.status, dict(reply.headers), json.loads(raw) if reply.headers["Content-Type"].startswith("application/json") else raw
        except urllib.error.HTTPError as error:
            with error:
                return error.code, dict(error.headers), json.loads(error.read())

    def finished(self, number: int) -> dict:
        deadline = time.monotonic() + 20
        while True:
            job = next(job for job in self.call("nam/train/state")[2]["jobs"] if job["id"] == number)
            if job["state"] not in ("queued", "running"):
                return job
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)

    def test_a_capture_from_tone3000_is_trained_and_offered_to_the_bank(self):
        credit = {"tone": "Ampeg SVT", "creator": "someone", "license": "t3k", "source": "TONE3000"}
        reply = self.call("nam/train?name=Amp%20A2.nam", source("accurate"),
                          {"X-Credit": urllib.parse.quote(json.dumps(credit))})[2]
        self.assertEqual(reply, {"ok": True, "id": 1})
        self.assertEqual((self.finished(1)["state"], self.finished(1)["result"]), ("done", "Amp A2.nam"))
        status, headers, model = self.call("nam/train/model?id=1")
        self.assertEqual((model, urllib.parse.unquote(headers["X-File-Name"])), (compact_nam(0), "Amp A2.nam"))
        self.assertEqual(json.loads((self.data / "Converted_NAM/Compact/Amp A2.tone3000.json").read_text()), credit)
        self.assertEqual(self.call("nam/train/preview?id=1&which=original")[2], b"original")

    def test_what_is_not_trained(self):
        self.assertEqual(self.call("nam/train?name=C.nam", compact_nam(0))[2],
                         {"ok": False, "error": "This one is Compact already: it goes into the bank as it is."})
        self.assertIn("nam2zoom cannot train this one", self.call("nam/train?name=X.nam", b"not json")[2]["error"])

    def test_deciding_and_cancelling_from_the_page(self):
        self.call("nam/train?name=Maybe.nam", source("review"))
        self.assertEqual(self.finished(1)["state"], "review")
        self.assertEqual(self.call("nam/train/review", json.dumps({"id": 1, "use": True}).encode())[2], {"ok": True})
        self.assertEqual(self.finished(1)["state"], "done")
        pidfile = self.folder / "trainer.pid"
        self.call("nam/train?name=Slow.nam", source("slow", pidfile=str(pidfile)))
        deadline = time.monotonic() + 20
        while not pidfile.exists() or not pidfile.read_text():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.02)
        self.assertEqual(self.call("nam/train/cancel", json.dumps({"id": 2}).encode())[2], {"ok": True})
        self.assertEqual(self.finished(2)["state"], "cancelled")
        self.assertEqual(self.call("nam/train/review", json.dumps({"id": 2, "use": True}).encode())[:3:2],
                         (400, {"ok": False, "error": "Nothing to decide for this training."}))


@unittest.skipUnless(nam_train.find_app(), "nam2zoom.app is not on this computer")
class TheRealApp(unittest.TestCase):
    def test_it_is_found_and_ready_to_train(self):
        trainer = nam_train.Trainer()
        self.addCleanup(trainer.stop)
        self.assertIsNone(trainer.problem, trainer.problem)


if __name__ == "__main__":
    unittest.main()
