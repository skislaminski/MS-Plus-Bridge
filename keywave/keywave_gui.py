#!/usr/bin/env python3
"""keywave_gui.py - the window of "KeyWave Tables".

A small web server on this computer (127.0.0.1, random port, random access
token) serves one page; it is shown in a native window (pywebview) when that
is available, otherwise in the browser: on Windows in an app window of
Microsoft Edge when Edge is there, else in
a normal browser tab. The page loads a WAV file (dropped
onto it, or one of the examples), shows and plays the 8 frames the KeyWave
effect would get, and writes KEYWAVE.ZD2 + KEYWAVE.ZIC into a folder. The
work is done by keywave_tables.py. Nothing here talks to a pedal: installing
the file stays with stomphacks (https://github.com/thammer/stomphacks).

    keywave_gui.py [--browser] [--port N] [--no-open] [--out DIR]

The effects go into --out, else the folder the environment variable
KEYWAVE_TABLES_OUT names, else Documents/KeyWave Tables.
"""
import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import keywave_tables as kt

APP_NAME = "KeyWave Tables"
VERSION = "1.2"
MAX_UPLOAD = 64 * 1024 * 1024          # a WAV with 256 frames of 4096 float samples is 4 MB
IDLE_EXIT = 90.0                       # browser mode: end when the page has been gone this long
                                       # (as the installer; a hidden tab may poll only once a minute)
WINDOW = (980, 860)


def documents_dir():
    """the user's Documents folder; on Windows the one the shell names, which
    may have been moved (into OneDrive, for example)"""
    if sys.platform == "win32":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(32768)
            # 5 = CSIDL_PERSONAL (Documents); 0 = S_OK
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"


OUT_ENV = "KEYWAVE_TABLES_OUT"


def default_out_dir():
    """the folder the effects go to: the one the environment variable
    KEYWAVE_TABLES_OUT names when it is set, else Documents/KeyWave Tables
    (made when the first effect is written); --out overrides both"""
    own = os.environ.get(OUT_ENV, "")
    if own.strip():
        return Path(os.path.expanduser(own))
    return documents_dir() / "KeyWave Tables"


def example_dir():
    for d in (Path(getattr(sys, "_MEIPASS", kt.HERE)) / "examples", kt.HERE / "examples", kt.HERE.parent / "tables"):
        if d.is_dir():
            return d
    return None


EXAMPLES = [("sawsqr", "Saw → square"), ("pulse", "Pulse 50% → 6%"),
            ("formant", "Formant (vowel)"), ("organ", "Organ (drawbars)")]
OUT_FILES = (kt.FILENAME + ".ZD2", kt.FILENAME + ".ZIC", kt.INFO_FILE)


def takes(folder, new):
    """may the effect `new` be written into folder? Yes if it is not there yet,
    or is a folder whose KEYWAVE.ZD2 is missing or the same; not if it is a
    file, holds another KEYWAVE.ZD2, or one of the three names is a folder"""
    if not folder.exists():
        return True
    if not folder.is_dir():
        return False
    if any((folder / f).exists() and not (folder / f).is_file() for f in OUT_FILES):
        return False
    z = folder / OUT_FILES[0]
    return not z.exists() or z.read_bytes() == new


def no_permission(folder):
    """what to say when the system does not let the app write into folder"""
    if sys.platform == "darwin":
        how = ("Allow it in System Settings → Privacy & Security → Files and Folders → %s "
               "(Documents Folder), then click Make again." % APP_NAME)
    elif sys.platform == "win32":
        how = ("If Controlled folder access is on (Windows Security → Virus & threat protection → "
               "Ransomware protection), allow %s there, then click Make again." % sys.executable)
    else:
        how = "Check the permissions of that folder."
    return "%s may not write to %s. %s" % (APP_NAME, folder, how)


