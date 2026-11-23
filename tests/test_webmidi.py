"""The bridge in a phone's browser (webmidi/): the page loads every file of the program the bridge
needs. And the MIDI check (webmidi/check.html): its SysEx byte for byte as zoom_sysex.py's, its
allowlist refusing whatever the Python one refuses, its way to the pedal against the simulated
MS-60B+; run in Node, skipped where there is none. The bridge itself in Pyodide is tried by hand:
webmidi/sim_server.py, then http://127.0.0.1:8790/webmidi/?sim in Chrome."""
import dataclasses
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import effect_names
import ui
import zoom_sysex as zs
from tests import sim
from webmidi import sim_server

NODE = shutil.which("node")
WEBMIDI = Path(__file__).resolve().parent.parent / "webmidi"
ID = sim.ID
TARGETS = [zs.ParamTarget(slot=0, param=4, min=0, max=100), zs.ParamTarget(slot=2, param=3, min=10, max=150)]
JS_KINDS = ("identity_request", "edit_enable", "edit_disable", "set_param", "query_program", "query_patch")


class Files(unittest.TestCase):
    def test_the_page_loads_every_module_and_file_the_bridge_needs(self):
        root = WEBMIDI.parent
        loaded = set(json.loads((WEBMIDI / "files.json").read_text(encoding="utf-8")))
        # what importing them alone loads of the program, as webmidi/browser.py does (not what other tests loaded)
        used = set(json.loads(subprocess.run(
            [sys.executable, "-c", "import json, sys, pathlib, bridge, probe, ui; print(json.dumps(["
             "pathlib.Path(m.__file__).name for m in list(sys.modules.values()) if getattr(m, '__file__', None)"
             " and pathlib.Path(m.__file__).resolve().parent == pathlib.Path.cwd()]))"],
            cwd=root, capture_output=True, text=True, check=True).stdout))
        self.assertEqual(set(), used - loaded)
        for name in loaded | {"mido_webmidi.py", "browser.py"}:
            with self.subTest(name):
                self.assertTrue((root / name).is_file() or (WEBMIDI / name).is_file())
        self.assertIn(effect_names.TABLE.name, loaded)
        self.assertIn(ui.PAGE.name, loaded)


def node(script: str, data=None):
    """Runs an ES module in Node with zs and data (JSON on stdin) at hand; what it prints, as JSON."""
    head = (f'import * as zs from "{(WEBMIDI / "zoom_sysex.js").as_uri()}";\n'
            'import {readFileSync} from "node:fs";\nconst data = JSON.parse(readFileSync(0, "utf8"));\n')
    run = subprocess.run([NODE, "--input-type=module", "-e", head + script], input=json.dumps(data),
                         capture_output=True, text=True, timeout=60)
    if run.returncode:
        raise AssertionError(run.stderr)
    return json.loads(run.stdout)


def python(function, *args):
    try:
        return list(function(*args))
    except ValueError:
        return "error"


