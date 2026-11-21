"""The Zoom MS Installer inside the bridge (`bridge.py --workshop`).

Its page is a tab of the bridge's page. It runs as it does on its own: the same engine and the
same safety, on a listener of its own that only this computer reaches, under a secret address.
One thing differs: before each of its jobs the bridge lets go of the pedal (Bridge.lend), and it
takes it back once the job is over. While the installer's alarm is up the bridge stays away
(bridge.installer_alarm).

Under the same secret address it serves two more pages. The IR Loader Tool (ir-loader): its
"Send to the installer" puts the pair it writes into the installer's list, through the installer's
own upload. The N2Z Bank page (nam): Compact NAM models become N2ZBANK.ZD2 + .ZIC (nam_bank.py) in
the installer's list. The installer's lists show the picture of each effect (picture, from
effect_pictures.py).

Every tab is served in the bridge's look: tab_theme.css goes last into the head of each page.

KeyWave Tables runs beside it, on a listener of its own (it names two addresses as the installer
does), unchanged but for its "Send to the installer", which hands what it made to the installer.

installer/ is an unchanged copy of Zoom MS Installer 1.3 (QUELLEN/programme/installer/zoom-zt2-master).
It is built on zoom-zt2 by Simon Wood and on the transfer safety of stomphacks by Thomas Hammer, both
MIT (installer/NOTICE.md). To update it, copy those files again; tests/test_workshop.py runs its
self-test. ir_loader_tool.html is a copy of "IR Loader Tool.html" (QUELLEN/prototypes/irloader/app,
made by make_tool.py and checked by test_tool.mjs there). keywave/ is a copy of KeyWave Tables 1.2
(QUELLEN/prototypes/wavetable: app/ and tools/wt_*.py, with ms60b/ and wt_tables.bin as base/ and
tables/*.wav as examples/, as its Windows package lays them out)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import signal
import sys
import threading
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Optional

INSTALLER = Path(__file__).resolve().parent / "installer"
TOOL = Path(__file__).resolve().parent / "ir_loader_tool.html"
NAM_PAGE = Path(__file__).resolve().parent / "nam_bank.html"
KEYWAVE = Path(__file__).resolve().parent / "keywave"
THEME = Path(__file__).resolve().parent / "tab_theme.css"
PICTURE_NAME = re.compile(r"[A-Za-z0-9_\-]{1,64}\.ZD2")       # picture?file=: a file name on the pedal, nothing else
# in front of each file name in the installer's two lists, the effect's picture; the installer's own
# script builds those lists anew whenever they change, so each new row gets its picture then
PICTURES_SCRIPT = b"""<script>
(() => {
  function pictures(wrap) {
    for (const row of wrap.querySelectorAll("tr")) {
      const cell = row.querySelector("td.mono");
      if (!cell || cell.querySelector("img")) continue;
      const img = Object.assign(document.createElement("img"),
                                {loading: "lazy", alt: "", src: "picture?file=" + encodeURIComponent(cell.textContent)
                                                                + (wrap.id === "queueWrap" ? "&from=queue" : "")});
      img.style.cssText = "height:34px; image-rendering:pixelated; vertical-align:middle; margin-right:8px";
      img.onerror = () => { img.hidden = true; };
      cell.prepend(img);
    }
  }
  for (const wrap of [document.getElementById("addedWrap"), document.getElementById("queueWrap")]) {
    new MutationObserver(() => pictures(wrap)).observe(wrap, {childList: true});
    pictures(wrap);
  }
})();
</script>
"""
NAM_HEADER = 64 * 1024          # the list of the models sent with a bank to build (nam/build)
# the IR Loader Tool fits in a worker made from a blob, which the installer's own policy
# (default-src 'self') would refuse
T3K_IMAGES = "https://assets.tone3000.com"     # where TONE3000 keeps the pictures of its tones
TOOL_POLICY = ("default-src 'self'; script-src 'self' 'unsafe-inline'; worker-src blob:; "
               f"style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: {T3K_IMAGES}")
NAM_POLICY = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
              f"img-src 'self' data: {T3K_IMAGES}")
sys.path.insert(0, str(INSTALLER))
sys.path.insert(0, str(KEYWAVE))
import zoom_ms_gui as gui            # noqa: E402  (from installer/)
import keywave_gui as keywave        # noqa: E402  (from keywave/)
import zoom_ms_installer as engine   # noqa: E402
import effect_pictures               # noqa: E402
import nam_bank                      # noqa: E402
import nam_train                     # noqa: E402
import tone3000                      # noqa: E402

LEND_SECONDS = 60.0     # a Read Pedal of the bridge can hold its loop this long (ui.READ_PEDAL_SECONDS)
STOP_SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK")


class Workshop(gui.App):
    """The installer's App. A job starts only once the bridge has let go of the pedal."""

    def __init__(self, data_root: str, demo_pedal=None) -> None:
        super().__init__(data_root, demo_pedal)
        self.bridge = None              # set once there is one: without a bridge nobody else has the pedal
        self.url: Optional[str] = None
        self.lock_path: Optional[str] = None
        self.server: Optional[ThreadingHTTPServer] = None
        self.keywave_url: Optional[str] = None
        self.keywave_server: Optional[ThreadingHTTPServer] = None
        self.trainer: Optional[nam_train.Trainer] = None     # NAM models into Compact ones (nam2zoom.app's trainer)
        self.t3k = tone3000.Tone3000(Path(data_root) / "tone3000.json")
        self._starting = threading.Lock()
        self._owner: object = None

    def start_job(self, name, func):
        with self._starting:            # two clicks at once: one job, one loan
            if self.job_running():
                return False
            owner = object()
            if self.bridge is not None:
                reply = self.bridge.call(lambda: self.bridge.lend(owner, name), LEND_SECONDS)
                if "error" in reply:
                    self.say("Not started: " + reply["error"])
                    return False
            self._owner = owner
            if super().start_job(name, func):
                return True
            self._give_back(owner)
            return False

    def _run_job(self, name, func):
        owner = self._owner
        try:
            super()._run_job(name, func)
        finally:                        # only now: its alarm, if it raised one, is on disk
            self._give_back(owner)

    def _give_back(self, owner: object) -> None:
        if self.bridge is not None:
            self.bridge.call(lambda: self.bridge.take_back(owner))

    def training(self) -> nam_train.Trainer:
        """Looked for on first use (Spotlight takes a moment)."""
        if self.trainer is None:
            self.trainer = nam_train.Trainer()
        return self.trainer

    def pages(self) -> dict:
        """The tabs of the bridge's page and their secret addresses (only for this computer)."""
        pages = {"installer": self.url, "ir-loader": self.url + "ir-loader", "nam": self.url + "nam"}
        if self.keywave_url:
            pages["keywave"] = self.keywave_url
        return pages

    def stop(self) -> None:
        """A transfer that is running is finished first. A second Ctrl+C or a closed window must not
        cut it off."""
        kept = {}
        if self.job_running():
            print("A transfer to the pedal is running: the bridge ends once it is done.", flush=True)
            for name in STOP_SIGNALS:
                if hasattr(signal, name):
                    kept[name] = signal.signal(getattr(signal, name), signal.SIG_IGN)
        try:
            self.shutdown()
        finally:
            for name, handler in kept.items():
                signal.signal(getattr(signal, name), handler)
        if self.trainer is not None:          # a training that runs is stopped: it is only a cache entry
            self.trainer.stop()
        for server in (self.server, self.keywave_server):
            if server is not None:
                server.shutdown()
                server.server_close()
        try:
            os.remove(self.lock_path)
        except OSError:
            pass


