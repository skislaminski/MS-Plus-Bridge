"""The bridge's interface in the browser: status, learning parameters, assignments per preset,
export and import, which controller of the keyboard sets which knob of the synth effect, the
arpeggiator, the step sequencer, the switches of the effects, and which MIDI ports the devices are.

Runs as a small HTTP server in the same process as the bridge and is reachable from this
computer only; with listen=True (a Raspberry Pi without screen, or a computer whose `ui:` says
`listen: true`) from the network as well, for a phone or tablet in the same WLAN. The interface itself sends nothing to the pedal; it changes the
assignments and asks the bridge to switch an effect.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
import urllib.request
from urllib.parse import parse_qs, urlsplit

import effect_names
try:
    import effect_pictures
except ImportError:    # a card written before the pictures went along: the names stand in
    effect_pictures = None

PAGE = Path(__file__).resolve().parent / "ui.html"
DEFAULT_PORT = 8765
MAX_BODY = 1_000_000
READ_PEDAL_SECONDS = 60.0     # how long Read Pedal may take: the list, and up to 20 effect files of about a second each
EFFECT_ID = re.compile(r"[0-9A-Fa-f]{8}")    # an effect by its id, as the state gives it
# the pictures of the effects: from the installer's data folder (bridge.INSTALLER_DATA; bridge.py runs as
# __main__), on the Pi from the folder export.py wrote onto its card beside the program
INSTALLER_DATA = Path(os.path.expanduser("~")) / "Zoom MS Installer"
PICTURES = (effect_pictures.Pictures(INSTALLER_DATA if INSTALLER_DATA.is_dir() else Path(__file__).resolve().parent / "pictures")
            if effect_pictures else None)


def reachable_name(host: str) -> bool:
    """Over the network: whether the page was asked for under a name of this computer (its
    address, its name, or its name in the local network, .local), not under a name that only
    points here (a website that rebinds its own name to this address must not get in)."""
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 or host.startswith("[") else host
    name = name.strip("[]").lower().rstrip(".")
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    own = socket.gethostname().lower().removesuffix(".local")
    return name in ("localhost", own, f"{own}.local")


def network_address() -> Optional[str]:
    """This computer's address in its local network (the one a phone in the same WLAN reaches it
    under), None if it is in none. Nothing is sent: a UDP socket only picks the way out."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))           # TEST-NET-1: never reached
            address = probe.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") or address == "0.0.0.0" else address