@unittest.skipUnless(NODE, "Node.js is not installed")
class SameBytes(unittest.TestCase):
    def test_the_builders_make_the_same_bytes(self):
        cases = [("buildIdentityRequest", zs.build_identity_request, []),
                 ("buildEditEnable", zs.build_edit_enable, [ID]), ("buildEditDisable", zs.build_edit_disable, [ID]),
                 ("buildQueryProgram", zs.build_query_program, [ID]), ("buildQueryPatch", zs.build_query_patch, [ID])]
        cases += [("buildSetParam", zs.build_set_param, [ID, slot, param, value])
                  for slot, param, value in ((0, 2, 0), (5, 13, 127), (1, 4, 128), (2, 3, 1000), (3, 2, 4095),
                                             (0, 2, 0x3FFF), (0, 2, 0x4000), (0, 2, -1))]
        made = node('console.log(JSON.stringify(data.map(([name, args]) => {'
                    ' try { return Array.from(zs[name](...args)); } catch { return "error"; } })));',
                    [[name, args] for name, _, args in cases])
        for (name, function, args), bytes_ in zip(cases, made):
            with self.subTest(name, args=args):
                self.assertEqual(python(function, *args), bytes_)

    def test_the_allowlist_refuses_what_the_python_one_refuses(self):
        messages = [zs.build_identity_request(), zs.build_edit_enable(ID), zs.build_edit_disable(ID),
                    zs.build_query_program(ID), zs.build_query_patch(ID),
                    zs.build_set_param(ID, 0, 4, 100), zs.build_set_param(ID, 0, 4, 101),     # beyond its range
                    zs.build_set_param(ID, 2, 3, 9), zs.build_set_param(ID, 2, 3, 150),
                    zs.build_set_param(ID, 1, 4, 50),                                       # not measured
                    zs.build_switch_effect(ID, 0, False), zs.build_change_effect(ID, 0, 0x01000010),
                    zs.build_set_param(ID, 0, 1, 5), zs.build_name_character(ID, 0, "A"),
                    zs.build_query_preset(ID, 0, 1), zs.build_file_status(ID), zs.build_edit_enable(0x6B),
                    bytes.fromhex("F0 52 00 6E 28 00 F7"), bytes.fromhex("F0 52 00 6E 01 F7"),
                    bytes.fromhex("F0 52 00 6E 04 F7"), bytes.fromhex("F0 52 00 6E 5B F7"),
                    bytes.fromhex("F0 52 00 6E 64 47 F7"), bytes.fromhex("F0 52 00 6E 32 F7"),
                    bytes.fromhex("F0 52 00 6E 64 20 00 64 02 00 00 00 00 00 F7"),
                    bytes.fromhex("F0 52 00 6E 64 20 00 5F 02 41 00 00 00 00 F7"),
                    bytes.fromhex("F0 52 00 6E 80 F7"), bytes.fromhex("52 00 6E 50 F7"), b"\xF0"]

        def kind(message, device_id):
            try:
                return zs.classify(message, device_id, TARGETS)
            except zs.NotAllowed:
                return "refused"

        cases = [(list(message), device_id) for message in messages for device_id in (ID, None)]
        found = node('const targets = data.targets; console.log(JSON.stringify(data.cases.map(([msg, id]) => {'
                     ' try { return zs.classify(msg, id, targets); } catch (e) {'
                     ' if (!(e instanceof zs.NotAllowed)) throw e; return "refused"; } })));',
                     {"targets": [dataclasses.asdict(t) for t in TARGETS], "cases": cases})
        for (message, device_id), js in zip(cases, found):
            with self.subTest(zs.to_hex(message), device_id=device_id):
                py = kind(bytes(message), device_id)
                self.assertEqual(py if py in JS_KINDS else "refused", js)

    def test_the_parsers_read_the_same(self):
        effect_names.use_cache(None)
        dump = sim.patch_dump([(0x01000010, (6, 1, 70, 80)), (0x08000F71, (1000, 4095, 0, 255, 7)),
                               (0x01000030, 3)], name="Bass Rig", off=[1])
        broken = dump[:-3] + bytes([dump[-3] ^ 1]) + dump[-2:]
        found = node('const table = JSON.parse(readFileSync(data.table, "utf8"));'
                     'console.log(JSON.stringify({identity: zs.parseIdentityReply(data.identity),'
                     ' dumps: data.dumps.map((raw) => { const d = zs.parsePatchDump(zs.zoomBody(raw, data.id));'
                     '   return d && {name: d.name, effects: d.effects, size: d.data.length}; }),'
                     ' knobs: data.ids.map((id) => zs.knobs(table, id)),'
                     ' names: data.ids.map((id) => (zs.effect(table, id) || {}).name || null)}));',
                     {"identity": list(sim.IDENTITY_REPLY), "dumps": [list(dump), list(broken)], "id": ID,
                      "table": str(effect_names.TABLE), "ids": [0x01000010, 0x02000060, 0x08000F71, 0x00000FF0, 0]})
        identity = zs.parse_identity_reply(sim.IDENTITY_REPLY)
        self.assertEqual({"deviceId": identity.device_id, "family": list(identity.family),
                          "model": list(identity.model), "version": identity.version}, found["identity"])
        parsed = zs.parse_patch_dump(zs.zoom_body(dump, ID))
        self.assertEqual({"name": parsed.name, "size": len(parsed.data),
                          "effects": [{"id": e.id, "enabled": e.enabled, "firstParam": e.first_param,
                                       "values": list(e.values)} for e in parsed.effects]}, found["dumps"][0])
        self.assertIsNone(zs.parse_patch_dump(zs.zoom_body(broken, ID)))
        self.assertIsNone(found["dumps"][1])
        for effect_id, knobs, name in zip([0x01000010, 0x02000060, 0x08000F71, 0x00000FF0, 0], found["knobs"],
                                          found["names"]):
            with self.subTest(f"{effect_id:08X}"):
                self.assertEqual(effect_names.knobs(effect_id), knobs)
                self.assertEqual((effect_names.effect(effect_id) or {}).get("name"), name)


