"""The pedal alone: with no keyboard, external sequencer or foot controller connected the bridge
connects to the MS-60B+ all the same, its preset is edited in the interface, and a controller
joins when it is plugged in and leaves when it is pulled, the session staying with the pedal
(2026-10-22, the user's wish).

Run against the simulated devices from sim.py.
"""
import yaml

import bridge
import zoom_sysex as zs
from tests.sim import CONTROLLER_PORT, KEYBOARD_PORT, ZOOM_PORT, SimulatedCase, World, cc, learned, mapping, \
    note_off, note_on
from tests.test_headless import HeadlessCase
from tests.test_synth import EVERYTHING, KEYSYNTH, SYNTH

PRESET = "0/94"
STOCK, ROOM = 0x01000020, 0x0B000020           # BlackOpt, BrghtRoom (Decay 0–29)
K = zs.key_for
PEDAL_ONLY = lambda seconds: [ZOOM_PORT]
ENABLE = "F0 52 00 6E 50 F7"


def sent(world: World) -> list:
    """(slot, param, value) of every "set parameter" to an effect of the chain (not the name's)."""
    return [(slot, param, value) for _, slot, param, value in world.params if slot != zs.NAME_SLOT]


class PedalAlone(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "rename_patch", "switch_effect")

    def test_the_preset_is_edited_with_no_controller_connected(self):
        seen = {}

        def client(connection):
            connection.at(1.0)
            effects = lambda **data: connection.call("/api/effects", data)[:2]
            seen["knob"] = effects(action="knob", slot=1, param=3, value=29, effect=f"{ROOM:08X}")
            seen["switch"] = effects(action="switch", slot=0, on=False)
            seen["rename"] = connection.call("/api/rename", {"name": "Clean"})[:2]
            seen["learn"] = effects(action="learn", slot=0)            # a switch needs a controller to learn
            connection.at(2.0)
            seen["state"] = connection.call("/api/state")[1]
        self.install({PRESET: [mapping()]}, {PRESET: learned((0, 2, 0, 100))})   # the foot controller is set up
        world = World(ports=PEDAL_ONLY, chain=[STOCK, ROOM], length=2.3)
        world.whole_patch = True                                       # the patch read back after the name has it
        self.run_bridge(world, client)
        self.assertEqual([seen[call] for call in ("knob", "switch", "rename")], [(200, {"result": None})] * 3)
        self.assertEqual(seen["learn"], (400, {"error": "No controller is connected."}))
        self.assertEqual(sorted(sent(world)), [(0, zs.SWITCH_PARAM, 0), (1, 3, 29)])
        self.assertEqual(world.name, "Clean")
        state = seen["state"]
        self.assertTrue(state["connected"])
        self.assertEqual((state["status"], state["patch"]["name"]), ("Ready", "Clean"))
        self.assertEqual(state["chain_effects"][1]["values"]["3"], 29)
        self.assertEqual(state["devices"]["expression"], {"name": CONTROLLER_PORT, "port": None, "choices": []})
        self.assertFalse(state["effects"]["controller"])
        self.assertIn(f"Ready: no controller → {ZOOM_PORT}, firmware 1.20", self.output)
        self.assertEqual(self.output.count(bridge.NO_CONTROLLER), 1)  # once, not at every check of the ports
        self.assertNotIn("Waiting for devices", self.output)
        self.assertEqual(world.count(ENABLE), 1)

    def test_controllers_join_and_leave_and_the_editor_goes_on(self):
        ports = lambda seconds: [ZOOM_PORT] + (EVERYTHING(seconds)[1:] if 1.0 < seconds < 2.0 else [])
        seen = {}

        def client(connection):
            connection.at(1.7)
            seen["joined"] = connection.call("/api/state")[1]
            connection.at(2.4)                                         # both gone
            seen["knob"] = connection.call("/api/effects", {"action": "knob", "slot": 2, "param": 3, "value": 20,
                                                            "effect": f"{ROOM:08X}"})[:2]
            seen["left"] = connection.call("/api/state")[1]
        script = [(1.4, note_on(40)), (1.5, note_off(40)), (1.6, cc(64)),
                  (2.2, cc(127)), (2.2, note_on(45))]                  # played on controllers that are gone
        world = World(script, ports=ports, chain=[KEYSYNTH, STOCK, ROOM], length=2.8)
        self.install({PRESET: [mapping(1, 2)]}, {PRESET: learned((1, 2, 0, 100))}, synth=SYNTH)
        self.run_bridge(world, client)
        self.assertEqual(sent(world), [(0, 2, K(40)), (0, 2, 0), (1, 2, 50), (2, 3, 20)])
        self.assertEqual(seen["knob"], (200, {"result": None}))
        self.assertEqual({key: seen["joined"]["synth"][key] for key in ("keyboard", "expression")},
                         {"keyboard": True, "expression": True})
        self.assertTrue(seen["joined"]["effects"]["controller"])
        self.assertEqual({key: seen["left"]["synth"][key] for key in ("keyboard", "expression", "playable")},
                         {"keyboard": False, "expression": False, "playable": False})
        self.assertTrue(seen["left"]["connected"])
        for port in (KEYBOARD_PORT, CONTROLLER_PORT):
            self.assertIn(f"{port} connected", self.output)
            self.assertIn(f"{port} is gone", self.output)
        self.assertIn(f"Ready: no controller → {ZOOM_PORT}", self.output)
        self.assertEqual(self.output.count(bridge.NO_CONTROLLER), 2)  # at the start, and when the last one left
        self.assertNotIn("Connection lost", self.output)
        self.assertEqual(world.count(ENABLE), 1)                       # one session throughout

    def test_a_config_without_the_line_of_the_expression_controller_has_none(self):
        """Its line under “ports:” deleted, the bridge starts as with an empty one, and says that
        no controller is set up: none can join."""
        self.install()
        cfg = yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))
        del cfg["ports"]["chocolate"]
        cfg["expression"].update(cc=None, channel=None)
        bridge.CONFIG.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        self.run_bridge(World(ports=lambda seconds: [ZOOM_PORT, CONTROLLER_PORT], length=1.2))
        self.assertIn(f"Ready: no controller → {ZOOM_PORT}", self.output)
        self.assertEqual(self.output.count(bridge.NO_CONTROLLER_SET_UP), 1)
        self.assertNotIn(bridge.NO_CONTROLLER, self.output)
        self.assertNotIn(f"{CONTROLLER_PORT} connected", self.output)


class StatusLight(HeadlessCase):
    def test_the_pi_waits_for_a_controller_as_it_is_for_playing(self):
        self.write_card()
        self.forget_the_installation()
        ports = lambda seconds: EVERYTHING(seconds) if 1.0 < seconds < 1.8 else [ZOOM_PORT]
        world = World([(1.3, note_on(45)), (1.4, note_off(45))], ports=ports, chain=[STOCK, KEYSYNTH], length=2.2)
        self.run_headless(world)
        self.assertEqual(self.led.states, ["waiting", "ready", "waiting"])
        self.assertEqual([value for *_, value in world.params], [K(45), 0])     # played while it was there
        self.assertIn(f"Ready: no controller → {ZOOM_PORT}", self.journal)
        # without an interface (not --listen) nothing can be edited there, so the line does not say so
        self.assertEqual(self.journal.count("No controller connected – a controller joins when it is plugged in"), 2)
        self.assertNotIn(bridge.NO_CONTROLLER, self.journal)
        self.assertNotIn("Connection lost", self.journal)
