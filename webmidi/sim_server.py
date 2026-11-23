"""Serves the Web MIDI prototype (webmidi/) with the simulated MS-60B+ of tests/sim.py behind
/sim/: the page opened with ?sim talks to it instead of a pedal. For development only; it never
opens a MIDI port. http://127.0.0.1 counts as a secure context, as Web MIDI needs.

    .venv/bin/python webmidi/sim_server.py [port]     → http://127.0.0.1:8790/webmidi/?sim
"""
from __future__ import annotations

import json
import posixpath
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mido  # noqa: E402

from tests import sim  # noqa: E402

DEFAULT_PORT = 8790          # not the bridge's 8765
CHAIN = (0x01000010, 0x01000030)          # DYN Comp and OptComp, from zoom_effects.json


def world() -> sim.World:
    """A pedal with two effects whose knobs are all in the patch, running until the server stops."""
    pedal = sim.World(length=float("inf"), chain=CHAIN)
    pedal.whole_patch = True
    pedal.values.update({(0, 2): 6, (0, 4): 70, (0, 5): 80, (1, 2): 3, (1, 5): 90})
    return pedal


def serve(pedal: sim.World, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Starts the server in a background thread: webmidi/ and zoom_effects.json, and /sim/."""
    lock = threading.Lock()

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def _json(self, data) -> None:
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = posixpath.normpath(unquote(urlsplit(self.path).path))     # as the file is looked up
            if self.path == "/sim/poll":
                with lock:
                    pedal.advance()
                    answers = [list(message.bytes()) for message in pedal.zoom_queue]
                    pedal.zoom_queue.clear()
                self._json(answers)
            elif path in ("/zoom_effects.json", "/webmidi") or path.startswith("/webmidi/"):    # nothing else
                super().do_GET()
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            if self.path != "/sim/send":
                self.send_error(404)
                return
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            with lock:
                pedal.receive(mido.Message.from_bytes(data))
            self._json(None)

    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    running = serve(world(), int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT)
    print(f"http://127.0.0.1:{running.server_address[1]}/webmidi/?sim  (Ctrl+C ends it)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        running.shutdown()
