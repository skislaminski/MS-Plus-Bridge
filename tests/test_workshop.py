"""The Zoom MS Installer inside the bridge (workshop.py, `bridge.py --workshop`): each of its jobs
has the pedal alone, an alarm keeps the bridge away after it, its address is only for this
computer, and without a bridge it runs on its own. Its jobs run against its own simulated pedal
(MockPedal from installer/zoom_ms_selftest.py), the bridge against the one of tests/sim.py."""
import base64
import json
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import bridge
import effect_pictures
import ui
import workshop
from tests.sim import SimulatedCase, World, mapping
from tests.test_change_effect import CHORUS, HOGI, STOCK
from tests.test_lease import EDIT_OFF, sent_between


def wait_for(condition, seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("waited in vain")
        time.sleep(0.01)


def themed(page: bytes) -> bytes:
    """A tab's page as it is served: tab_theme.css in a style of its own, last in its head."""
    return page.replace(b"</head>", workshop.FRAMED + b"<style>\n" + workshop.THEME.read_bytes() + b"</style>\n</head>", 1)


def zic(width: int, height: int, fill: int) -> bytes:
    """A .ZIC with one picture, every byte of it fill."""
    return b"ZBMP" + (4).to_bytes(4, "little") + struct.pack("<HH", width, height) + bytes([fill]) * (width * -(-height // 8))


class Jobs(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.install({"0/94": [mapping(0, 2)]})
        self.app = workshop.start(str(bridge.INSTALLER_DATA), workshop.gui.make_demo_pedal(""))
        self.addCleanup(self.app.stop)

    def run_job(self, name, func, world: World) -> dict:
        """Starts the job a second into the run; when it started and ended, and the state after."""
        seen = {}

        def client(connection):
            self.app.bridge = self.instance
            connection.at(1.0)
            seen["started"] = self.app.start_job(name, func)
            seen["from"] = world.now()
            wait_for(lambda: not self.app.job_running())
            seen["to"] = world.now()
            connection.at(seen["to"] + 0.8)
            seen["after"] = connection.call("/api/state")[1]

        self.run_bridge(world, client)
        return seen

    def test_a_job_has_the_pedal_alone_and_the_bridge_is_back_after_it(self):
        world = World(length=4.0)
        seen = self.run_job("connect", self.app.job_connect, world)
        self.assertTrue(seen["started"])
        self.assertEqual(self.app.result["code"], 0)
        self.assertIn(EDIT_OFF, sent_between(world, 0.9, seen["from"]))      # let go before the job began
        self.assertEqual(sent_between(world, seen["from"], seen["to"]), [])
        self.assertTrue(sent_between(world, seen["to"], world.length))
        self.assertTrue(seen["after"]["connected"])
        self.assertIn("Pedal released: the installer is working (connect)", self.output)

    def test_not_while_the_bridge_changes_something_on_the_pedal(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "change_effect")
        known = json.loads(bridge.MEASUREMENTS.read_text(encoding="utf-8"))
        known["effects"] = [f"{HOGI:08X}"]
        bridge.MEASUREMENTS.write_text(json.dumps(known), encoding="utf-8")
        seen = {}

        def client(connection):
            self.app.bridge = self.instance
            connection.at(1.0)
            connection.call("/api/effects", {"action": "change", "slot": 1, "effect": f"{HOGI:08X}"})
            seen["started"] = self.app.start_job("connect", self.app.job_connect)
            seen["state"] = connection.call("/api/state")[1]

        # the patch that would show the change never comes: the change stays on its way
        world = World([(0.9, lambda world: setattr(world, "patches", False))], chain=[STOCK, CHORUS], length=1.6)
        self.run_bridge(world, client)
        self.assertFalse(seen["started"])
        self.assertIn("Not started: The bridge is changing something on the pedal: try again in a moment.",
                      self.app.log_lines)
        self.assertTrue(seen["state"]["connected"])
        self.assertIsNone(self.instance.lent)

    def test_after_a_transfer_it_could_not_verify_the_bridge_stays_away(self):
        world = World(length=3.0)
        seen = self.run_job("install", lambda: 4, world)
        self.assertTrue(self.app.unresolved)
        self.assertTrue(os.path.exists(bridge.INSTALLER_DATA / "unresolved.json"))
        self.assertEqual(sent_between(world, seen["from"], world.length + 1), [])
        self.assertFalse(seen["after"]["connected"])
        self.assertIn("could not verify a transfer", seen["after"]["status"])

    def test_a_verified_write_leaves_no_alarm_behind(self):
        def install():                                  # as the engine: the marker on disk while it writes
            self.app.writing(None)
            time.sleep(0.4)                             # the watch sees it meanwhile
            return 0
        seen = self.run_job("install", install, World(length=3.0))
        self.assertEqual(self.app.result["code"], 0)
        self.assertTrue(seen["after"]["connected"])
        self.assertNotIn("could not verify", self.output)

    def test_a_job_started_before_there_was_a_bridge_has_the_pedal_alone_too(self):
        # during the setup in the terminal the installer's page is there already, the bridge not yet
        world = World(length=2.6)
        self.assertTrue(self.app.start_job("backup", lambda: time.sleep(1.2) or 0))
        with self.devices(world), mock.patch.object(bridge, "HOME", self.folder):
            bridge._run(SimpleNamespace(no_ui=True), self.app)
        self.assertEqual(self.app.result["code"], 0)
        self.assertEqual(sent_between(world, 0.0, 1.19), [])             # the job ends 1.2 s in, not before
        self.assertTrue(sent_between(world, 1.19, world.length))          # and the bridge is back after it

    def test_once_the_bridge_has_stopped_the_installer_runs_its_jobs_on_its_own(self):
        seen = {}

        def alone(app, why):
            seen["why"], seen["started"] = why, app.start_job("backup", lambda: 0)
        with self.devices(World()), mock.patch.object(bridge, "HOME", self.folder), \
                mock.patch.object(bridge.Bridge, "run", side_effect=SystemExit("Device ID 6E does not match config.yaml")), \
                mock.patch.object(bridge, "_installer_alone", alone), mock.patch.object(workshop, "LEND_SECONDS", 2):
            bridge._run(SimpleNamespace(no_ui=True), self.app)
        self.assertEqual(seen["why"], "The bridge stopped: Device ID 6E does not match config.yaml")
        self.assertTrue(seen["started"])                # not lent to a loop that is over
        self.assertIsNone(self.app.bridge)
        wait_for(lambda: not self.app.job_running())

    def test_its_own_lock_does_not_count_as_an_installer_open_on_its_own(self):
        lock = json.loads((bridge.INSTALLER_DATA / "running.json").read_text())
        self.assertEqual((lock["url"], lock["pid"]), (self.app.url, os.getpid()))
        self.assertIsNone(bridge.installer_holds(bridge.INSTALLER_DATA))
        self.app.stop()
        self.assertFalse((bridge.INSTALLER_DATA / "running.json").exists())

    def test_stopping_waits_for_a_running_job(self):
        before = signal.getsignal(signal.SIGINT)
        self.assertTrue(self.app.start_job("backup", lambda: time.sleep(0.4) or 0))
        with mock.patch("builtins.print"):
            self.app.stop()
        self.assertEqual(self.app.result["code"], 0)
        self.assertIs(signal.getsignal(signal.SIGINT), before)


class TheAddress(unittest.TestCase):
    def ask(self, **serve) -> tuple:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        ui.serve(SimpleNamespace(), {"port": port}, **serve)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/workshop", timeout=5) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_given_to_this_computer(self):
        pages = {"installer": "http://127.0.0.1:1/token/", "keywave": "http://127.0.0.1:2/other/"}
        self.assertEqual(self.ask(workshop=pages), (200, {"pages": pages}))

    def test_none_without_the_installer(self):
        self.assertEqual(self.ask()[0], 404)


class TheIRLoaderTool(SimulatedCase):
    """Its page under the installer's secret address; what its "Send to the installer" sends (here:
    the pair it was built with) lands in the installer's list as an effect that may be installed."""

    def setUp(self) -> None:
        super().setUp()
        self.app = workshop.start(str(bridge.INSTALLER_DATA))
        self.addCleanup(self.app.stop)

    def request(self, path: str, body=None) -> tuple:
        request = urllib.request.Request(self.app.url + path, data=body, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=10) as reply:
                return reply.status, reply.read(), reply.headers
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read(), error.headers

    def test_its_page_is_served_with_a_policy_that_lets_it_fit_in_a_worker(self):
        status, page, headers = self.request("ir-loader")
        self.assertEqual((status, page), (200, themed(workshop.TOOL.read_bytes())))
        self.assertIn("worker-src blob:", headers["Content-Security-Policy"])
        self.assertIn(b'id="send" hidden', page)                      # shown once the installer answers
        wrong = self.app.url.replace(self.app.url.split("/")[3], "not-the-token")
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(wrong + "ir-loader", timeout=10)
        refused.exception.close()
        self.assertEqual(refused.exception.code, 404)

    def test_what_it_sends_is_in_the_installers_list(self):
        page = workshop.TOOL.read_text(encoding="utf-8")
        for name, key in (("IRLOADER.ZD2", "BASE_ZD2"), ("IRLOADER.ZIC", "BASE_ZIC")):
            data = base64.b64decode(re.search(f'const {key} = "([^"]+)"', page).group(1))
            self.assertEqual(json.loads(self.request(f"api/upload?name={name}", data)[1]), {"ok": True})
        self.assertEqual(json.loads(self.request("api/queue/rescan", b"{}")[1]), {"ok": True})
        queue = json.loads(self.request("api/state")[1])["queue"]
        self.assertEqual([(item["file"], item["ok"]) for item in queue], [("IRLOADER.ZD2", True)])

    def test_not_while_the_installer_works_on_the_pedal(self):
        # the file in the list stays the one the job is about to read
        self.assertTrue(self.app.start_job("backup", lambda: time.sleep(0.6) or 0))
        self.assertEqual(json.loads(self.request("api/upload?name=IRLOADER.ZD2", b"new")[1]), {"ok": False, "busy": True})
        wait_for(lambda: not self.app.job_running())
        self.assertFalse((bridge.INSTALLER_DATA / "inbox" / "IRLOADER.ZD2").exists())


class KeyWaveTables(SimulatedCase):
    """KeyWave Tables beside the installer: its page from an address of its own, what it makes in
    its own folder as always, and its "Send to the installer" puts that into the installer's list."""

    def setUp(self) -> None:
        super().setUp()
        self.out = self.folder / "KeyWave Tables"
        self.app = workshop.start(str(bridge.INSTALLER_DATA), keywave_out=self.out)
        self.addCleanup(self.app.stop)

    def call(self, path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        with urllib.request.urlopen(urllib.request.Request(self.app.keywave_url + path, data=data), timeout=30) as reply:
            raw = reply.read()
            return json.loads(raw) if reply.headers.get("Content-Type", "").startswith("application/json") else raw

    def made(self) -> dict:
        loaded = self.call("api/example", {"name": "organ"})
        made = self.call("api/build", {"id": loaded["id"], "name": "Organ"})
        self.assertEqual(Path(made["file"]), self.out / "Organ" / "KEYWAVE.ZD2")
        return made

    def queue(self) -> list:
        with urllib.request.urlopen(self.app.url + "api/state", timeout=10) as state:
            return [(item["file"], item["ok"]) for item in json.loads(state.read())["queue"]]

    def test_its_page_knows_the_installer_is_there(self):
        page = self.call("")
        self.assertEqual(page, themed((workshop.KEYWAVE / "keywave_gui.html").read_bytes()))
        self.assertIn(b'<button class="link" id="send" hidden>', page)               # shown when the state says so
        self.assertTrue(self.call("api/state")["installer"])
        self.assertEqual(self.app.pages()["keywave"], self.app.keywave_url)

    def test_what_it_made_goes_into_the_installers_list(self):
        self.assertEqual(self.call("api/send", {"file": self.made()["file"]}), {"ok": True})
        self.assertEqual(self.queue(), [("KEYWAVE.ZD2", True)])

    def test_only_an_effect_it_made(self):
        elsewhere = self.folder / "elsewhere" / "KEYWAVE.ZD2"
        elsewhere.parent.mkdir()
        elsewhere.write_bytes((workshop.KEYWAVE / "base" / "KEYWAVE.ZD2").read_bytes())
        for file in (str(elsewhere), str(bridge.INSTALLER_DATA / "running.json"), "", "../KEYWAVE.ZD2"):
            self.assertEqual(self.call("api/send", {"file": file}), {"ok": False, "error": "Make the effect first."})
        self.assertEqual(self.queue(), [])

    def test_not_without_its_icon(self):
        made = Path(self.made()["file"])
        made.with_suffix(".ZIC").unlink()
        self.assertEqual(self.call("api/send", {"file": str(made)}), {"ok": False, "error": "Make the effect first."})
        self.assertEqual(self.queue(), [])

    def test_not_while_the_installer_works_on_the_pedal(self):
        made = self.made()
        self.assertTrue(self.app.start_job("backup", lambda: time.sleep(0.6) or 0))
        self.assertEqual(self.call("api/send", {"file": made["file"]}),
                         {"ok": False, "error": "The installer is working on the pedal: send again once it is done."})


class TheTabs(SimulatedCase):
    """Every tab in the bridge's look, and the pictures of the effects in the installer's lists: the
    icon that came with a file in its list, else the one in the pedal's backups."""

    def setUp(self) -> None:
        super().setUp()
        backup = bridge.INSTALLER_DATA / "Backups" / "ms-60bp_20261121-021339"
        (backup / "files").mkdir(parents=True)
        (backup / "manifest.json").write_text(json.dumps({"complete": True, "created_ts": 1.0}))
        for name in ("IRLOADER.ZIC", "DCOMP.ZIC"):
            (backup / "files" / name).write_bytes(zic(4, 8, 0xFF))
        self.app = workshop.start(str(bridge.INSTALLER_DATA), keywave_out=self.folder / "KeyWave Tables")
        self.addCleanup(self.app.stop)

    def get(self, url: str, body=None) -> tuple:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=10) as reply:
                return reply.status, reply.read(), reply.headers["Content-Type"]
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read(), error.headers["Content-Type"]

    def test_each_tab_is_in_the_bridges_look_once(self):
        theme = workshop.FRAMED + b"<style>\n" + workshop.THEME.read_bytes() + b"</style>\n</head>"
        for url in (self.app.url, self.app.url + "nam", self.app.url + "ir-loader", self.app.keywave_url):
            self.assertEqual(self.get(url)[1].count(theme), 1, url)

    def test_the_installers_page_brings_the_pictures_into_its_lists(self):
        page = (workshop.INSTALLER / "zoom_ms_gui.html").read_bytes()
        self.assertEqual(self.get(self.app.url)[1],
                         themed(page).replace(b"</body>", workshop.PICTURES_SCRIPT + b"</body>"))
        self.assertIn(b'getElementById("addedWrap"), document.getElementById("queueWrap")', workshop.PICTURES_SCRIPT)

    def test_a_file_in_the_list_shows_the_icon_it_came_with(self):
        page = workshop.TOOL.read_text(encoding="utf-8")
        pair = {name: base64.b64decode(re.search(f'const {key} = "([^"]+)"', page).group(1))
                for name, key in (("IRLOADER.ZD2", "BASE_ZD2"), ("IRLOADER.ZIC", "BASE_ZIC"))}
        for name, data in pair.items():
            self.assertEqual(json.loads(self.get(self.app.url + f"api/upload?name={name}", data)[1]), {"ok": True})
        self.get(self.app.url + "api/queue/rescan", b"{}")
        self.assertEqual(self.get(self.app.url + "picture?file=IRLOADER.ZD2&from=queue"),
                         (200, effect_pictures.png(pair["IRLOADER.ZIC"]), "image/png"))
        # the same name on the pedal (its list of added effects) shows the backups' picture
        self.assertEqual(self.get(self.app.url + "picture?file=IRLOADER.ZD2"),
                         (200, effect_pictures.png(zic(4, 8, 0xFF)), "image/png"))

    def test_a_file_in_the_list_without_an_icon_shows_the_blank_one_that_gets_installed(self):
        page = workshop.TOOL.read_text(encoding="utf-8")
        effect = base64.b64decode(re.search('const BASE_ZD2 = "([^"]+)"', page).group(1))
        self.assertEqual(json.loads(self.get(self.app.url + "api/upload?name=IRLOADER.ZD2", effect)[1]), {"ok": True})
        self.get(self.app.url + "api/queue/rescan", b"{}")
        blank = effect_pictures.png((workshop.INSTALLER / "BLANK.ZIC").read_bytes())
        self.assertEqual(self.get(self.app.url + "picture?file=IRLOADER.ZD2&from=queue"), (200, blank, "image/png"))
        self.assertEqual(self.get(self.app.url + "picture?file=NOTQUEUED.ZD2&from=queue")[0], 404)

    def test_else_the_picture_in_the_backups(self):
        backed_up = (200, effect_pictures.png(zic(4, 8, 0xFF)), "image/png")
        self.assertEqual(self.get(self.app.url + "picture?file=IRLOADER.ZD2"), backed_up)       # not in the list
        self.assertEqual(self.get(self.app.url + "picture?file=DCOMP.ZD2"), backed_up)
        self.assertEqual(self.get(self.app.url + "picture?file=NOTHERE.ZD2")[0], 404)

    def test_only_the_name_of_an_effect_file_and_only_under_the_secret_address(self):
        for name in ("DCOMP.ZIC", "DCOMP", "files/DCOMP.ZD2", "..%2FDCOMP.ZD2", "..%5CDCOMP.ZD2", ""):
            self.assertEqual(self.get(self.app.url + "picture?file=" + name)[0], 404, name)
        wrong = self.app.url.replace(self.app.url.split("/")[3], "not-the-token")
        self.assertEqual(self.get(wrong + "picture?file=DCOMP.ZD2")[0], 404)


class WithoutABridge(SimulatedCase):
    def test_the_installer_stays_when_the_bridge_cannot_start(self):
        app = workshop.start(str(bridge.INSTALLER_DATA))
        self.addCleanup(app.stop)
        bridge.CONFIG.unlink(missing_ok=True)                   # "config.yaml is missing"
        with mock.patch.object(bridge, "_wait_forever", side_effect=KeyboardInterrupt), \
                mock.patch("webbrowser.open") as opened, mock.patch.object(bridge, "say") as said:
            bridge._run(SimpleNamespace(no_ui=True), app)
        opened.assert_called_once_with(app.url)
        self.assertIn("config.yaml is missing", said.call_args[0][0])
        self.assertIn("the installer runs on its own", said.call_args[0][0])

    def test_the_installer_stays_when_the_setup_in_the_terminal_is_cancelled(self):
        app = SimpleNamespace(url="http://127.0.0.1:1/token/", stop=mock.Mock())
        cancelled = SimpleNamespace(returncode=1)           # e.g. "Are the patches backed up?" – no
        with mock.patch.object(sys, "argv", ["bridge.py", "--open", "--workshop"]), \
                mock.patch.object(sys, "stdin", SimpleNamespace(isatty=lambda: True)), \
                mock.patch.object(signal, "signal"), mock.patch.object(bridge.zs, "tolerant_console"), \
                mock.patch.object(workshop, "start", return_value=app), \
                mock.patch.object(bridge.subprocess, "run", return_value=cancelled) as setup, \
                mock.patch.object(bridge, "_run") as run, mock.patch.object(bridge, "_installer_alone") as alone:
            bridge.main()
        self.assertEqual(setup.call_args[0][0][-1], "setup")
        alone.assert_called_once_with(app, "The bridge is not set up yet")
        run.assert_not_called()
        app.stop.assert_called_once()


class TheCopy(unittest.TestCase):
    def test_the_installer_passes_its_own_self_test_under_this_python(self):
        run = subprocess.run([sys.executable, "zoom_ms_selftest.py"], cwd=workshop.INSTALLER,
                             capture_output=True, text=True, timeout=600)
        self.assertEqual(run.returncode, 0, run.stdout[-2000:] + run.stderr[-2000:])
        self.assertIn("ALL PASSED", run.stdout)


if __name__ == "__main__":
    unittest.main()