# inside the bridge's frame (not opened on its own, as the installer is when the bridge cannot start)
FRAMED = b'<script>if (window.top !== window) document.documentElement.dataset.framed = "";</script>\n'


def _themed(page: bytes, style: bytes) -> bytes:
    """A tab's page in the bridge's look: the theme comes last in its head, so it wins."""
    return page.replace(b"</head>", FRAMED + style + b"</head>", 1)


def _handler(app: Workshop, token: str, page: bytes, style: bytes):
    """The installer's handler, and the pages of the IR Loader Tool and of the N2Z Bank under the
    same address, in the bridge's look (style), and the pictures of the effects."""
    tool, nam_page = _themed(TOOL.read_bytes(), style), _themed(NAM_PAGE.read_bytes(), style)
    pictures = effect_pictures.Pictures(app.data_root)

    class Handler(gui.make_handler(app, token, page)):
        def _raw(self, code: int, body: bytes, ctype: str, headers: dict) -> None:
            self.send_response(code)
            for name, value in dict(headers, **{"Content-Type": ctype, "Content-Length": str(len(body)),
                                                 "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _callback(self, query: str):
            """TONE3000 sends its window back to this one address without the secret: it is told to TONE3000.
            Only a sign-in started here (its single-use state) is taken."""
            if (self.headers.get("Host") or "").split(":")[0] != "127.0.0.1":
                return self._send(404, b"", "text/plain")
            try:
                result = app.t3k.callback({key: values[0] for key, values in urllib.parse.parse_qs(query).items()})
            except (ValueError, KeyError, OSError, tone3000.NotSignedIn) as error:
                result = {"ok": False, "error": str(error) or "TONE3000 did not sign you in."}
            self._send(200, tone3000.callback_page(result), "text/html; charset=utf-8")

        def do_GET(self):
            address = urllib.parse.urlsplit(self.path)
            if address.path == "/t3k/callback":
                return self._callback(address.query)
            route = self._route()
            if route is not None and route[0] == "nam":
                return self._raw(200, nam_page, "text/html; charset=utf-8", {"Content-Security-Policy": NAM_POLICY})
            if route is not None and route[0] == "picture":
                return self._picture(route[1].get("file", [""])[0], route[1].get("from", [""])[0] == "queue")
            if route is not None and (route[0].startswith("nam/train/") or route[0].startswith("t3k/")):
                return self._get(*route)
            if route is None or route[0] != "ir-loader":
                return super().do_GET()
            self.send_response(200)
            for name, value in (("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(tool))),
                                ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                                ("Content-Security-Policy", TOOL_POLICY)):
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(tool)

        def _picture(self, name: str, queued: bool):
            """picture?file=NAME.ZD2: the effect's picture (its NAME.ZIC) as a PNG. A row of the list to
            install (from=queue) shows the icon that came with its file, as it gets installed: BLANK.ZIC
            when there is none; the pedal's rows show the backups'."""
            if not PICTURE_NAME.fullmatch(name):
                return self._send(404, b"", "text/plain")
            found = None
            if queued:
                with app.lock:
                    path = next((q["path"] for q in app.queue if q["file"].upper() == name.upper()), None)
                if path is None:
                    return self._send(404, b"", "text/plain")
                for icon in (path[:-4] + ".ZIC", path[:-4] + ".zic", str(INSTALLER / "BLANK.ZIC")):
                    try:
                        found = effect_pictures.png(Path(icon).read_bytes())
                        break
                    except OSError:
                        continue
            else:
                found = pictures.picture(name)
            if found is None:
                return self._send(404, b"", "text/plain")
            self._raw(200, found, "image/png", {})

        def _get(self, path: str, query: dict):
            """The trainings and TONE3000: what the NAM and IR Loader pages ask."""
            one = lambda name, default="": query.get(name, [default])[0]
            try:
                if path == "nam/train/state":
                    return self._send(200, app.training().state())
                if path == "nam/train/model":
                    name, data = app.training().model(int(one("id", "0")))
                    return self._raw(200, data, "application/octet-stream", {"X-File-Name": urllib.parse.quote(name)})
                if path == "nam/train/preview":
                    return self._raw(200, app.training().preview(int(one("id", "0")), one("which")), "audio/wav", {})
                if path == "t3k/status":
                    return self._send(200, app.t3k.status())
                if path == "t3k/start":                   # the window opened from a click on the page
                    port = self.server.server_address[1]
                    address = app.t3k.start(one("kind"), f"http://127.0.0.1:{port}/t3k/callback")
                    return self._raw(302, b"", "text/plain", {"Location": address})
                if path == "t3k/tone":
                    return self._send(200, app.t3k.tone(one("id"), one("kind")))
                if path == "t3k/file":
                    name, data, credit = app.t3k.file(one("model"))
                    return self._raw(200, data, "application/octet-stream",
                                     {"X-File-Name": urllib.parse.quote(name),
                                      "X-Credit": urllib.parse.quote(json.dumps(credit))})
            except tone3000.NotSignedIn:
                return self._send(401, {"ok": False, "error": "Sign in to TONE3000 again."})
            except (ValueError, KeyError, OSError) as error:
                return self._send(400, {"ok": False, "error": str(error)})
            self._send(404, b"", "text/plain")

        def do_POST(self):
            """nam/check and nam/build take models of up to 16 MiB each and nam/train one of up to 64 MiB,
            more than the installer's own uploads (1 MiB); everything else is the installer's."""
            route = self._route()
            small = ("nam/train/cancel", "nam/train/review", "t3k/signout")
            if route is not None and (route[0] == "nam/train" or route[0] in small):
                return self._post(*route)
            # the IR Loader's tab sends while a job may run (the installer's own page waits): the file
            # in the queue would be replaced under the job that is about to read it
            if route is not None and route[0] == "api/upload" and app.job_running():
                return self._send(200, {"ok": False, "busy": True})
            if route is None or route[0] not in ("nam/check", "nam/build"):
                return super().do_POST()
            most = nam_bank.MAX_CHECK if route[0] == "nam/check" else nam_bank.MAX_MODELS * nam_bank.MAX_NAM + NAM_HEADER
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            least = 0 if route[0] == "nam/check" else 1     # an empty file is checked (and named so)
            if not least <= length <= most:
                return self._send(413, {"ok": False, "error": "Too large: nam2zoom takes models of up to 16 MiB into a bank."})
            body = self.rfile.read(length)
            if route[0] == "nam/check":
                return self._send(200, nam_bank.check(route[1].get("name", ["model.nam"])[0], body))
            self._send(200, _build_bank(app, body))

        def _post(self, path: str, query: dict):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= (tone3000.MAX_FILE if path == "nam/train" else NAM_HEADER):
                return self._send(413, {"ok": False, "error": "Too large."})
            body = self.rfile.read(length) if length else b""
            try:
                if path == "nam/train":
                    return self._send(200, _train(app, query.get("name", ["model.nam"])[0], body,
                                                  self.headers.get("X-Credit")))
                data = json.loads(body.decode("utf-8")) if body else {}
                if path == "nam/train/cancel":
                    app.training().cancel(int(data.get("id", 0)))
                elif path == "nam/train/review":
                    app.training().review(int(data.get("id", 0)), data.get("use") is True)
                elif path == "t3k/signout":
                    app.t3k.sign_out()
                return self._send(200, {"ok": True})
            except (ValueError, TypeError, AttributeError, OSError) as error:   # OSError: a model not written
                return self._send(400, {"ok": False, "error": str(error) or "Invalid request."})

    return Handler


def _train(app: Workshop, name: str, data: bytes, credit: Optional[str]) -> dict:
    """nam/train: a NAM that is not Compact yet, into the trainer's queue."""
    found = nam_bank.check(name, data)
    if found["status"] == "direct":
        return {"ok": False, "error": "This one is Compact already: it goes into the bank as it is."}
    if found["status"] != "adaptable":
        return {"ok": False, "error": f"nam2zoom cannot train this one: {found.get('reason')}"}
    try:
        credit = json.loads(urllib.parse.unquote(credit)) if credit else None
    except ValueError:
        credit = None
    job = app.training().add(name, data, credit if isinstance(credit, dict) else None)
    return {"ok": True, "id": job.id}


def _build_bank(app: Workshop, body: bytes) -> dict:
    """nam/build: a 4-byte length, the list {"models": [{"name", "size", "label"}]} as JSON, then the
    models one after the other. The bank goes into the installer's list, as an upload would."""
    try:
        size = int.from_bytes(body[:4], "little")
        if not 0 < size <= NAM_HEADER:
            raise ValueError
        listed = json.loads(body[4:4 + size].decode("utf-8"))["models"]
        models, labels, at = [], [], 4 + size
        for entry in listed:
            name, length, label = str(entry["name"]), int(entry["size"]), str(entry["label"])
            if not 0 < length <= nam_bank.MAX_NAM or at + length > len(body):
                raise ValueError
            models.append((name, body[at:at + length]))
            labels.append(label)
            at += length
        if at != len(body):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return {"ok": False, "error": "Invalid request."}
    if app.job_running():
        return {"ok": False, "error": "The installer is working on the pedal: build again once it is done."}
    try:
        zd2, zic = nam_bank.build(models, labels)
    except ValueError as error:
        return {"ok": False, "error": str(error)}
    if not (app.save_upload("N2ZBANK.ZD2", zd2) and app.save_upload("N2ZBANK.ZIC", zic)):
        return {"ok": False, "error": "The installer did not take the bank."}
    app.rescan()
    return {"ok": True, "labels": labels, "zd2_sha256": hashlib.sha256(zd2).hexdigest(),
            "patches": nam_bank.patches_using_bank(app.data_root)}


class KeyWave(keywave.App):
    """KeyWave Tables beside the installer. Its effects go where they always go (Documents/KeyWave
    Tables); "Send to the installer" puts one of them into the installer's list."""

    def __init__(self, installer: Workshop, out_dir=None) -> None:
        super().__init__(out_dir or keywave.default_out_dir())
        self.installer = installer

    def state(self):
        return dict(super().state(), installer=True)        # its page shows "Send to the installer"

    def send(self, data: dict) -> dict:
        path = Path(str(data.get("file") or ""))
        # only a KEYWAVE.ZD2 this app wrote: in a folder of its own inside its output folder
        if (path.name != "KEYWAVE.ZD2" or not path.is_file() or not path.with_suffix(".ZIC").is_file()
                or path.resolve().parent.parent != self.out_dir.resolve()):
            return {"ok": False, "error": "Make the effect first."}
        if self.installer.job_running():
            return {"ok": False, "error": "The installer is working on the pedal: send again once it is done."}
        if not (self.installer.save_upload("KEYWAVE.ZD2", path.read_bytes())
                and self.installer.save_upload("KEYWAVE.ZIC", path.with_suffix(".ZIC").read_bytes())):
            return {"ok": False, "error": "The installer did not take the effect."}
        self.installer.rescan()
        return {"ok": True}


def _start_keywave(installer: Workshop, out_dir, style: bytes) -> None:
    app = KeyWave(installer, out_dir)
    token = secrets.token_urlsafe(16)

    class Handler(keywave.make_handler(app, token, _themed((KEYWAVE / "keywave_gui.html").read_bytes(), style))):
        def do_POST(self):
            if self._route() != "api/send":
                return super().do_POST()
            try:
                length = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(length).decode("utf-8")) if 0 < length <= 64 * 1024 else None
            except ValueError:
                data = None
            if not isinstance(data, dict):
                return self._send(400, {"ok": False, "error": "Unreadable request."})
            self._send(200, app.send(data))

    installer.keywave_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    installer.keywave_server.daemon_threads = True
    installer.keywave_url = f"http://127.0.0.1:{installer.keywave_server.server_address[1]}/{token}/"
    threading.Thread(target=installer.keywave_server.serve_forever, daemon=True).start()


def start(data_root: Optional[str] = None, demo_pedal=None, keywave_out=None) -> Workshop:
    """Starts the installer beside the bridge, as its own main() does, but without its window: the
    bridge's page shows it (Workshop.pages). Raises RuntimeError when it cannot be here. KeyWave
    Tables starts beside it, its effects going into keywave_out (its own default: Documents/KeyWave
    Tables); without it the other tabs stay."""
    data_root = os.path.abspath(data_root or gui.default_data_root())
    lock_path = os.path.join(data_root, "running.json")
    try:
        os.makedirs(data_root, exist_ok=True)
        open(lock_path, "a").close()    # "a": keeps the address of an installer that is running
    except OSError as error:
        raise RuntimeError(f"its folder {data_root} cannot be used ({error.strerror or error}); if it is a "
                           "link to a folder that was moved, point it to the new place") from error
    if gui.already_running(lock_path):
        raise RuntimeError("the Zoom MS Installer is open on its own – close it and start the bridge again")
    engine.set_data_root(data_root)
    engine.open_log("gui")
    app = Workshop(data_root, demo_pedal)
    engine.ui = app
    token = secrets.token_urlsafe(16)
    style = b"<style>\n" + THEME.read_bytes() + b"</style>\n"
    page = _themed((INSTALLER / "zoom_ms_gui.html").read_bytes(), style).replace(b"</body>", PICTURES_SCRIPT + b"</body>", 1)
    app.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(app, token, page, style))
    app.server.daemon_threads = True
    app.url = f"http://127.0.0.1:{app.server.server_address[1]}/{token}/"
    app.lock_path = lock_path
    with open(lock_path, "w") as f:     # a standalone installer started now opens this one instead
        json.dump({"url": app.url, "pid": os.getpid()}, f)
    threading.Thread(target=app.server.serve_forever, daemon=True).start()
    engine.say(f"{gui.APP_NAME} {gui.VERSION} in the bridge" + (" - DEMO with a simulated pedal" if demo_pedal else ""))
    engine.say(f"Backups, adapted copies and logs are kept in: {data_root}")
    try:
        _start_keywave(app, keywave_out, style)
    except Exception as error:          # an incomplete copy: the other tabs stay
        engine.say(f"KeyWave Tables is not here: {type(error).__name__}: {error}")
    return app
