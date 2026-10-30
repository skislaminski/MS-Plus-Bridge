"""Renaming the preset on the pedal from the interface: one message per character, as the pedal
reports a name edited on it (`64 20 00 5F <place> <character>`), then the patch is read back.
Also: a name edited on the pedal shows in the interface, and the program changes the pedal sends
meanwhile are no preset change."""
import unittest
from unittest import mock

import mido

import bridge
import zoom_sysex as zs
from tests.sim import (CONTROLLER_PORT, ID, KEYBOARD_PORT, ZOOM_PORT, SimulatedCase, World, mapping,
                       patch_dump)

PRESET = "0/94"
STOCK = 0x01000020


def names_sent(world: World) -> list:
    """(place, character) of every name character sent to the pedal."""
    return [(param, chr(value)) for _, slot, param, value in world.params if slot == zs.NAME_SLOT]


class Messages(unittest.TestCase):
    def test_a_character_of_the_name_is_a_kind_of_its_own(self):
        message = zs.build_name_character(ID, 27, "~")
        self.assertEqual(zs.to_hex(message), "F0 52 00 6E 64 20 00 5F 1B 7E 00 00 00 00 F7")
        self.assertEqual(zs.classify(message, ID), "rename_patch")

    def test_only_the_28_places_and_the_characters_of_a_name_pass(self):
        for place, character in ((28, "A"), (0, "*"), (0, '"'), (0, "\x7f"), (0, "\n")):
            with self.assertRaises(zs.NotAllowed, msg=(place, character)):
                zs.classify(zs._zoom(ID, 0x64, 0x20, 0x00, 0x5F, place, ord(character), 0, 0, 0, 0), ID)
            with self.assertRaises(ValueError):
                zs.build_name_character(ID, place, character)
        with self.assertRaises(zs.NotAllowed):                          # a value above 127
            zs.classify(zs._zoom(ID, 0x64, 0x20, 0x00, 0x5F, 0, 0x41, 1, 0, 0, 0), ID)

    def test_the_whole_name_is_read_from_the_patch(self):
        body = zs.zoom_body(patch_dump([(STOCK, 0)], name="A NAME OF TWENTY-EIGHT CHARS"), ID)
        self.assertEqual(zs.parse_patch_dump(body).name, "A NAME OF TWENTY-EIGHT CHARS")
        body = zs.zoom_body(patch_dump([(STOCK, 0)], name="  INDENTED"), ID)
        self.assertEqual(zs.parse_patch_dump(body).name, "  INDENTED")    # the places stay where they are