class App:
    def __init__(self, out_dir):
        self.out_dir = Path(out_dir)
        self.lock = threading.Lock()
        self.tables = {}                # id -> (table, info, source name)
        self.write_lock = threading.Lock()
        self.last_seen = time.monotonic()
        self.bye_at = None
        self.pages = {}                 # browser mode: id of each open page -> when it was last seen
        self.base = kt.base_files()     # fails early if the app is incomplete

    def page_seen(self, page):
        """a request from the page; with its id it counts as open until it says bye"""
        now = time.monotonic()
        self.last_seen = now
        self.bye_at = None              # the page is (back) there, e.g. after a reload
        if page:
            with self.lock:
                self.pages.pop(page, None)
                self.pages[page] = now
                while len(self.pages) > 50:
                    self.pages.pop(next(iter(self.pages)))

    def page_gone(self, page):
        """a page said bye (it was closed or reloaded): the program ends a few
        seconds after the last open page has gone, not when one of two tabs closes"""
        now = time.monotonic()
        with self.lock:
            self.pages.pop(page, None)
            for p in [p for p, t in self.pages.items() if now - t > IDLE_EXIT]:
                del self.pages[p]       # a tab that went without a word
            if not self.pages:
                self.bye_at = now

    def remember(self, table, info, source):
        key = hashlib.sha1(json.dumps(table).encode()).hexdigest()[:16]
        with self.lock:
            self.tables[key] = (table, info, source)
            while len(self.tables) > 20:
                self.tables.pop(next(iter(self.tables)))
        return key

    def answer(self, table, info, source, name):
        return {"ok": True, "id": self.remember(table, info, source), "name": kt.clean_name(name),
                "info": info, "frames": kt.preview(table)}

    def load(self, data):
        raw = base64.b64decode(data.get("data") or "")
        fname = str(data.get("file") or "file.wav")
        if raw[:4] == b"ZDLF":          # a KEYWAVE.ZD2: show the table that is in it
            table, name = kt.table_in(raw, self.base[2])
            return self.answer(table, {"source_format": "KEYWAVE.ZD2", "frame_len_how": "table from the effect",
                                       "source_frames": kt.FRAMES, "source_frame_len": kt.lay.FRAME_SAMPLES,
                                       "taken_at": list(range(kt.FRAMES)), "zd2": True}, fname, name)
        table, info = kt.load(raw, data.get("frame_len") or "auto", data.get("normalize") or "table", fname)
        return self.answer(table, info, fname, Path(fname).stem)

    def example(self, data):
        d = example_dir()
        name = str(data.get("name"))
        if d is None or name not in dict(EXAMPLES):
            return {"ok": False, "error": "Example not found."}
        raw = (d / (name + ".wav")).read_bytes()
        table, info = kt.load(raw, data.get("frame_len") or "auto", data.get("normalize") or "table", name)
        return self.answer(table, info, name + ".wav", name)

    def build(self, data):
        with self.lock:
            got = self.tables.get(str(data.get("id")))
        if not got:
            return {"ok": False, "error": "The table is no longer there. Please load the file again."}
        table, info, source = got
        zd2, zic, anchor, src = self.base
        name = kt.clean_name(data.get("name"))
        new = kt.patch(zd2, table, name, anchor)
        folder = self.out_dir / name
        # one folder inside the output folder, never the output folder itself or its parent
        if os.path.dirname(os.path.abspath(str(folder))) != os.path.abspath(str(self.out_dir)):
            raise kt.TableError('"%s" cannot be the name of a folder. Please choose another name.' % name)
        meta = dict(info, name=name, source=source, base_sha256=hashlib.sha256(zd2).hexdigest(),
                    zd2_sha256=hashlib.sha256(new).hexdigest(), made=time.strftime("%Y-%m-%d %H:%M:%S"),
                    program="%s %s" % (APP_NAME, VERSION))
        try:
            with self.write_lock:
                n = 2
                while not takes(folder, new):
                    folder = self.out_dir / ("%s %d" % (name, n))
                    n += 1
                path = kt.write_effect(folder, new, zic, meta)
        except PermissionError:
            raise kt.TableError(no_permission(self.out_dir))
        except OSError as e:
            raise kt.TableError("Could not write the effect into %s: %s." % (folder, e.strerror or e))
        return {"ok": True, "folder": str(folder), "file": str(path), "sha": meta["zd2_sha256"][:16]}

    def reveal(self, data):
        p = str(data.get("path") or "")
        if p and Path(p).exists():
            if sys.platform == "darwin":
                subprocess.Popen(["/usr/bin/open", "-R", p])
            elif sys.platform == "win32":
                subprocess.Popen('explorer /select,"%s"' % os.path.normpath(p))
        return {"ok": True}

    def state(self):
        d = example_dir()
        reveal = {"darwin": "Show in Finder", "win32": "Show in Explorer"}.get(sys.platform)
        return {"app": APP_NAME, "version": VERSION, "out_dir": str(self.out_dir), "reveal": reveal,
                "examples": [{"name": n, "label": l} for n, l in EXAMPLES if d and (d / (n + ".wav")).is_file()],
                "frames": kt.FRAMES, "frame_samples": kt.lay.FRAME_SAMPLES}