@unittest.skipUnless(NODE, "Node.js is not installed")
class SimulatedPedal(unittest.TestCase):
    """webmidi/pedal.js through the MIDIAccess of webmidi/sim_midi.js, the way the page uses them."""

    def test_it_reads_the_patch_and_sets_one_knob(self):
        pedal = sim_server.world()
        server = sim_server.serve(pedal, 0)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        found = node(f'import {{Pedal}} from "{(WEBMIDI / "pedal.js").as_uri()}";\n'
                     f'import {{simulatedAccess}} from "{(WEBMIDI / "sim_midi.js").as_uri()}";\n'
                     'const access = simulatedAccess(data.base), table = JSON.parse(readFileSync(data.table, "utf8"));\n'
                     'const pedal = new Pedal(access, {backupConfirmed: true, isApproved: () => true}, table);\n'
                     'await pedal.start();\n'
                     'const set = await pedal.setKnob(0, 4, 42), again = await pedal.setKnob(0, 4, 42);\n'
                     'const refused = [];\n'
                     'for (const attempt of [() => pedal.setKnob(0, 4, 101), () => pedal.setKnob(0, 1, 5),\n'
                     '                       () => pedal.send(zs.buildSetParam(pedal.deviceId, 0, 0, 0))])\n'
                     '  try { await attempt(); refused.push(null); } catch (e) { refused.push(e.message); }\n'
                     'await pedal.close();\nawait access.stop();\n'
                     'console.log(JSON.stringify({set, again, refused, preset: pedal.preset, name: pedal.patch.name,'
                     ' ids: pedal.patch.effects.map((e) => e.id), values: pedal.patch.effects[0].values.slice(0, 4)}));\n'
                     'process.exit(0);',
                     {"base": f"http://127.0.0.1:{server.server_address[1]}", "table": str(effect_names.TABLE)})
        self.assertEqual([zs.to_hex(message) for message in (
            zs.build_identity_request(), zs.build_edit_enable(ID), zs.build_query_program(ID),
            zs.build_query_patch(ID), zs.build_set_param(ID, 0, 4, 42), zs.build_set_param(ID, 0, 4, 42),
            zs.build_edit_disable(ID))], [sent for _, sent in pedal.sent])
        self.assertTrue(found["set"])                    # confirmed …
        self.assertFalse(found["again"])                 # … and the second time not: it held that value already
        self.assertEqual(42, pedal.values[(0, 4)])
        self.assertEqual([6, 0, 42, 80], found["values"])
        self.assertEqual({"bank": 0, "program": 94}, found["preset"])
        self.assertEqual(("USER-001", list(sim_server.CHAIN)), (found["name"], found["ids"]))
        self.assertEqual(["Tone goes from 0 to 100", "effect 1 has no knob 0 that is known",
                          "param 0 is blocked (effect on/off or effect type)"], found["refused"])
        self.assertFalse(pedal.edit_mode)


if __name__ == "__main__":
    unittest.main()