def running_bridge(port: int) -> Optional[dict]:
    """The state of a bridge whose interface already answers on this computer at that port, None
    if none does (the port is free, or something else has it)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as response:
            state = json.loads(response.read())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and "status" in state else None


def _json(status: int, data) -> tuple:
    return status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8", "", "no-store"


def answer(bridge, method: str, target: str, body: bytes = b"", here: bool = False,
           workshop: Optional[dict] = None) -> tuple:
    """A request to the interface → (status, body, content type, file name to save it under or "",
    Cache-Control). For the server below and, in a phone's browser (webmidi/), for the page itself.
    here: asked from this computer itself (or the phone that runs the bridge), not over the network."""
    url = urlsplit(target)
    if method == "GET":
        if url.path == "/api/workshop" and workshop and here:
            return _json(200, {"pages": workshop})
        if url.path == "/":
            return 200, PAGE.read_bytes(), "text/html; charset=utf-8", "", "no-store"
        if url.path == "/api/state":
            return _json(200, bridge.state())
        if url.path == "/api/export":
            return _export(bridge, parse_qs(url.query).get("key", [None])[0])
        if url.path == "/api/labels":
            return _labels(bridge, parse_qs(url.query).get("id", [""])[0])
        if url.path == "/api/picture":
            return _picture(bridge, parse_qs(url.query).get("id", [""])[0])
        if url.path == "/api/settings-file":
            reply = bridge.call(lambda: bridge.settings_file())
            if "error" in reply:
                return _json(400, reply)
            # under the name the bridge on the Pi looks for
            return 200, reply["result"].encode("utf-8"), "text/yaml; charset=utf-8", "expression-bridge.yaml", "no-store"
        return _json(404, {"error": "Not found."})
    try:
        data = json.loads(body or b"{}")
        actions = {
            "/api/learn": lambda: bridge.learn(str(data.get("action"))),
            "/api/mapping": lambda: bridge.save_mapping(str(data.get("key")), data),
            "/api/delete": lambda: bridge.delete_mapping(str(data.get("key"))),
            "/api/import": lambda: bridge.import_data(data),
            "/api/synth": lambda: bridge.synth_control(str(data.get("action")), data.get("knob")),
            "/api/arp": lambda: bridge.arp(data),
            "/api/seq": lambda: bridge.seq(data),
            "/api/devices": lambda: bridge.devices(data),
            "/api/effects": lambda: bridge.effects(data),
            "/api/scenes": lambda: bridge.scene(data),
            "/api/pedal_expression": lambda: bridge.pedal_expression(data),
            "/api/parameter": lambda: bridge.parameter(data),
            "/api/rename": lambda: bridge.rename_preset(data),
            "/api/preset": lambda: bridge.preset(data),
            "/api/pedal": lambda: bridge.pedal(str(data.get("action"))),
        }
        if url.path not in actions or not isinstance(data, dict):
            return _json(404, {"error": "Not found."})
        # a phone must not take the pedal back while a program on the computer writes to it
        if url.path == "/api/pedal" and not here:
            return _json(403, {"error": "The pedal is released and taken back on the computer itself."})
        # reading the pedal takes longer when it reads effect files the table does not describe
        reading = url.path == "/api/effects" and data.get("action") == "read_pedal"
        reply = bridge.call(actions[url.path], READ_PEDAL_SECONDS if reading else 3.0)
        return _json(400 if "error" in reply else 200, reply)
    except ValueError:
        return _json(400, {"error": "Invalid request."})


def _export(bridge, key) -> tuple:
    """All assignments, or those of one preset, as a file to download."""
    reply = bridge.call(lambda: bridge.export_data(key))
    if "error" in reply:
        return _json(400, reply)
    presets = reply["result"]["presets"]
    name = "all-presets" if key is None else f"preset-{next(iter(presets.values()))['display']}"
    body = json.dumps(reply["result"], indent=2, ensure_ascii=False).encode("utf-8")
    return 200, body, "application/json; charset=utf-8", f"expression-bridge-{name}.json", "no-store"


def _labels(bridge, effect_id: str) -> tuple:
    """The texts the pedal shows for the values of an effect's knobs ({"labels": {param:
    [text per value]}}), by the effect's id. Asked for again when the state's
    effects.labels_version changes; an answer that is no 200 is to be asked for later."""
    if not EFFECT_ID.fullmatch(effect_id):
        return _json(400, {"error": "An effect is given by its id: eight hex digits."})
    reply = bridge.call(lambda: bridge.value_labels(int(effect_id, 16)))
    if "error" in reply:
        return _json(400, reply)
    return _json(200, {"labels": reply["result"]})


def _picture(bridge, effect_id: str) -> tuple:
    """The picture the pedal shows for an effect, as PNG, by the effect's id; 404 if there is
    none (an effect without a file, like BPM, one in no backup of the installer, or the Pi)."""
    if not EFFECT_ID.fullmatch(effect_id):
        return _json(400, {"error": "An effect is given by its id: eight hex digits."})
    if PICTURES is None:                       # the Pi: no installer, no pictures
        return _json(404, {"error": "No picture."})
    # the table changes in the bridge's own loop; the picture is read here, outside of it
    reply = bridge.call(lambda: (effect_names.effect(int(effect_id, 16)) or {}).get("file"))
    if "error" in reply:                       # busy (Read Pedal) or not running: ask again later
        return _json(503, reply)
    file = reply.get("result")
    picture = PICTURES.picture(file) if file else None
    if picture is None:
        return _json(404, {"error": "No picture."})
    return 200, picture, "image/png", "", "max-age=3600"


def serve(bridge, settings: dict, listen: bool = False, workshop: Optional[dict] = None) -> str:
    """Starts the server in a background thread and returns its address. listen: reachable from
    every network the computer is in, not only from itself. workshop: the pages of the installer in
    this program and their secret addresses (workshop.Workshop.pages), handed only to this computer
    itself."""
    port = int(settings.get("port", DEFAULT_PORT))
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def _reply(self, status: int, body: bytes, content_type: str, filename: str = "",
                   cache: str = "no-store") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")   # no other site frames it
            self.end_headers()
            self.wfile.write(body)

        def _local(self) -> bool:
            """Accept requests from our own page only, not from other websites. Over the network the
            page is reached under any name or address, so it must come from the same one."""
            origin, host = self.headers.get("Origin"), self.headers.get("Host")
            if listen:
                return bool(host) and reachable_name(host) and (origin is None or origin.split("://")[-1] == host)
            return host in allowed and (origin is None or origin.split("://")[-1] in allowed)

        def _from_here(self) -> bool:
            """Asked from this computer itself, not from a phone in the WLAN (--listen)."""
            return self.client_address[0] in ("127.0.0.1", "::1")

        def do_GET(self) -> None:
            if not self._local():
                self._reply(*_json(403, {"error": "Only reachable locally."}))
            else:
                self._reply(*answer(bridge, "GET", self.path, here=self._from_here(), workshop=workshop))

        def do_POST(self) -> None:
            if not self._local():
                self._reply(*_json(403, {"error": "Only reachable locally."}))
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self._reply(*_json(400, {"error": "Invalid request."}))
                return
            if not 0 <= length <= MAX_BODY:          # -1 would read until the sender stops
                self._reply(*_json(413, {"error": "Too large."}))
                return
            self._reply(*answer(bridge, "POST", self.path, self.rfile.read(length), here=self._from_here()))

    server = ThreadingHTTPServer(("0.0.0.0" if listen else "127.0.0.1", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if listen:
        name = socket.gethostname().removesuffix(".local")
        return f"http://{name}.local" + ("" if port == 80 else f":{port}")
    return f"http://127.0.0.1:{port}"
