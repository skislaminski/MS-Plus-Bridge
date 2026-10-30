"""Another effect in a place of the chain, from the interface: parameter 1 of the place with the
effect's whole id (`64 20 00 <slot> 01 <id, 7 bits per byte>`), only to an effect the pedal itself
reported in one of its presets, only into a place it reported an effect in, and read back from
the patch. The effects the pedal reports are kept (measurements.json, or the settings file)."""
import json
import unittest

import yaml

import bridge
import config_schema as cs
import zoom_sysex as zs
from tests.sim import ID, PROJECT, SimulatedCase, World, mapping

PRESET = "0/94"
STOCK, CHORUS, ROOM, HOGI = 0x01000020, 0x06000020, 0x0B000020, 0x09000F83   # BlackOpt, ChorusOne, BrghtRoom


def changes_sent(world: World) -> list:
    """(slot, effect id) of every change of an effect sent to the pedal."""
    found = []
    for _, text in world.sent:
        raw = bytes.fromhex(text.replace(" ", ""))
        if raw[4:7] == b"\x64\x20\x00" and raw[8] == zs.EFFECT_PARAM and raw[7] < zs.MAX_SLOTS:
            found.append((raw[7], zs.decode_effect(raw[9:14])))
    return found


class Messages(unittest.TestCase):
    def test_the_id_goes_as_the_value_of_parameter_1_seven_bits_per_byte(self):
        message = zs.build_change_effect(ID, 1, HOGI)
        self.assertEqual(zs.to_hex(message), "F0 52 00 6E 64 20 00 01 01 03 1F 00 48 00 F7")
        self.assertEqual(zs.decode_effect(message[9:14]), HOGI)
        self.assertEqual(zs.classify(message, ID, changeable=(0, 1), effects={HOGI}), "change_effect")

    def test_only_a_reported_effect_into_a_place_with_an_effect(self):
        message = zs.build_change_effect(ID, 1, HOGI)
        for changeable, effects in (((0, 1), {ROOM}), ((0,), {HOGI}), ((), ())):
            with self.assertRaises(zs.NotAllowed, msg=(changeable, effects)):
                zs.classify(message, ID, changeable=changeable, effects=effects)
        with self.assertRaises(zs.NotAllowed):                 # the effect type through "set parameter"
            zs.classify(zs.build_set_param(ID, 1, 1, 3), ID, changeable=(1,), effects={HOGI})
        for slot, effect_id in ((6, HOGI), (0, 0), (0, 1 << 32)):
            with self.assertRaises(ValueError):
                zs.build_change_effect(ID, slot, effect_id)

    def test_the_names_and_the_cursor_are_not_taken_for_an_effect(self):
        self.assertEqual(zs.classify(zs.build_name_character(ID, 1, "A"), ID), "rename_patch")
        with self.assertRaises(zs.NotAllowed):
            zs.classify(bytes.fromhex("F052006E6420006401010000000000F7".replace(" ", "")), ID,
                        changeable=(0, 1), effects={1})


