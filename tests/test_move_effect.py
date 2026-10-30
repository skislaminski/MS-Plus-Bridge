"""Moving an effect in the chain from the interface. The pedal has no message for it: the bridge
reads the patch afresh, sends it back into the edit buffer (`28 <patch, 7-bit packed>`) with the
ids, the settings blocks and the PRM2 bits of neighbouring places swapped, place by place, and
reads it again. A swap is checked against moves made on the MS-60B+ itself
(tests/pedal_patches.json, from its log)."""
import json
import unittest
from unittest import mock

import bridge
import zoom_sysex as zs
from tests.sim import (CONTROLLER_PORT, ID, KEYBOARD_PORT, PROJECT, ZOOM_PORT, SimulatedCase, World, cc, mapping,
                       note_off, note_on, patch_dump)

PRESET = "0/94"
STOCK, CHORUS, ROOM = 0x01000020, 0x06000020, 0x0B000020      # BlackOpt, ChorusOne, BrghtRoom
KEYSYNTH = 0x07000F61
SYNTH = {"keyboard": KEYBOARD_PORT, "channel": 1, "effect_id": KEYSYNTH}
EVERYTHING = lambda seconds: [ZOOM_PORT, CONTROLLER_PORT, KEYBOARD_PORT]
REAL = json.loads((PROJECT / "tests" / "pedal_patches.json").read_text(encoding="utf-8"))


def without_cursor_and_switches(patch: bytes) -> bytes:
    """The effect selected on the pedal (PRM2 10–12) and on/off are left out: a move on the pedal
    changes the one, and the other was switched between two of the patches."""
    chunks = zs.patch_chunks(patch)
    data = bytearray(patch)
    prm2, edtb = chunks[b"PRM2"][0], chunks[b"EDTB"][0]
    data[prm2 + 10:prm2 + 13] = bytes(3)
    for slot in range(int.from_bytes(patch[12:16], "little")):
        data[edtb + 24 * slot] &= 0xFE
    return bytes(data)


class Patches(unittest.TestCase):
    def test_a_swap_gives_the_patch_the_pedal_reported_after_its_own_move(self):
        for move in REAL["moves"]:
            before, after = bytes.fromhex(move["before"]), bytes.fromhex(move["after"])
            self.assertEqual(without_cursor_and_switches(zs.swap_effects(before, move["slot"])),
                             without_cursor_and_switches(after), move["when"])

    def test_the_amp_bit_goes_with_its_effect(self):
        patch = bytes.fromhex(REAL["amp"]["patch"])                  # AMPG SVT is effect 5
        self.assertIsNone(zs.check_patch(patch))
        moved = zs.swap_effects(patch, 3)
        self.assertIsNone(zs.check_patch(moved))
        prm2 = zs.patch_chunks(moved)[b"PRM2"][0]
        self.assertEqual((moved[prm2 + 20] >> 1) & 0x3F, 0b001000)  # now effect 4
        self.assertEqual(zs.swap_effects(moved, 3), patch)

    def test_what_is_not_laid_out_as_the_pedals_own_is_not_sent(self):
        patch = bytes.fromhex(REAL["amp"]["patch"])
        prm2 = zs.patch_chunks(patch)[b"PRM2"][0]
        wrong = bytearray(patch)
        wrong[prm2 + 20] = 0                                        # no amp bit for AMPG SVT
        self.assertIn("amp bits", zs.check_patch(bytes(wrong)))
        wrong = bytearray(patch)
        wrong[36:40] = (0x0B000020).to_bytes(4, "little")           # an id its settings do not name
        self.assertIn("not its own", zs.check_patch(bytes(wrong)))
        self.assertEqual(zs.check_patch(b"PTCF" + bytes(40)), "not 1 to 6 effects")
        for slot in (-1, 5, 6):
            with self.assertRaises(ValueError):
                zs.swap_effects(patch, slot)
        with self.assertRaises(ValueError):
            zs.build_patch_upload(ID, bytes(wrong))

    def test_only_the_one_patch_prepared_passes_the_allowlist(self):
        patch = zs.swap_effects(bytes.fromhex(REAL["moves"][0]["before"]), 0)
        message = zs.build_patch_upload(ID, patch)
        self.assertEqual(message[:5], bytes([0xF0, 0x52, 0x00, ID, 0x28]))
        self.assertEqual(zs.unpack_7bit(message[5:-1]), patch)
        self.assertEqual(zs.classify(message, ID, upload=message), "move_effect")
        for upload in (None, zs.build_patch_upload(ID, bytes.fromhex(REAL["moves"][0]["before"]))):
            with self.assertRaises(zs.NotAllowed):
                zs.classify(message, ID, upload=upload)