class Renaming(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "rename_patch")

    def rename(self, *names, world=None, **settings) -> list:
        """Calls /api/rename with each name in turn; returns the replies and the state after."""
        self.install({PRESET: [mapping(0, 2)]}, **settings)
        seen = []

        def client(connection):
            connection.at(1.0)
            for name in names:
                seen.append(connection.call("/api/rename", {"name": name})[:2])
            connection.at(1.8)
            seen.append(connection.call("/api/state")[1])

        world = world or World(chain=[STOCK], length=2.0)
        self.run_bridge(world, client)
        self.world = world
        return seen

    def test_a_program_change_passed_to_the_pedal_stops_it(self):
        # the rest of the name went into the preset loaded then
        world = World([(1.05, mido.Message("program_change", program=3))], chain=[STOCK], length=2.0)
        *_, state = self.rename("NEWNAME", world=world, passthrough=True)
        self.assertLess(len(names_sent(world)), 8)
        self.assertEqual(state["rename"]["result"], {"ok": False, "text": "Renaming stopped: preset changed"})

    def test_the_name_goes_to_the_pedal_character_by_character_and_is_read_back(self):
        *replies, state = self.rename(" Clean Solo ")
        self.assertEqual(replies, [(200, {"result": None})])
        self.assertEqual(names_sent(self.world), list(enumerate("Clean Solo".ljust(28))))
        self.assertEqual(self.world.name, "Clean Solo")
        self.assertEqual(state["patch"]["name"], "Clean Solo")
        self.assertEqual(state["rename"], {"approved": True, "busy": False, "result": {
            "ok": True, "text": "Preset 095 is called “Clean Solo” now (28 of 28 characters confirmed) – "
                                "save the preset on the pedal to keep the name"}})

    def test_a_name_the_pedal_did_not_take_is_said(self):
        world = World(chain=[STOCK], length=2.0)
        world.takes_names = False
        *_, state = self.rename("Clean", world=world)
        self.assertFalse(state["rename"]["result"]["ok"])
        self.assertIn("The pedal did not take the name “Clean”: it reports “USER-001” (0 of 28", self.output)

    def test_names_that_cannot_be_sent(self):
        replies = self.rename("", "   ", "A" * 29, "Crème", "a*b")[:-1]
        self.assertEqual([status for status, _ in replies], [400] * 5)
        self.assertIn("at most 28 characters", replies[2][1]["error"])
        self.assertIn("Not possible in a name: è", replies[3][1]["error"])
        self.assertIn("Not possible in a name: *", replies[4][1]["error"])
        self.assertEqual(names_sent(self.world), [])

    def test_one_name_at_a_time(self):
        replies = self.rename("First", "Second")[:-1]
        self.assertEqual(replies[1], (400, {"error": "The preset is being renamed already."}))
        self.assertEqual(self.world.name, "First")

    def test_without_approval_nothing_is_sent(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        *replies, state = self.rename("Clean")
        self.assertEqual(replies[0][0], 400)
        self.assertIn("python probe.py approve rename_patch query_patch", replies[0][1]["error"])
        self.assertFalse(state["rename"]["approved"])
        self.assertEqual(names_sent(self.world), [])

    def test_a_preset_change_stops_it(self):
        world = World([(1.1, lambda world: world.preset_change(5))], chain=[STOCK], length=2.0)
        with mock.patch.object(bridge, "NAME_SEND_SECONDS", 0.02):
            self.rename("Clean", world=world)
        self.assertIn("Renaming stopped: preset changed", self.output)
        self.assertLess(len(names_sent(world)), 28)                     # the other preset gets none
        self.assertTrue(all(seconds < 1.2 for seconds, slot, *_ in world.params if slot == zs.NAME_SLOT))


class EditedOnThePedal(SimulatedCase):
    def test_the_new_name_shows_and_the_program_changes_meanwhile_are_no_preset_change(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        self.install({PRESET: [mapping(0, 2)]})
        seen = {}

        def client(connection):
            connection.at(1.4)
            seen["state"] = connection.call("/api/state")[1]

        world = World([(1.0, lambda world: world.edit_name("DRIVE")), (1.2, mido.Message("control_change", control=25, value=127))],
                      chain=[STOCK], length=1.6)
        self.run_bridge(world, client)
        self.assertEqual(seen["state"]["patch"]["name"], "DRIVE")
        self.assertEqual(seen["state"]["patch"]["display"], "095")
        self.assertNotIn("Preset 001", self.output)
        self.assertEqual([(slot, param, value) for _, slot, param, value in world.params if slot != zs.NAME_SLOT],
                         [(0, 2, 100)])                                  # still preset 095's assignment

    def test_a_name_the_pedal_gives_by_itself_is_read_from_the_patch(self, synth=None, ports=None):
        # 2026-10-21: FMSynth into the empty preset 014 – the pedal called it USER-012 and reported
        # U S E R, 1 and spaces in places that held spaces already, not the -, 0 and 2
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        self.install({PRESET: [mapping(0, 2)]}, synth=synth)
        seen = {}

        def client(connection):
            connection.at(2.4)
            seen["state"] = connection.call("/api/state")[1]

        reported = [(0, "U"), (1, "S"), (2, "E"), (3, "R"), (20, " "), (6, "1"), (16, " ")] + [
            (place, " ") for place in range(21, 28)]
        world = World([(1.0, lambda world: world.name_itself("USER-012", reported))], chain=[STOCK], length=2.6,
                      **({"ports": ports} if ports else {}))
        world.name = "Empty"
        self.run_bridge(world, client)
        self.assertEqual(seen["state"]["patch"]["name"], "USER-012")

    def test_so_also_with_a_keyboard_synth_set_up_and_none_in_the_preset(self):
        # the patch read back was put off (a query a second before), and then left out: the synth
        # effect was known to be absent
        self.test_a_name_the_pedal_gives_by_itself_is_read_from_the_patch(
            synth={"keyboard": KEYBOARD_PORT, "channel": 1},
            ports=lambda seconds: [ZOOM_PORT, CONTROLLER_PORT, KEYBOARD_PORT])


if __name__ == "__main__":
    unittest.main()