def make_handler(app, token, page):
    prefix = "/" + token + "/"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, code, body=b"", ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                 "style-src 'self' 'unsafe-inline'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(body)

        def _route(self):
            """the path after the token, or None: requests that do not name
            this computer as host or lack the token get nothing"""
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                return None
            path = urllib.parse.urlsplit(self.path).path
            return path[len(prefix):] if path.startswith(prefix) else None

        def _page(self):
            """the id the page sends with its polls and its bye (?page=...)"""
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            return (query.get("page") or [""])[0][:64]

        def do_GET(self):
            path = self._route()
            if path is None:
                return self._send(404, b"", "text/plain")
            app.page_seen(self._page())
            if path == "":
                return self._send(200, page, "text/html; charset=utf-8")
            if path == "api/state":
                return self._send(200, app.state())
            self._send(404, b"", "text/plain")

        def do_POST(self):
            path = self._route()
            if path is None:
                return self._send(404, b"", "text/plain")
            app.last_seen = time.monotonic()
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_UPLOAD:
                return self._send(413, {"ok": False, "error": "File too large."})
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except ValueError:
                return self._send(400, {"ok": False, "error": "Unreadable request."})
            actions = {"api/load": app.load, "api/example": app.example, "api/build": app.build,
                       "api/reveal": app.reveal}
            if path == "api/bye":
                app.page_gone(self._page())
                return self._send(200, {"ok": True})
            if path not in actions:
                return self._send(404, {"ok": False})
            try:
                return self._send(200, actions[path](data))
            except kt.TableError as e:
                return self._send(200, {"ok": False, "error": str(e)})
            except Exception as e:      # shown in the window instead of a dead button
                return self._send(200, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    return Handler


def run_window(url):
    try:
        import webview
    except Exception:
        return False
    webview.create_window(APP_NAME, url, width=WINDOW[0], height=WINDOW[1], min_size=(720, 640))
    webview.start()
    return True


def open_in_browser(url):
    """Windows: an app window of Microsoft Edge (no tabs, no address bar; Edge
    is part of Windows). Else, or
    when Edge is not found, a normal browser tab."""
    try:
        if sys.platform == "win32":
            for var in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
                exe = os.path.join(os.environ.get(var, ""), "Microsoft", "Edge", "Application", "msedge.exe")
                if os.path.isfile(exe):
                    subprocess.Popen([exe, "--app=" + url, "--window-size=%d,%d" % WINDOW])
                    return
    except OSError:
        pass
    webbrowser.open(url)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="keywave_gui", description=APP_NAME)
    ap.add_argument("--browser", action="store_true", help="the browser instead of a native window")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-open", action="store_true", help="print the address only (for testing)")
    ap.add_argument("--out", default=None, help="folder for the effects (default: $%s when it is set, "
                    "else Documents/KeyWave Tables)" % OUT_ENV)
    a = ap.parse_args(argv)
    try:
        app = App(a.out or default_out_dir())
    except kt.TableError as e:
        print("%s: %s" % (APP_NAME, e), file=sys.stderr)
        return 1
    page_path = Path(getattr(sys, "_MEIPASS", kt.HERE)) / "keywave_gui.html"
    if not page_path.is_file():
        page_path = kt.HERE / "keywave_gui.html"
    token = secrets.token_urlsafe(16)
    server = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(app, token, page_path.read_bytes()))
    server.daemon_threads = True
    url = "http://127.0.0.1:%d/%s/" % (server.server_address[1], token)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if a.no_open:
        print(url, flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    if not a.browser and run_window(url):
        return 0
    open_in_browser(url)
    while True:                         # browser mode: end when the page is gone
        time.sleep(1.0)
        now = time.monotonic()
        if (app.bye_at and now - app.bye_at > 3.0) or now - app.last_seen > IDLE_EXIT:
            server.shutdown()
            return 0


if __name__ == "__main__":
    sys.exit(main())