class Reported(SimulatedCase):
    def test_the_effects_the_pedal_reports_are_kept(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        self.install({PRESET: [mapping(0, 2)]})
        self.run_bridge(World(chain=[STOCK, 0, CHORUS], length=1.0))
        self.assertEqual(json.loads(bridge.MEASUREMENTS.read_text(encoding="utf-8"))["effects"],
                         ["01000020", "06000020"])                # an empty place is none
        self.assertEqual(zs.Measurements(bridge.MEASUREMENTS).effects, {STOCK, CHORUS})

    def test_in_a_settings_file(self):
        config = yaml.safe_load((PROJECT / "config.example.yaml").read_text(encoding="utf-8"))
        settings = cs.build(config, {}, {}, {}, "2026-10-19 21:00:00", pedal_effects={HOGI, STOCK})
        self.assertEqual(settings["pedal_effects"], ["01000020", "09000F83"])
        self.assertEqual(zs.read_effect_ids(settings["pedal_effects"]), {STOCK, HOGI})
        self.assertNotIn("pedal_effects", cs.build(config, {}, {}, {}, "2026-10-19 21:00:00"))
        self.assertFalse([problem for problem in cs.validate(settings) if "pedal_effects" in problem])
        for wrong in (["9000F83"], ["00000000"], "01000020", [1]):
            self.assertTrue(any("pedal_effects" in problem for problem in cs.validate(dict(settings, pedal_effects=wrong))),
                            wrong)
        measurements = cs.Measurements({}, {STOCK})
        self.assertTrue(measurements.found([0, STOCK, HOGI]))
        self.assertFalse(measurements.found([HOGI]))
        self.assertEqual(measurements.effects, {STOCK, HOGI})


class Changing(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "change_effect")

    def change(self, *changes, world=None, mappings=None, reported=(HOGI, ROOM)) -> list:
        """Calls /api/effects "change" for each (slot, effect) in turn; returns the replies and the state after."""
        self.install(mappings or {PRESET: [mapping(0, 2)]})
        known = json.loads(bridge.MEASUREMENTS.read_text(encoding="utf-8"))
        known["effects"] = [f"{effect:08X}" for effect in reported]
        bridge.MEASUREMENTS.write_text(json.dumps(known), encoding="utf-8")
        seen = []

        def client(connection):
            connection.at(1.0)
            for slot, effect in changes:
                seen.append(connection.call("/api/effects", {"action": "change", "slot": slot,
                                                             "effect": f"{effect:08X}"})[:2])
            connection.at(1.8)
            seen.append(connection.call("/api/state")[1])

        world = world or World(chain=[STOCK, CHORUS], length=2.0)
        self.run_bridge(world, client)
        self.world = world
        return seen

    def test_the_effect_goes_to_the_pedal_and_is_read_back(self):
        reply, state = self.change((1, HOGI))
        self.assertEqual(reply, (200, {"result": None}))
        self.assertEqual(changes_sent(self.world), [(1, HOGI)])
        self.assertEqual(self.world.chain, [STOCK, HOGI])
        self.assertEqual([entry["name"] for entry in state["chain_effects"]], ["BlackOpt", "HOGi"])
        self.assertEqual(state["effects"]["change"], {"approved": True, "busy": False, "result": {
            "ok": True, "text": "Effect 2 is HOGi now – save the preset on the pedal to keep it"}})

    def test_the_choices_are_the_effects_the_pedal_reported_by_group(self):
        *_, state = self.change()
        self.assertEqual([(choice["group"], choice["name"]) for choice in state["effects"]["choices"]],
                         [("Dynamics", "BlackOpt"), ("Modulation", "ChorusOne"), ("SFX", "HOGi"),
                          ("Reverb", "BrghtRoom")])      # a custom effect where it is installed, as on the pedal

    def test_a_custom_effect_reported_under_two_ids_is_offered_once(self):
        """A preset made with an older build of KeyWave reported it as 07000F7C (Pitch shift), the
        table has it as 09000F7C (SFX): the pedal lists it once, so the choices do."""
        older, keywave = 0x07000F7C, 0x09000F7C
        choices = lambda *effects: [(choice["group"], choice["name"], choice["id"])
                                    for choice in bridge.effect_choices(frozenset(effects))]
        self.assertEqual(choices(older, keywave, STOCK),
                         [("Dynamics", "BlackOpt", "01000020"), ("SFX", "KeyWave", "09000F7C")])
        self.assertEqual(choices(older), [("Pitch shift", "KeyWave", "07000F7C")])   # the only id of it there is

    def test_an_effect_the_pedal_did_not_take_is_said(self):
        world = World(chain=[STOCK, CHORUS], length=2.0)
        world.takes_effects = False
        *_, state = self.change((1, HOGI), world=world)
        self.assertEqual(state["effects"]["change"]["result"], {
            "ok": False, "text": "The pedal did not take HOGi: effect 2 is ChorusOne"})

    def test_what_cannot_be_sent(self):
        replies = self.change((1, 0x0A000020), (2, HOGI), (1, CHORUS))[:-1]
        self.assertEqual(replies[0], (400, {"error": "Only an effect the pedal has (in its list of files, or "
                                                     "reported in a preset) can be chosen."}))
        self.assertEqual(replies[1], (400, {"error": "This preset has no effect 3."}))
        self.assertEqual(replies[2], (200, {"result": None}))      # there already: nothing to send
        self.assertEqual(changes_sent(self.world), [])

    def test_without_approval_nothing_is_sent(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        reply, state = self.change((1, HOGI))
        self.assertEqual(reply[0], 400)
        self.assertIn("python probe.py approve change_effect query_patch", reply[1]["error"])
        self.assertFalse(state["effects"]["change"]["approved"])
        self.assertEqual(changes_sent(self.world), [])

    def test_what_was_assigned_to_the_effect_before_waits(self):
        assigned = {PRESET: [mapping(1, 4, effect=f"{CHORUS:08X}")]}
        _, state = self.change((1, HOGI), mappings=assigned)
        self.assertEqual(state["targets"][0]["away"], "ChorusOne · Tone")
        self.assertEqual([(slot, param) for _, slot, param, _ in self.world.params if param != zs.EFFECT_PARAM], [])


if __name__ == "__main__":
    unittest.main()
