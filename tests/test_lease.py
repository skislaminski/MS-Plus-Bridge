"""The pedal let go of for another program: with the button (/api/pedal), and by itself while the
Zoom MS Installer is open or its alarm is up. Meanwhile nothing goes to the pedal; afterwards the
bridge connects again and reads the pedal's effects once more (another program may have changed them)."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import bridge
import zoom_sysex as zs
from tests.sim import SimulatedCase, World, cc, mapping, patch_dump
from tests import test_add_effect          # (its test classes imported by name would run here once more)
from tests.test_add_effect import ROOM, STOCK
from tests.test_change_effect import CHORUS, HOGI

EDIT_OFF, PC_MODE = "F0 52 00 6E 51 F7", "F0 52 00 6E 52 F7"


def sent_between(world: World, start: float, end: float) -> list:
    return [text for seconds, text in world.sent if start < seconds < end]


class FakeInstaller:
    """The Zoom MS Installer as this bridge sees it: its running.json, and its address answering."""

    def __init__(self, folder):
        class Ping(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_GET(self) -> None:
                body = json.dumps({"app": "Zoom MS Installer"}).encode() if self.path == "/token/api/ping" else b"{}"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Ping)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.lock = folder / "running.json"
        folder.mkdir(exist_ok=True)

    def open(self, world=None) -> None:
        self.lock.write_text(json.dumps({"url": f"http://127.0.0.1:{self.server.server_port}/token/"}))

    def close(self, world=None) -> None:
        """As the installer ends: its running.json stays, nothing answers at its address."""
        self.server.shutdown()
        self.server.server_close()


class TheButton(SimulatedCase):
    def test_nothing_goes_to_the_pedal_while_it_is_released(self):
        self.install({"0/94": [mapping(0, 2)]})
        seen = {}

        def client(connection):
            connection.at(1.0)
            seen["release"] = connection.call("/api/pedal", {"action": "release"})[:2]
            seen["released"] = world.now()
            connection.at(1.6)
            seen["state"] = connection.call("/api/state")[1]
            seen["reclaiming"] = world.now()
            seen["reclaim"] = connection.call("/api/pedal", {"action": "reclaim"})[:2]
            connection.at(2.4)
            seen["after"] = connection.call("/api/state")[1]

        world = World([(1.3, cc(40))], length=2.6)              # the controller moved while released
        self.run_bridge(world, client)
        self.assertEqual(seen["release"], (200, {"result": None}))
        self.assertIn(EDIT_OFF, [text for seconds, text in world.sent if seconds < seen["released"]])
        self.assertEqual(sent_between(world, seen["released"], seen["reclaiming"]), [])
        self.assertFalse(seen["state"]["connected"])
        self.assertTrue(seen["state"]["lease"])
        self.assertEqual(seen["state"]["status"], "Pedal released: for another program")
        self.assertEqual(seen["reclaim"], (200, {"result": None}))
        self.assertTrue(seen["after"]["connected"])
        self.assertFalse(seen["after"]["lease"])

    def test_not_while_a_change_is_on_its_way(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "change_effect")
        self.install({"0/94": [mapping(0, 2)]})
        known = json.loads(bridge.MEASUREMENTS.read_text(encoding="utf-8"))
        known["effects"] = [f"{HOGI:08X}"]
        bridge.MEASUREMENTS.write_text(json.dumps(known), encoding="utf-8")
        seen = []

        def client(connection):
            connection.at(1.0)
            seen.append(connection.call("/api/effects", {"action": "change", "slot": 1, "effect": f"{HOGI:08X}"})[:2])
            seen.append(connection.call("/api/pedal", {"action": "release"})[:2])
            seen.append(connection.call("/api/state")[1])

        # the patch that would show the change never comes: the change stays on its way
        world = World([(0.9, lambda world: setattr(world, "patches", False))], chain=[STOCK, CHORUS], length=1.6)
        self.run_bridge(world, client)
        self.assertEqual(seen[0], (200, {"result": None}))
        self.assertEqual(seen[1], (400, {"error": "Something is being changed on the pedal: try again in a moment."}))
        self.assertTrue(seen[2]["connected"])
        self.assertFalse(seen[2]["lease"])

    def test_an_unknown_action(self):
        self.install({})
        seen = []
        self.run_bridge(World(length=1.0), lambda connection: seen.append(
            connection.call("/api/pedal", {"action": "steal"})[:2]))
        self.assertEqual(seen, [(400, {"error": "Unknown action."})])


class TheInstaller(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.install({"0/94": [mapping(0, 2)]})
        self.installer = FakeInstaller(bridge.INSTALLER_DATA)
        self.addCleanup(self.installer.server.server_close)

    def test_while_it_is_open_the_bridge_does_not_connect(self):
        self.installer.open()
        seen = {}

        def client(connection):
            connection.at(0.8)
            seen.update(connection.call("/api/state")[1])
            connection.at(1.0)               # (the world's own script runs only while a port of it is read)
            self.installer.close()

        world = World(length=2.0)
        self.run_bridge(world, client)
        self.assertEqual(sent_between(world, 0.0, 1.0), [])
        self.assertEqual(seen["status"], "Pedal released: the Zoom MS Installer is open")
        self.assertFalse(seen["connected"])
        self.assertTrue(sent_between(world, 1.0, 2.0))                 # closed: the bridge is back
        self.assertEqual(self.output.count("Pedal released: the Zoom MS Installer is open"), 1)

    def test_once_it_opens_the_bridge_lets_go(self):
        world = World([(1.0, self.installer.open)], length=2.0)
        self.run_bridge(world)
        self.assertTrue(sent_between(world, 0.0, 1.0))
        let_go = next(seconds for seconds, text in world.sent if seconds > 1.0 and text == EDIT_OFF)
        self.assertEqual(sent_between(world, let_go, 2.0), [])
        self.assertIn("Pedal released: the Zoom MS Installer is open", self.output)

    def test_its_alarm_keeps_the_bridge_away_also_when_it_is_closed(self):
        (bridge.INSTALLER_DATA / "unresolved.json").write_text('{"models": [39]}')
        world = World(length=1.0)
        self.run_bridge(world)
        self.assertEqual(world.sent, [])
        self.assertIn("could not verify a transfer – leave the pedal on and run its rescue", self.output)

    def test_only_the_installer_on_this_computer_is_asked(self):
        (bridge.INSTALLER_DATA / "running.json").write_text(json.dumps({"url": "http://example.com/token/"}))
        with mock.patch("urllib.request.urlopen") as asked:
            self.assertIsNone(bridge.installer_holds(bridge.INSTALLER_DATA))
        asked.assert_not_called()


class ReadAgain(SimulatedCase):
    """As at the start (test_add_effect.ReadAtStart): the pedal's files are read once it is back."""

    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "read_pedal", "query_preset")
        patcher = mock.patch.object(bridge, "PRESET_READ_GAP", 0.001)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_effects_are_read_again_once_the_pedal_is_back(self):
        self.install({})

        def client(connection):
            connection.at(2.4)
            connection.call("/api/pedal", {"action": "release"})
            connection.at(2.8)
            connection.call("/api/pedal", {"action": "reclaim"})

        world = World(program=8, chain=[STOCK, ROOM], length=5.5)
        world.files = test_add_effect.ReadPedal.FILES
        world.stored[(0, 8)] = zs.unpack_7bit(patch_dump([(STOCK, 0), (ROOM, 0)], name=world.name)[9:-6])
        self.run_bridge(world, client)
        self.assertEqual(sum(1 for _, text in world.sent if text == PC_MODE), 2)
        self.assertFalse(world.pc_mode)


if __name__ == "__main__":
    unittest.main()
