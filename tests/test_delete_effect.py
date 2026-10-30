"""Deleting an effect (or an empty place) from the chain from the interface. As for moving and
adding there is no message for it: the patch is read afresh, sent back with the effect taken out
(zs.remove_effect: its id, its 24 bytes of settings, the length, the PRM2 bits of the places after
it, the effect selected on the pedal; checked against effects deleted on the MS-60B+ itself,
tests/pedal_patches.json), and read again."""
import json
import unittest

import bridge
import zoom_sysex as zs
from tests.sim import ID, PROJECT, SimulatedCase, World, mapping, note_on, patch_dump
from tests.test_editor import KEYSYNTH, SYNTH, K, sent
from tests.test_synth import EVERYTHING

PRESET = "0/94"
STOCK, CHORUS, ROOM = 0x01000020, 0x06000020, 0x0B000020      # BlackOpt, ChorusOne, BrghtRoom
REAL = json.loads((PROJECT / "tests" / "pedal_patches.json").read_text(encoding="utf-8"))


def selected(patch: bytes) -> list:
    """The places PRM2 bytes 10–12 name: the effect selected on the pedal and those after it."""
    return zs._selected_places(patch, zs.patch_chunks(patch)[b"PRM2"][0])


def within_patch(patch: bytes, left_out=()) -> bytes:
    """What a patch holds, up to its own length, without PRM2 10–12 and the bytes left_out."""
    data = bytearray(patch[:int.from_bytes(patch[4:8], "little")])
    prm2 = zs.patch_chunks(bytes(data))[b"PRM2"][0]
    for index in [prm2 + 10, prm2 + 11, prm2 + 12, *left_out]:
        data[index] = 0
    return bytes(data)


def with_selected(patch: bytes, places) -> bytes:
    data = bytearray(patch)
    prm2 = zs.patch_chunks(patch)[b"PRM2"][0]
    word = 12 | sum(place << 5 + 4 * index for index, place in enumerate(places))
    data[prm2 + 10:prm2 + 13] = word.to_bytes(3, "little")
    return bytes(data)


class Patches(unittest.TestCase):
    def test_the_patch_the_pedal_reported_after_its_own_delete(self):
        for deleted in REAL["deletes"]:
            before, after = bytes.fromhex(deleted["before"]), bytes.fromhex(deleted["after"])
            made = zs.remove_effect(before, deleted["slot"])
            self.assertEqual(len(made), len(before))                       # the buffer keeps its size
            self.assertEqual(within_patch(made, deleted.get("turned", ())),
                             within_patch(after, deleted.get("turned", ())), deleted["when"])
            self.assertEqual(selected(made)[0], selected(after)[0], deleted["when"])

    def test_the_effect_selected_on_the_pedal(self):
        six = bytes.fromhex(REAL["amp"]["patch"])
        for places, slot, wanted in (([3, 4, 5], 5, [3, 4]),               # one after it: as it was
                                     ([3, 4, 5], 1, [2, 3, 4]),            # one before it: it moves up
                                     ([3, 4, 5], 3, [3, 4]),               # itself: the one after it
                                     ([5], 5, [4])):                       # the last itself: the new last
            self.assertEqual(selected(zs.remove_effect(with_selected(six, places), slot)), wanted, (places, slot))

    def test_the_bits_of_the_places_after_it_move_down(self):
        patch = bytes.fromhex(REAL["amp"]["patch"])                        # AMPG SVT is effect 5
        prm2 = zs.patch_chunks(patch)[b"PRM2"][0]
        moved = zs.remove_effect(patch, 1)
        self.assertEqual((moved[prm2 - 28 + 20] >> 1) & 0x3F, 0b001000)   # now effect 4
        gone = zs.remove_effect(patch, 4)
        self.assertEqual((gone[prm2 - 28 + 20] >> 1) & 0x3F, 0)
        self.assertIsNone(zs.check_patch(gone))

    def test_an_added_place_taken_out_again(self):
        for added in REAL["adds"]:
            before = bytes.fromhex(added["before"])
            again = zs.remove_effect(zs.insert_empty(before, added["slot"]), added["slot"])
            self.assertEqual(within_patch(again), within_patch(before))

    def test_not_the_only_effect_or_outside_the_chain(self):
        one = zs.parse_patch_dump(zs.zoom_body(patch_dump([(STOCK, 0)]), ID)).data
        with self.assertRaises(ValueError):
            zs.remove_effect(one, 0)
        patch = bytes.fromhex(REAL["amp"]["patch"])
        for slot in (-1, 6):
            with self.assertRaises(ValueError):
                zs.remove_effect(patch, slot)

    def test_only_the_prepared_patch_as_its_own_kind(self):
        message = zs.build_patch_upload(ID, zs.remove_effect(bytes.fromhex(REAL["amp"]["patch"]), 0))
        self.assertEqual(zs.classify(message, ID, upload=message, upload_kind="delete_effect"), "delete_effect")
        with self.assertRaises(zs.NotAllowed):
            zs.classify(message, ID)


class DeleteCase(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "delete_effect")

    def delete(self, *calls, world=None, mappings=None, synth=None, until=1.6) -> list:
        """Calls /api/effects "delete" for each (slot, the effect meant there) in turn."""
        self.install(mappings or {PRESET: [mapping(0, 2)]}, synth=synth)
        seen = []

        def client(connection):
            connection.at(1.0)
            for slot, effect in calls:
                seen.append(connection.call("/api/effects", {"action": "delete", "slot": slot,
                                                             "effect": f"{effect:08X}"})[:2])
            connection.at(until)
            seen.append(connection.call("/api/state")[1])

        world = world or World(chain=[STOCK, CHORUS, ROOM], length=until + 0.3)
        self.run_bridge(world, client)
        self.world = world
        return seen