class Moving(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "move_effect")

    def move(self, *moves, world=None, mappings=None) -> list:
        """Calls /api/effects "move" for each (slot, to) in turn; returns the replies and the state after."""
        self.install(mappings or {PRESET: [mapping(0, 2)]})
        seen = []

        def client(connection):
            connection.at(1.0)
            for slot, to in moves:
                seen.append(connection.call("/api/effects", {"action": "move", "slot": slot, "to": to})[:2])
                connection.at(1.0 + 0.4 * len(seen))
            connection.at(1.0 + 0.4 * len(moves) + 0.4)
            seen.append(connection.call("/api/state")[1])

        world = world or World(chain=[STOCK, CHORUS, ROOM], length=1.0 + 0.4 * len(moves) + 0.8)
        self.run_bridge(world, client)
        self.world = world
        return seen

    def test_the_patch_goes_back_with_the_effects_swapped_and_is_read_again(self):
        reply, state = self.move((1, 2))
        self.assertEqual(reply, (200, {"result": None}))
        self.assertEqual(self.world.chain, [STOCK, ROOM, CHORUS])
        self.assertEqual(len(self.world.uploads), 1)
        sent = self.world.uploads[0]
        before = zs.unpack_7bit(patch_dump([(STOCK, 0), (CHORUS, 0), (ROOM, 0)])[9:-6])
        self.assertEqual(sent, zs.swap_effects(before, 1))
        self.assertEqual([entry["name"] for entry in state["chain_effects"]], ["BlackOpt", "BrghtRoom", "ChorusOne"])
        self.assertEqual(state["effects"]["change"]["result"], {
            "ok": True, "text": "ChorusOne is effect 3 now – save the preset on the pedal to keep it"})

    def test_over_several_places_those_in_between_move_up(self):
        _, state = self.move((0, 2))
        self.assertEqual(self.world.chain, [CHORUS, ROOM, STOCK])
        before = zs.unpack_7bit(patch_dump([(STOCK, 0), (CHORUS, 0), (ROOM, 0)])[9:-6])
        self.assertEqual(self.world.uploads, [zs.swap_effects(zs.swap_effects(before, 0), 1)])
        self.assertEqual(state["effects"]["change"]["result"]["text"],
                         "BlackOpt is effect 3 now – save the preset on the pedal to keep it")

    def test_an_empty_place_moves_too(self):
        world = World(chain=[STOCK, 0, ROOM], length=2.2)
        _, state = self.move((1, 2), world=world)
        self.assertEqual(world.chain, [STOCK, ROOM, 0])
        self.assertEqual(state["effects"]["change"]["result"]["text"],
                         "An empty place is effect 3 now – save the preset on the pedal to keep it")

    def test_what_is_assigned_goes_along(self):
        assigned = {PRESET: [mapping(1, 4, effect=f"{CHORUS:08X}")]}
        *_, state = self.move((1, 2), mappings=assigned)
        self.assertIsNone(state["targets"][0]["away"])
        self.assertEqual(state["mappings"][0]["targets"][0]["slot"], 2)

    def test_a_move_the_pedal_did_not_take_is_said(self):
        world = World(chain=[STOCK, CHORUS, ROOM], length=2.2)
        world.takes_patches = False
        *_, state = self.move((0, 1), world=world)
        self.assertEqual(state["effects"]["change"]["result"], {
            "ok": False, "text": "The pedal did not take the move: BlackOpt is effect 1"})

    def test_what_cannot_be_moved(self):
        replies = self.move((0, -1), (2, 3), (1, 1))[:-1]
        self.assertEqual([status for status, _ in replies], [400, 400, 400])
        self.assertEqual(replies[0][1]["error"], "Effect 1 cannot move to place 0.")
        self.assertEqual(self.world.uploads, [])

    def test_without_approval_nothing_is_sent(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        reply, state = self.move((0, 1))
        self.assertIn("python probe.py approve move_effect query_patch", reply[1]["error"])
        self.assertFalse(state["effects"]["move"]["approved"])
        self.assertEqual(self.world.uploads, [])



class WhileMoving(SimulatedCase):
    """What is on its way while an effect moves: nothing is repeated into a place that may hold
    another effect by then, and a key released meanwhile ends its note once the chain is known."""
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "move_effect")
        patcher = mock.patch.object(bridge, "SYNTH_QUERY_SECONDS", 0.2)
        patcher.start()
        self.addCleanup(patcher.stop)

    def move(self, world: World, at: float, slot: int, to: int, mappings=None, synth=None) -> float:
        """Moves effect slot to place to at that time; returns when the patch went to the pedal."""
        self.install(mappings, synth=synth)

        def client(connection):
            connection.at(at)
            connection.call("/api/effects", {"action": "move", "slot": slot, "to": to})

        self.run_bridge(world, client)
        return next(seconds for seconds, text in world.sent if text.startswith("F0 52 00 6E 28"))

    def test_an_unconfirmed_value_is_not_repeated_into_the_place_its_effect_left(self):
        world = World([(1.0, cc(64))], chain=[STOCK, CHORUS, ROOM], length=2.0)
        world.accept = lambda slot, param, value: (slot, param) != (0, 2)      # no ack: it would go again
        sent = self.move(world, 1.1, 0, 1, {PRESET: [mapping(0, 2, effect=f"{STOCK:08X}")]})
        self.assertEqual(world.chain, [CHORUS, STOCK, ROOM])
        self.assertEqual([place for seconds, *place, _ in world.params if seconds > sent and place == [0, 2]], [])

    def test_an_unconfirmed_note_is_not_repeated_into_the_place_the_synth_left(self):
        world = World([(1.0, note_on(40))], ports=EVERYTHING, chain=[KEYSYNTH, STOCK, CHORUS], length=2.0)
        world.accept = lambda slot, param, value: (slot, param, value) != (0, 2, zs.key_for(40))
        sent = self.move(world, 1.015, 0, 1, synth=SYNTH)
        self.assertEqual(world.chain, [STOCK, KEYSYNTH, CHORUS])
        after = [tuple(place) for seconds, *place in world.params if seconds > sent]
        self.assertNotIn((0, 2, zs.key_for(40)), after)
        self.assertIn((1, 2, zs.key_for(40)), after)        # the note, where the synth is now

    def test_a_key_released_while_the_pedal_does_not_report_the_move_ends_its_note_later(self):
        def answer(on: bool):
            return lambda world: setattr(world, "patches", on)

        script = [(1.0, note_on(40)), (1.15, answer(False)), (1.2, note_off(40)), (2.0, answer(True))]
        world = World(script, ports=EVERYTHING, chain=[KEYSYNTH, STOCK, CHORUS], length=4.5)
        self.move(world, 1.1, 1, 2, synth=SYNTH)
        closed = [seconds for seconds, *place in world.params if place == [0, 2, 0]]
        self.assertLess(closed[0], 4.0)     # once the patch came (3.4 s), not only as the bridge stopped

    def test_the_bridge_stopping_while_an_effect_moves_ends_the_note(self):
        world = World([(1.0, note_on(40))], ports=EVERYTHING, chain=[KEYSYNTH, STOCK, CHORUS], length=1.25)
        self.move(world, 1.1, 1, 2, synth=SYNTH)             # the synth keeps its place
        self.assertEqual(world.values[(0, 2)], 0)


if __name__ == "__main__":
    unittest.main()
