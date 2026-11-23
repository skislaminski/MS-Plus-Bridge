"""Scene A and B that a modified firmware keeps in every preset of the pedal (pedal_scenes.py): the
question for them, reading, editing, writing and reading back – against the simulated pedal, which
alone knows the PROVISIONAL form of the messages.
"""
import unittest
from unittest import mock

import bridge
import pedal_scenes
import zoom_sysex as zs
from tests.sim import ID, World
from tests.test_effects import FIRST, SECOND, THIRD, EffectCase

KEYSYNTH, DELAY, ROOM = 0x07000F61, 0x0A000020, 0x0B000020
SIX = [FIRST, SECOND, THIRD, DELAY, ROOM, 0x06000010]
SCENE_KINDS = tuple(pedal_scenes.KINDS)
PLACE = (9, 4)                                   # the simulated pedal's preset: 095


def scenes(a=None, b=None) -> dict:
    """Scene A and B from (effects, values) pairs."""
    made = pedal_scenes.empty()
    for name, given in (("A", a), ("B", b)):
        if given:
            made[name] = {"effects": dict(given[0]), "values": dict(given[1])}
    return made


STORED = scenes(a=({0: True, 2: False}, {(0, 2): (FIRST, 40)}))


class Provisional(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()                        # a SimulatedCase's own temporary installation too
        patcher = mock.patch.object(pedal_scenes, "COMMAND", pedal_scenes.PROVISIONAL)
        patcher.start()
        self.addCleanup(patcher.stop)


class Messages(Provisional):
    def test_scenes_go_into_bytes_and_back(self):
        full = scenes(a=({slot: slot % 2 == 0 for slot in range(6)},
                         {(slot, 2 + slot % 3): (effect, 10 * slot) for slot, effect in enumerate(SIX)}),
                      b=({5: False}, {(3, 2): (DELAY, 962)}))
        for case in (pedal_scenes.empty(), STORED, full):
            with self.subTest(case=case):
                self.assertEqual(pedal_scenes.decode(pedal_scenes.encode(case)), case)
        self.assertEqual(pedal_scenes.encode(pedal_scenes.empty()), bytes(6))       # two empty scenes
        message = pedal_scenes.build_write(ID, *PLACE, STORED)
        self.assertEqual(zs.classify(message, ID), "write_pedal_scenes")
        self.assertTrue(all(byte < 0x80 for byte in message[1:-1]))
        for wrong in (b"\x01", bytes(5), bytes([0x40, 0, 0, 0, 0, 0]), pedal_scenes.encode(STORED) + b"\x00"):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                pedal_scenes.decode(wrong)

    def test_the_messages_are_on_the_allowlist_only_as_they_should_be(self):
        self.assertEqual(zs.classify(pedal_scenes.build_query(ID), ID), "query_pedal_scenes")
        self.assertEqual(zs.classify(pedal_scenes.build_read(ID, *PLACE), ID), "read_pedal_scenes")
        refused = {
            "no such preset": pedal_scenes.build_read(ID, 10, 0),
            "a Key knob": pedal_scenes.build_write(ID, *PLACE, scenes(a=({}, {(0, 2): (KEYSYNTH, 500)}))),
            "above the knob's highest": pedal_scenes.build_write(ID, *PLACE, scenes(a=({}, {(0, 2): (FIRST, 101)}))),
            "no knob of that effect": pedal_scenes.build_write(ID, *PLACE, scenes(a=({}, {(0, 30): (FIRST, 1)}))),
            "not scenes": zs._zoom(ID, pedal_scenes.COMMAND, pedal_scenes.WRITE, 9, 0, 4, 0, 1),
        }
        for name, message in refused.items():
            with self.subTest(name), self.assertRaises(zs.NotAllowed):
                zs.classify(message, ID)

    def test_until_the_bytes_are_agreed_none_of_them_is_sent_or_heard(self):
        message = pedal_scenes.build_read(ID, *PLACE)
        with mock.patch.object(pedal_scenes, "COMMAND", None):
            with self.assertRaises(zs.NotAllowed):
                zs.classify(message, ID)
            self.assertIsNone(pedal_scenes.parse(message[4:-1]))
        self.assertNotIn("read_pedal_scenes", zs.KINDS)       # not offered for approval for a real pedal

    def test_the_pedals_answers(self):
        body = lambda *data: bytes([pedal_scenes.COMMAND, *data])
        self.assertEqual(pedal_scenes.parse(body(0, 1, 8)), ("query", 1, 8))
        self.assertEqual(pedal_scenes.parse(body(1, 0, 9, 0, 4, 0) + pedal_scenes.encode(STORED)),
                         ("read", 0, PLACE, STORED))
        self.assertEqual(pedal_scenes.parse(body(1, 3, 9, 0, 4, 0)), ("read", 3, PLACE, None))
        self.assertEqual(pedal_scenes.parse(body(2, 0, 9, 0, 4, 0)), ("write", 0, PLACE))
        self.assertIsNone(pedal_scenes.parse(body(1, 0, 9, 0, 4, 0, 9)))           # no scenes in it


class SceneRun(Provisional, EffectCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", *SCENE_KINDS)
        self.install()

    def world(self, chain=(FIRST, SECOND, THIRD), length=3.0, stored=STORED, **settings) -> World:
        world = World(chain=list(chain), length=length, program=PLACE[1])
        world.bank = PLACE[0]
        world.scene_support = True
        if stored is not None:
            world.pedal_scenes[PLACE] = pedal_scenes.encode(stored)
        for name, value in settings.items():
            setattr(world, name, value)
        return world

    def act(self, world: World, steps) -> list:
        """Runs the bridge with steps of (seconds, action, data) for /api/scenes, or (seconds, None,
        None) for the state of the pedal's scenes; returns what each gave."""
        seen = []

        def client(connection):
            for seconds, action, data in steps:
                connection.at(seconds)
                if action is None:
                    seen.append(connection.call("/api/state")[1]["scenes"]["pedal"])
                else:
                    seen.append(connection.call("/api/scenes", dict(data or {}, action=action))[1])
        self.run_bridge(world, client)
        return seen

    def held(self, world: World) -> dict:
        return pedal_scenes.decode(world.pedal_scenes[PLACE])

    def assert_allowed(self, world: World) -> None:
        for _, text in world.sent:                       # everything sent is on the allowlist
            zs.classify(bytes.fromhex(text.replace(" ", "")), ID)


class ReadingAndWriting(SceneRun):
    def test_read_edit_write_and_read_back(self):
        world = self.world()
        seen = self.act(world, [
            (1.0, "pedal_read", None), (1.3, None, None),
            (1.4, "pedal_value", {"scene": "B", "slot": 1, "param": 3, "value": 70}),
            (1.45, "pedal_switch", {"scene": "B", "slot": 0, "on": False}),
            (1.5, "pedal_switch", {"scene": "A", "slot": 2, "on": None}),       # out of scene A
            (1.55, None, None), (1.6, "pedal_write", None), (1.9, None, None)])
        read, edited, written = seen[1], seen[5], seen[7]
        self.assertEqual(read["place"]["display"], "095")
        self.assertFalse(read["dirty"])
        self.assertEqual(read["scenes"]["A"]["effects"], [{"slot": 0, "on": True}, {"slot": 2, "on": False}])
        self.assertEqual(read["scenes"]["A"]["values"][0]["label"], "BlackOpt · Comp")
        self.assertEqual(read["result"]["text"], "Scenes A and B of preset 095 read")
        self.assertTrue(edited["dirty"])
        self.assertEqual(edited["scenes"]["B"]["values"], [{"slot": 1, "param": 3, "value": 70, "effect": "01000030",
                                                            "label": "OptComp · Lo", "away": False}])
        self.assertEqual(written["result"], {"ok": True, "text": "Scenes A and B written into preset 095 and read "
                                                                "back the same"})
        self.assertFalse(written["dirty"])
        self.assertEqual(self.held(world), scenes(a=({0: True}, {(0, 2): (FIRST, 40)}),
                                                  b=({0: False}, {(1, 3): (SECOND, 70)})))
        reads = [text for _, text in world.sent if text.startswith(f"F0 52 00 6E {pedal_scenes.COMMAND:02X} 01")]
        self.assertEqual(len(reads), 2)                                       # read, and read back
        self.assert_allowed(world)

    def test_empty_scenes_and_all_six_effects(self):
        world = self.world(chain=SIX, stored=None)
        steps = [(1.0, "pedal_read", None), (1.3, None, None)]
        steps += [(1.4 + 0.02 * slot, "pedal_switch", {"scene": "A", "slot": slot, "on": slot != 3}) for slot in range(6)]
        steps += [(1.55 + 0.02 * slot, "pedal_value", {"scene": "B", "slot": slot, "param": 2, "value": slot % 2})
                  for slot in range(6)]
        steps += [(1.8, "pedal_write", None), (2.1, None, None)]
        seen = self.act(world, steps)
        self.assertEqual(seen[1]["scenes"], {"A": {"effects": [], "values": []}, "B": {"effects": [], "values": []}})
        self.assertEqual([entry for entry in seen[2:14] if entry.get("error")], [])
        self.assertTrue(seen[-1]["result"]["ok"], seen[-1]["result"])
        self.assertEqual(self.held(world), scenes(
            a=({slot: slot != 3 for slot in range(6)}, {}),
            b=({}, {(slot, 2): (effect, slot % 2) for slot, effect in enumerate(SIX)})))

    def test_what_a_scene_cannot_hold_is_refused(self):
        world = self.world(chain=(KEYSYNTH, FIRST), scene_most=1)
        seen = self.act(world, [
            (1.0, "pedal_read", None), (1.3, None, None),
            (1.4, "pedal_value", {"scene": "B", "slot": 0, "param": 2, "value": 500}),     # KeySynth's Key
            (1.45, "pedal_value", {"scene": "B", "slot": 1, "param": 2, "value": 101}),    # above BlackOpt's Comp
            (1.5, "pedal_value", {"scene": "B", "slot": 0, "param": 3, "value": 2}),       # Wave1: fine
            (1.55, "pedal_value", {"scene": "B", "slot": 1, "param": 2, "value": 5}),      # one more than it keeps
            (1.6, "pedal_value", {"scene": "B", "slot": 4, "param": 2, "value": 5}),       # no effect there
            (1.7, None, None)])
        self.assertNotIn((0, 2), {(choice["slot"], choice["param"]) for choice in seen[1]["choices"]})
        self.assertIn({"slot": 0, "param": 3, "name": "KeySynth · Wave1", "max": 9}, seen[1]["choices"])
        self.assertIn("Key knob", seen[2]["error"])
        self.assertIn("101 is not a value", seen[3]["error"])
        self.assertNotIn("error", seen[4])
        self.assertIn("the pedal keeps 1", seen[5]["error"])
        self.assertIn("no effect 5", seen[6]["error"])
        self.assertEqual(len(seen[7]["scenes"]["B"]["values"]), 1)

    def test_a_scene_the_pedal_did_not_keep_as_written_is_not_done(self):
        world = self.world(scene_store=lambda data: pedal_scenes.encode(STORED))     # keeps the old ones
        seen = self.act(world, [(1.0, "pedal_read", None),
                                (1.3, "pedal_switch", {"scene": "B", "slot": 1, "on": True}),
                                (1.4, "pedal_write", None), (1.8, None, None)])
        self.assertFalse(seen[-1]["result"]["ok"])
        self.assertIn("holds other scenes than were written", seen[-1]["result"]["text"])
        self.assertTrue(seen[-1]["dirty"])                  # the edit is still not on the pedal

    def test_a_write_the_pedal_refuses_says_why(self):
        world = self.world(scene_status=3)
        seen = self.act(world, [(1.0, "pedal_read", None),
                                (1.3, "pedal_switch", {"scene": "B", "slot": 1, "on": True}),
                                (1.4, "pedal_write", None), (1.8, None, None)])
        self.assertEqual(seen[-1]["result"], {"ok": False, "text": "Scenes not written into preset 095: the pedal is "
                                                                  "busy (a preset being saved or edited on it)"})
        self.assertEqual(self.held(world), STORED)


class PresetChanged(SceneRun):
    def test_scenes_being_read_when_the_preset_changes_are_not_taken(self):
        world = self.world(scene_delay=0.4)
        world.script.append((1.2, lambda world: world.preset_change(5)))
        seen = self.act(world, [(1.0, "pedal_read", None), (1.7, None, None)])
        self.assertEqual(seen[-1]["result"], {"ok": False, "text": "Reading the scenes stopped: preset changed"})
        self.assertIsNone(seen[-1]["scenes"])           # the answer that came after it is not taken

    def test_scenes_are_written_only_into_the_preset_they_came_from(self):
        world = self.world()
        world.script.append((1.3, lambda world: world.preset_change(5)))
        seen = self.act(world, [(1.0, "pedal_read", None),
                                (1.5, "pedal_switch", {"scene": "B", "slot": 1, "on": True}),
                                (1.55, "pedal_write", None), (1.6, None, None)])
        self.assertIn("scenes of preset 095", seen[1]["error"])
        self.assertIn("scenes of preset 095, the pedal is on 096", seen[2]["error"])
        self.assertEqual(seen[3]["place"]["display"], "095")
        self.assertNotIn((9, 5), world.pedal_scenes)
        self.assertEqual(self.held(world), STORED)

    def test_scenes_on_their_way_when_the_preset_changes_are_read_back_by_their_address(self):
        world = self.world(scene_delay=0.3)
        world.script.append((1.7, lambda world: world.preset_change(5)))
        seen = self.act(world, [(1.0, "pedal_read", None),
                                (1.5, "pedal_switch", {"scene": "B", "slot": 1, "on": True}),
                                (1.6, "pedal_write", None), (2.4, None, None)])
        self.assertTrue(seen[-1]["result"]["ok"], seen[-1]["result"])
        self.assertEqual(self.held(world)["B"]["effects"], {1: True})
        self.assertNotIn((9, 5), world.pedal_scenes)


class NoAnswer(SceneRun):
    def test_a_read_without_answer_is_given_up(self):
        world = self.world(scene_answers=False)
        seen = self.act(world, [(1.0, "pedal_read", None), (1.5, None, None), (2.7, None, None)])
        self.assertEqual(seen[1]["busy"], "read")
        self.assertEqual(seen[2]["result"], {"ok": False, "text": "The pedal did not answer: the scenes of preset 095 "
                                                                 "were not read"})
        self.assertIsNone(seen[2]["busy"])

    def test_a_write_without_answer_is_not_called_done(self):
        world = self.world(length=3.6)
        world.script.append((1.4, lambda world: setattr(world, "scene_answers", False)))
        seen = self.act(world, [(1.0, "pedal_read", None),
                                (1.5, "pedal_switch", {"scene": "B", "slot": 1, "on": True}),
                                (1.55, "pedal_write", None), (3.2, None, None)])
        self.assertEqual(seen[-1]["result"]["ok"], False)
        self.assertIn("whether preset 095 holds the scenes is not known", seen[-1]["result"]["text"])
        self.assertTrue(seen[-1]["dirty"])


class WithoutPedalScenes(SceneRun):
    def test_a_firmware_without_scenes_gets_the_bridges_own(self):
        world = self.world(scene_support=False, length=3.2)
        seen = self.act(world, [(1.0, None, None), (2.9, None, None), (2.95, "pedal_read", None),
                                (3.0, "add", {"name": "Solo"})])
        self.assertIsNone(seen[1])                       # no scenes of the pedal: the bridge's are offered
        self.assertIn("keeps no scenes of its own: it did not answer", seen[2]["error"])
        self.assertNotIn("error", seen[3])
        self.assertEqual(self.instance.scenes[PLACE][0]["name"], "Solo")
        queries = [text for _, text in world.sent if text == zs.to_hex(pedal_scenes.build_query(ID))]
        self.assertEqual(len(queries), 1)

    def test_while_the_bytes_are_not_agreed_the_pedal_is_not_asked(self):
        world = self.world()
        with mock.patch.object(pedal_scenes, "COMMAND", None):
            seen = self.act(world, [(1.0, None, None), (1.1, "pedal_read", None)])
        self.assertIsNone(seen[0])
        self.assertIn("not agreed with the firmware yet", seen[1]["error"])
        self.assertFalse([text for _, text in world.sent if text.startswith(f"F0 52 00 6E {pedal_scenes.PROVISIONAL:02X}")])

    def test_without_approval_nothing_is_asked(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        world = self.world()
        seen = self.act(world, [(1.0, "pedal_read", None)])
        self.assertIn("asking the pedal about its scenes is not approved", seen[0]["error"])
        self.assertFalse([text for _, text in world.sent if text.startswith(f"F0 52 00 6E {pedal_scenes.PROVISIONAL:02X}")])


if __name__ == "__main__":
    unittest.main()