class Deleting(DeleteCase):
    def test_the_patch_goes_back_without_the_effect_and_is_read_again(self):
        reply, state = self.delete((0, STOCK))
        self.assertEqual(reply, (200, {"result": None}))
        self.assertEqual(self.world.chain, [CHORUS, ROOM])
        before = zs.unpack_7bit(patch_dump([(STOCK, 0), (CHORUS, 0), (ROOM, 0)])[9:-6])
        self.assertEqual(self.world.uploads, [zs.remove_effect(before, 0)])
        self.assertEqual([entry["name"] for entry in state["chain_effects"]], ["ChorusOne", "BrghtRoom"])
        self.assertEqual(state["effects"]["change"]["result"], {
            "ok": True, "text": "Effect 1 (BlackOpt) is deleted – save the preset on the pedal to keep it"})
        self.assertTrue(state["effects"]["delete"]["approved"])

    def test_an_empty_place_too(self):
        *_, state = self.delete((1, 0), world=World(chain=[STOCK, 0, ROOM], length=1.9))
        self.assertEqual(self.world.chain, [STOCK, ROOM])
        self.assertEqual(state["effects"]["change"]["result"]["text"],
                         "Effect 2 (an empty place) is deleted – save the preset on the pedal to keep it")

    def test_what_is_assigned_goes_along_and_that_of_the_effect_deleted_stops(self):
        *_, state = self.delete((0, STOCK), mappings={PRESET: [mapping(1, 4, effect=f"{CHORUS:08X}")]})
        self.assertIsNone(state["targets"][0]["away"])
        self.assertEqual(state["mappings"][0]["targets"][0]["slot"], 0)
        *_, state = self.delete((0, STOCK), mappings={PRESET: [mapping(0, 2, effect=f"{STOCK:08X}")]})
        self.assertEqual(state["targets"][0]["away"], "BlackOpt · Comp")

    def test_scene_entries_that_make_way_are_said(self):
        # a scene holds one effect per place: ChorusOne's entries move into BlackOpt's place
        scene = {"name": "S", "cc": 30, "values": [{"slot": 0, "param": 3, "value": 10},
                                                   {"slot": 1, "param": 4, "value": 20}],
                 "effects": {}, "chain": {"1": f"{STOCK:08X}", "2": f"{CHORUS:08X}"}}   # places from 1
        bridge.CONTROLS.write_text(json.dumps({"controls": {}, "scenes": {PRESET: [scene]}}), encoding="utf-8")
        *_, state = self.delete((0, STOCK))
        self.assertIn("scene “S” holds nothing for BlackOpt any longer – another effect took its place", self.output)
        self.assertEqual([(value["slot"], value["param"]) for value in state["scenes"]["list"][0]["values"]], [(0, 4)])

    def test_a_delete_the_pedal_did_not_take_is_said(self):
        world = World(chain=[STOCK, CHORUS, ROOM], length=2.2)
        world.takes_patches = False
        *_, state = self.delete((0, STOCK), world=world, until=1.9)
        self.assertEqual(state["effects"]["change"]["result"], {
            "ok": False, "text": "The pedal did not delete effect 1 (BlackOpt)"})

    def test_what_cannot_be_deleted(self):
        replies = self.delete((3, ROOM), (1, ROOM), world=World(chain=[STOCK, CHORUS, ROOM], length=1.9))[:-1]
        self.assertEqual(replies, [(400, {"error": "This preset has no effect 4."}),
                                   (400, {"error": "Effect 2 is ChorusOne now: nothing was deleted."})])
        replies = self.delete((0, STOCK), world=World(chain=[STOCK], length=1.9))[:-1]
        self.assertEqual(replies[0][1]["error"], "A preset keeps at least one effect: choose another one for it instead.")
        self.assertEqual(self.world.uploads, [])

    def test_without_approval_nothing_is_sent(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        reply, state = self.delete((0, STOCK))
        self.assertIn("python probe.py approve delete_effect query_patch", reply[1]["error"])
        self.assertFalse(state["effects"]["delete"]["approved"])
        self.assertEqual(self.world.uploads, [])


class SynthDeleted(DeleteCase):
    def played(self, chain, slot) -> World:
        world = World([(0.9, note_on(45)), (1.1, note_on(50))], ports=EVERYTHING, chain=chain, length=2.0)
        self.delete((slot, chain[slot]), world=world, synth=SYNTH, until=1.8)
        return world

    def test_notes_go_to_the_synth_where_it_is_now(self):
        world = self.played([ROOM, KEYSYNTH], 0)
        self.assertEqual(world.chain, [KEYSYNTH])
        self.assertEqual(sent(world), [(1, 2, K(45)), (0, 2, K(50)), (0, 2, 0)])   # the last: the bridge stops

    def test_once_the_synth_is_deleted_no_note_reaches_the_effect_in_its_place(self):
        world = self.played([KEYSYNTH, ROOM], 0)
        self.assertEqual(world.chain, [ROOM])
        self.assertEqual(sent(world), [(0, 2, K(45))])                     # nothing to BrghtRoom's PRE DLY


if __name__ == "__main__":
    unittest.main()
