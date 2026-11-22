#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zoom MS Installer - the graphical front end.

A window (macOS) or an app-style browser window (Windows) in front of the
same engine the text menu uses (zoom_ms_installer.py): every check, every
read-back and every safety rule is the engine's; this file only shows what
the engine says and passes on what the user answers.

How it is built: a tiny web server that listens on this computer only
(127.0.0.1, random port, random access token) serves one page; the page asks
the server for the current state a few times a second. On macOS the page is
shown in a native window (pywebview) when that is available, otherwise in the
browser.

    zoom_ms_gui.py                 start the program
    zoom_ms_gui.py --browser       always use the browser instead of a window
    zoom_ms_gui.py --demo          try it with a simulated pedal (from source)
    zoom_ms_gui.py --data FOLDER   keep Backups/Adapted/Logs in FOLDER
"""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
try:
    import fcntl
except ImportError:             # Windows
    fcntl = None

import zoom_ms_installer as m

APP_NAME = "Zoom MS Installer"
VERSION = "1.3"
MAX_UPLOAD = 1 << 20            # effect files are well under 100 KB
MAX_LOG = 4000
UPLOAD_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,8}\.(zd2|zic)$", re.IGNORECASE)
IDLE_EXIT = 90.0                # browser mode: quit when the page is gone
HEADLINE = re.compile(
    r"^(REFUSED|NOT COMPLETED|UNRESOLVED|TRANSFER ERROR|UNEXPECTED ERROR|"
    r"Cancelled|Nothing to transfer|Installed \d+|Backup done|Connected:|"
    r"The effect list |Effect list written back|.* removed, effect list)")


def model_name(model):
    if model is None:
        return "pedal that could not be identified"
    return m.MODELS.get(model, ("model 0x%02x" % model,))[0]


def default_data_root():
    """Backups must survive an update of the program, so they live in the
    user's home folder, not next to the program."""
    return os.path.join(os.path.expanduser("~"), APP_NAME)


def full_sync(fd):
    """fsync - on macOS also empty the drive's own cache, which a plain
    fsync leaves to the drive (power loss)."""
    os.fsync(fd)
    if hasattr(fcntl, "F_FULLFSYNC"):
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
        except OSError:
            pass                # e.g. SMB: the fsync above is all there is


def sync_folder(path):
    """Make a rename in `path` reach the disk (not possible on Windows)."""
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            full_sync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass                    # the file itself is already on the disk


def show_error(text):
    """A message the user sees even without a console (the Mac app,
    pythonw on Windows)."""
    if sys.stderr:              # None under pythonw
        sys.stderr.write(text + "\n")
    try:
        if sys.platform == "darwin":
            subprocess.run(["/usr/bin/osascript",
                            "-e", "on run argv",
                            "-e", "display alert (item 1 of argv) message "
                                  "(item 2 of argv) as critical",
                            "-e", "end run", APP_NAME, text], timeout=600)
        elif os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10)
    except Exception:
        pass


class App(object):
    """State of the running program + the engine's `ui` object."""

    def __init__(self, data_root, demo_pedal=None):
        self.data_root = data_root
        self.demo_pedal = demo_pedal
        self.lock = threading.RLock()
        self.log_lines = []
        self.log_base = 0             # number of the first kept line
        self.headline = ""
        self.question = None
        self.question_id = 0
        self.answer = None
        self.answered = threading.Event()
        self.job = None               # {"name", "running"}
        self.job_thread = None
        self.result = None            # {"job", "code", "message"}
        self.progress_state = None
        self.pedal = None
        self.queue = []               # list of item dicts, see describe()
        self.unresolved = False
        self.notice = ""
        self.auto_default = False
        self.stopping = False
        self.window = None            # pywebview window, if any
        self.last_seen = time.monotonic()
        self.bye_at = None
        self.inbox = tempfile.mkdtemp(prefix="zoom-ms-inbox-")
        self.seen_model = None        # model the last job identified
        self.job_model = None         # ... the running job identified
        self.job_wrote = False        # ... and started writing to it
        self.alarm_models = []        # models whose list is in doubt
        self.restore_from = None      # backup the red bar can restore from
        # An unverified transfer is remembered until a rescue of THAT pedal
        # says the list is fine, also across a restart of the program (the
        # window may have been closed before the result could be seen).
        self.marker = os.path.join(data_root, "unresolved.json")
        try:
            with open(self.marker) as f:
                saved = json.load(f)
            self.unresolved = bool(saved)
            models = saved.get("models") if isinstance(saved, dict) else None
            self.alarm_models = [x for x in models
                                 if x is None or isinstance(x, int)] \
                if isinstance(models, list) else [None]
            if self.unresolved and not self.alarm_models:
                self.alarm_models = [None]
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            # a marker that is there but cannot be read is an alarm, not
            # "all clear"
            self.unresolved = True
            self.alarm_models = [None]

    # what the rescue tells the user when the list is broken (the engine's
    # own text names a command line, which the window does not have)
    restore_hint = ("Leave the pedal POWERED. If there is a backup of this "
                    "pedal, the red bar above offers to restore the effect "
                    "list.")

    # ------------------------------------------------ the engine's ui object

    def say(self, text):
        with self.lock:
            self.log_lines.append(text)
            if len(self.log_lines) > MAX_LOG:
                drop = len(self.log_lines) - MAX_LOG
                del self.log_lines[:drop]
                self.log_base += drop
            if HEADLINE.match(text):
                self.headline = text

    def progress(self, text, done=None, total=None):
        with self.lock:
            self.progress_state = None if text is None else {
                "text": text.strip(), "done": done, "total": total}

    def _ask(self, question):
        """Show a question in the window and wait for the answer. Returns
        None if the program is closing."""
        with self.lock:
            self.question_id += 1
            question["id"] = self.question_id
            self.question = question
            self.answer = None
            self.answered.clear()
        while not self.answered.wait(0.3):
            if self.stopping:
                with self.lock:
                    self.question = None
                return None
        with self.lock:
            self.question = None
            return self.answer

    def ask_yes(self, question, details=None):
        answer = self._ask({"kind": "yesno", "text": question,
                            "details": list(details or [])})
        if answer == "cancel":
            # The page offers Cancel only for the backup question, which comes
            # before anything is written: the whole job ends there.
            raise m.Refused("cancelled")
        return answer is True

    def choose(self, title, options, prompt):
        answer = self._ask({"kind": "choose", "title": title,
                            "text": prompt, "options": list(options)})
        if isinstance(answer, int) and 0 <= answer < len(options):
            return answer
        return None

    def choose_category(self, zd2, table, own, current, used_ids, show_list):
        default = current or own

        def taken(cat):
            return ((cat["group"] << 24) | (zd2.id & 0xFFFFFF)) in used_ids

        if self.auto_default and default and not taken(default):
            return default
        options = [{"group": g, "label": table[g]["label"],
                    "count": table[g]["count"], "taken": taken(table[g]),
                    "own": bool(own and own["group"] == g),
                    "current": bool(current and current["group"] == g)}
                   for g in sorted(table)]
        while True:
            answer = self._ask({
                "kind": "category", "effect": zd2.name,
                "built_for": zd2.groupname,
                "current": current["label"] if current else "",
                "default": default["group"]
                if default and not taken(default) else None,
                "options": options})
            if not isinstance(answer, dict) or answer.get("skip"):
                return None
            group = answer.get("group")
            if group in table and not taken(table[group]):
                if answer.get("all"):
                    self.auto_default = True
                m.log("? category for %s -> %s" % (zd2.name, group))
                return table[group]

    # ----------------------------------------------------------------- jobs

    def transport(self):
        """None = the real pedal; the demo uses a simulated one."""
        return self.demo_pedal

    @staticmethod
    def opts(**kw):
        base = dict(yes=False, pedal=None, new_backup=False, adapt="auto",
                    category=None, force=False, model=None, flst=None,
                    write=False, files=[], file=None)
        base.update(kw)
        return argparse.Namespace(**base)

    def job_running(self):
        with self.lock:
            return bool(self.job and self.job["running"])

    def start_job(self, name, func):
        with self.lock:
            if self.job_running():
                return False
            self.job = {"name": name, "running": True}
            self.job_model = None
            self.job_wrote = False
            self.result = None
            self.progress_state = None
            self.headline = ""
            self.auto_default = False
            self.notice = ""
        self.job_thread = threading.Thread(
            target=self._run_job, args=(name, func), name="job-" + name)
        self.job_thread.start()       # not a daemon: never killed mid-write
        return True

    def _run_job(self, name, func):
        with self.lock:
            before = list(self.alarm_models)
        m.say("")
        m.keep_awake(True)
        try:
            code = func()
        except BaseException as e:    # must never take the program down
            import traceback
            m.log(traceback.format_exc())
            m.say("UNEXPECTED ERROR: %s: %s" % (type(e).__name__, e))
            code = 4
        finally:
            m.keep_awake(False)
        # a backup only reads: its code 4 is an alarm only if the engine
        # found the list in doubt (not e.g. a full disk on the computer)
        if code == 4 and (name in ("install", "remove", "rescue", "restore",
                                   "remove_stock", "restore_stock")
                          or name == "backup"
                          and self.headline.startswith("UNRESOLVED")):
            self._set_unresolved(True, self.job_model)
        elif name in ("rescue", "restore") and code == 0 and self.unresolved:
            # this pedal is fine now; an alarm of an unidentified pedal goes
            # too, one of another model stays
            left = [x for x in self.alarm_models
                    if x is not None and x != self.job_model]
            if left:
                self._set_alarm(left)
                m.say("The red bar stays: the effect list in doubt is on the "
                      "%s. Connect that pedal and run the rescue there."
                      % " and the ".join(model_name(x) for x in left))
            else:
                self._set_unresolved(False)
        elif self.job_wrote:
            # the write ended in a verified state: drop its in-progress alarm
            self._set_alarm(before)
        self._refresh_restore()
        with self.lock:
            self.job["running"] = False
            self.result = {"job": name, "code": code,
                           "message": self.headline}
            self.question = None
            self.progress_state = None

    def _set_unresolved(self, flag, model=None):
        """Raise the alarm for `model` (None: not identified), keeping any
        alarm already up for another pedal; or clear it altogether."""
        with self.lock:
            models = list(self.alarm_models) if flag else []
        if flag and model not in models:
            models.append(model)
        self._set_alarm(models)

    def writing(self, ident):
        """Called by the engine right before it writes to the pedal. The
        alarm is saved to disk now, so that a hard end of the program in the
        middle (Force Quit, crash, power loss) still shows the red bar at the
        next start. It is not shown now - nothing went wrong yet; _run_job
        raises it if the write fails and takes it back when it ends
        verified."""
        model = ident["model"] if ident else None
        with self.lock:
            self.job_wrote = True
            models = list(self.alarm_models)
        if model not in models:
            models.append(model)
        self._save_marker(models)

    def _set_alarm(self, models):
        with self.lock:
            self.alarm_models = list(models)
            self.unresolved = bool(models)
        self._save_marker(models)

    def _save_marker(self, models):
        saved = {"since": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "models": list(models)}
        try:
            if models:
                # temp file + rename: a write that fails half-way never
                # leaves an empty marker behind
                tmp = self.marker + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(saved, f)
                    f.flush()
                    full_sync(f.fileno())
                os.replace(tmp, self.marker)
                sync_folder(os.path.dirname(self.marker))
            elif os.path.exists(self.marker):
                os.remove(self.marker)
        except OSError as e:
            if models:
                m.say("WARNING: the alarm could not be saved (%s). If the "
                      "program ends now, it will not remind you at the next "
                      "start - leave the pedal POWERED until the rescue "
                      "reports the effect list valid." % e)

    def _restore_model(self):
        with self.lock:
            first = next((x for x in self.alarm_models if x is not None),
                         None)
            for model in (first, self.seen_model,
                          (self.pedal or {}).get("model")):
                if model is not None:
                    return model
        return None

    def _identified(self, ident):
        """A job talked to this pedal: it is the one the restore offer and
        the alarm refer to. A pedal view of another pedal is out of date."""
        with self.lock:
            self.seen_model = self.job_model = ident["model"]
            if self.pedal and self.pedal["model"] != ident["model"]:
                self.pedal = None

    def _refresh_restore(self):
        """The backup the red bar offers: the newest one of the pedal whose
        list is in doubt, else of the pedal the last job talked to."""
        model = self._restore_model()
        found = m.latest_backup(model=model) if model is not None else None
        with self.lock:
            self.restore_from = {
                "folder": os.path.basename(found[0]), "path": found[0],
                "created": found[1].get("created", "").replace("T", " "),
            } if found else None

    def job_connect(self):
        return m.run(self._read_pedal, self.opts())

    def _read_pedal(self, opts):
        st = m.read_state(opts, self.transport())
        self._identified(st["ident"])
        entries, stock, table = st["entries"], st["stock"], st["table"]
        added = [{"file": e[1], "version": e[2], "id": "0x%08x" % e[3],
                  "group": e[0],
                  "label": table.get(e[0], {}).get("label",
                                                   "group %d" % e[0])}
                 for e in entries if stock and e[1].upper() not in stock]
        listed = set(e[1].upper() for e in entries)

        def label(group):
            return table.get(group, {}).get("label", "group %d" % group)
        stock_on = [{"file": e[1], "version": e[2], "id": "0x%08x" % e[3],
                     "label": label(e[0])} for e in entries
                    if e[1].upper() in stock
                    and e[1].upper() not in m.KEEP_STOCK]
        stock_missing = [{"file": n, "id": "0x%08x" % eid,
                          "label": label(eid >> 24)}
                         for n, eid in sorted(stock.items())
                         if eid >= 0 and n not in listed
                         and n not in m.KEEP_STOCK]
        with self.lock:
            self.pedal = {
                "name": st["ident"]["name"],
                "firmware": st["ident"]["firmware"],
                "model": st["ident"]["model"],
                "known": st["ident"]["folder"] is not None,
                "effects": len(entries), "flst_ok": st["flst_ok"],
                "storage_free": st["storage_free"],
                "storage_max": st["storage_max"],
                "added": added, "read_at": time.strftime("%H:%M:%S"),
                "stock_on": stock_on, "stock_missing": stock_missing,
            }
        self._refresh_backup()
        m.say("%d effects on the pedal, %d of them added. %.1f of %.1f MB "
              "free." % (len(entries), len(added), st["storage_free"] / 1e6,
                         st["storage_max"] / 1e6))
        if not st["flst_ok"]:
            # A pedal with a broken effect list must not be restarted before
            # the list is repaired: raise the same alarm as after a failed
            # transfer and point to the rescue.
            self._set_unresolved(True, st["ident"]["model"])
            m.say("UNRESOLVED: the effect list on the pedal is NOT valid "
                  "(%s)" % "; ".join(st["problems"]))
            return 4
        return 0

    def _refresh_backup(self):
        with self.lock:
            if not self.pedal:
                return
            found = m.latest_backup(model=self.pedal["model"])
            self.pedal["backup"] = {
                "folder": os.path.basename(found[0]), "path": found[0],
                "created": found[1].get("created", "").replace("T", " "),
            } if found else None

    def _engine(self, command, opts):
        """Run an engine command; remember which pedal it talked to (the
        restore offer needs the model even if Connect was never pressed)."""
        code = m.run(lambda o: command(o, self.transport()), opts)
        ident = getattr(opts, "ident", None)
        if ident:
            self._identified(ident)
        return code

    def job_backup(self):
        code = self._engine(m.cmd_backup, self.opts())
        self._refresh_backup()
        return code

    def job_install(self):
        with self.lock:
            files = [q["path"] for q in self.queue if q["ok"]]
        if not files:
            m.say("REFUSED (nothing was changed on the pedal): there is no "
                  "effect in the list")
            return 2
        code = self._engine(m.cmd_install, self.opts(files=files))
        return self._read_again_after(code)

    def job_remove(self, names, stock=False):
        """Remove the effects ticked in the window, all in one job (stock:
        stock effects, which restore_stock brings back)."""
        if not names:
            m.say("REFUSED (nothing was changed on the pedal): no effect was "
                  "selected")
            return 2
        code = self._engine(m.cmd_uninstall,
                            self.opts(files=list(names), stock=stock))
        return self._read_again_after(code)

    def job_restore_stock(self, names):
        """Bring back the stock effects ticked in the window."""
        if not names:
            m.say("REFUSED (nothing was changed on the pedal): no effect was "
                  "selected")
            return 2
        code = self._engine(m.cmd_restore_stock,
                            self.opts(files=list(names), source=[]))
        return self._read_again_after(code)

    def _read_again_after(self, code):
        """After a change that went through, show the pedal's new contents.
        The result line of the change itself stays the headline - unless the
        list read back is broken: then the job ends as unresolved (4)."""
        headline = self.headline
        if code in (0, 3):    # 3: not completed, but the state is verified
            m.say("")
            if m.run(self._read_pedal, self.opts()) == 4:
                code = 4
        self._refresh_backup()
        if code != 4:
            self.headline = headline
        return code

    def job_rescue(self, restore=False):
        flst = None
        if restore:
            model = self._restore_model()
            found = m.latest_backup(model=model) if model is not None \
                else None
            if not found:
                m.say("REFUSED (nothing was changed on the pedal): there is "
                      "no backup of this pedal to restore the effect list "
                      "from")
                return 2
            flst = os.path.join(found[0], "files", m.FLST_NAME)
            m.say("Using the effect list from the backup %s"
                  % os.path.basename(found[0]))
        return self._engine(m.cmd_rescue, self.opts(flst=flst,
                                                     write=restore))

    # ---------------------------------------------------------------- queue

    @staticmethod
    def describe(path):
        item = {"path": path, "file": m.pedal_name(path), "ok": False,
                "name": "", "version": "", "category": "", "icon": False,
                "problem": ""}
        try:
            zd2, _, notes = m.load_effect(path)
        except m.Refused as e:
            item["problem"] = str(e)
            return item
        item.update(ok=True, name=zd2.name, version=zd2.version,
                    category=zd2.groupname, icon=not notes)
        return item

    def add_paths(self, paths):
        """Files and folders -> the list of effects to install. A file name
        that is already in the list replaces the entry (a newer build); the
        same name twice in what is added now keeps the first one, as the
        text menu does."""
        added = 0
        seen = set()
        for path in paths:
            path = os.path.abspath(os.path.expanduser(path))
            if os.path.isdir(path):
                candidates = m.zd2_files_in(path)
            elif path.upper().endswith(".ZD2"):
                candidates = [path]
            else:
                continue              # icons come along with their effect
            for cand in candidates:
                key = m.pedal_name(cand).upper()
                if key in seen:
                    m.say("Not added: %s - a file of that name is already "
                          "being added (%s)" % (cand, key))
                    continue
                item = self.describe(cand)
                if item["ok"]:        # a broken copy does not shut out others
                    seen.add(key)
                with self.lock:
                    gone = [q for q in self.queue
                            if q["file"].upper() == item["file"].upper()
                            and q["path"] != item["path"]]
                    self.queue = [q for q in self.queue
                                  if q["file"].upper() != item["file"].upper()]
                    self.queue.append(item)
                for q in gone:        # its uploaded copy must not come back
                    self._forget_upload(q["path"])
                added += 1
        return added

    def rescan(self):
        """After uploads: take in new effects, re-check the known ones (an
        icon may have arrived after its effect)."""
        try:
            names = sorted(os.listdir(self.inbox))
        except OSError:
            names = []
        with self.lock:
            known = set(q["path"] for q in self.queue)
            self.queue = [self.describe(q["path"]) for q in self.queue]
        self.add_paths([os.path.join(self.inbox, n) for n in names
                        if n.upper().endswith(".ZD2")
                        and os.path.join(self.inbox, n) not in known])

    def remove_from_queue(self, names):
        """Take the effects with these file names out of the list."""
        names = set(names)
        with self.lock:
            gone = [q for q in self.queue if q["file"] in names]
            self.queue = [q for q in self.queue if q["file"] not in names]
        for q in gone:
            self._forget_upload(q["path"])

    def clear_queue(self):
        with self.lock:
            gone, self.queue = self.queue, []
        for q in gone:
            self._forget_upload(q["path"])

    def _forget_upload(self, path):
        """Delete an uploaded copy (never a file the user picked in place)."""
        if os.path.dirname(os.path.abspath(path)) != self.inbox:
            return
        for victim in (path, path[:-4] + ".ZIC"):
            try:
                os.remove(victim)
            except OSError:
                pass

    def save_upload(self, name, data):
        name = os.path.basename(name.replace("\\", "/"))
        if not UPLOAD_NAME.match(name) or len(data) > MAX_UPLOAD:
            return False
        stem, ext = os.path.splitext(name)
        with open(os.path.join(self.inbox, stem + ext.upper()), "wb") as f:
            f.write(data)
        return True

    def pick(self, kind):
        """Native file dialog (window mode only). -> number of effects."""
        if self.window is None:
            return None
        import webview
        if kind == "folder":
            mode = getattr(webview, "FOLDER_DIALOG", 20)
            result = self.window.create_file_dialog(mode)
        else:
            mode = getattr(webview, "OPEN_DIALOG", 10)
            result = self.window.create_file_dialog(
                mode, allow_multiple=True,
                file_types=("Zoom effects (*.ZD2;*.zd2)",))
        if not result:
            return 0
        return self.add_paths(list(result))

    # ---------------------------------------------------------------- state

    def state(self, since):
        with self.lock:
            self.last_seen = time.monotonic()
            self.bye_at = None
            first = max(since, self.log_base)
            notice, self.notice = self.notice, ""
            return {
                "n": self.log_base + len(self.log_lines),
                "log": self.log_lines[first - self.log_base:],
                "job": dict(self.job) if self.job else None,
                "result": dict(self.result) if self.result else None,
                "question": self.question,
                "progress": self.progress_state,
                "pedal": self.pedal,
                "queue": [{k: v for k, v in q.items() if k != "path"}
                          for q in self.queue],
                "unresolved": self.unresolved,
                "restore_from": self.restore_from,
                "notice": notice,
                "native": self.window is not None,
                "demo": self.demo_pedal is not None,
                "version": VERSION,
                "data_root": self.data_root,
            }

    def open_folder(self, what):
        folder = {"backups": m.BACKUP_ROOT, "adapted": m.ADAPT_ROOT,
                  "logs": m.LOG_ROOT}.get(what, self.data_root)
        os.makedirs(folder, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)                    # noqa: only on Windows
        elif sys.platform == "darwin":
            subprocess.Popen(["/usr/bin/open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

    def shutdown(self):
        self.stopping = True
        if self.job_thread is not None:
            self.job_thread.join()    # a running transfer is finished first
        shutil.rmtree(self.inbox, ignore_errors=True)


# ------------------------------------------------------------ web server

def effect_names(data):
    """The file names a remove request (from the pedal or from the list)
    carries: {"files": [...]}, or {"file": "..."} for a single effect."""
    names = data.get("files")
    if not isinstance(names, list):
        names = [data.get("file")]
    return [n for n in names if isinstance(n, str) and n]


def make_handler(app, token, page):
    prefix = "/" + token + "/"
    # The build check questions the page through the window, which needs
    # eval; the program itself does not, so it stays forbidden otherwise.
    script_src = "'self' 'unsafe-inline'" + (
        " 'unsafe-eval'" if os.environ.get("ZOOM_MS_GUI_SELFCHECK") else "")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, code, body=b"", ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if ctype.startswith("text/html"):
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src " + script_src + "; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(body)

        def _route(self):
            """The part of the path after the token, or None. Requests that
            do not name this computer as host or lack the token get nothing:
            no other web page may drive the pedal."""
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                return None
            path = urllib.parse.urlsplit(self.path)
            if not path.path.startswith(prefix):
                return None
            return path.path[len(prefix):], urllib.parse.parse_qs(path.query)

        def do_GET(self):
            route = self._route()
            if route is None:
                return self._send(404, b"", "text/plain")
            path, query = route
            if path == "":
                return self._send(200, page, "text/html; charset=utf-8")
            if path == "api/state":
                try:
                    since = int(query.get("since", ["0"])[0])
                except ValueError:
                    since = 0
                return self._send(200, app.state(since))
            if path == "api/ping":
                return self._send(200, {"app": APP_NAME})
            self._send(404, b"", "text/plain")

        def do_POST(self):
            route = self._route()
            if route is None:
                return self._send(404, b"", "text/plain")
            path, query = route
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_UPLOAD + 1024:
                return self._send(413, {"ok": False})
            body = self.rfile.read(length) if length else b""
            if path == "api/upload":
                name = query.get("name", [""])[0]
                return self._send(200, {"ok": app.save_upload(name, body)})
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except ValueError:
                return self._send(400, {"ok": False})
            self._send(200, self._action(path, data))

        def _action(self, path, data):
            if path == "api/answer":
                with app.lock:
                    if app.question and app.question["id"] == data.get("id"):
                        app.answer = data.get("value")
                        app.answered.set()
                return {"ok": True}
            if path == "api/bye":
                app.bye_at = time.monotonic()
                return {"ok": True}
            if path == "api/open":
                app.open_folder(data.get("what"))
                return {"ok": True}
            if path.startswith("api/queue/") or path == "api/pick":
                if app.job_running():
                    return {"ok": False, "busy": True}
                if path == "api/queue/rescan":
                    app.rescan()
                elif path == "api/queue/remove":
                    app.remove_from_queue(effect_names(data))
                elif path == "api/queue/clear":
                    app.clear_queue()
                elif path == "api/pick":
                    return {"ok": True, "added": app.pick(data.get("kind"))}
                return {"ok": True}
            jobs = {
                "api/job/connect": ("connect", app.job_connect),
                "api/job/backup": ("backup", app.job_backup),
                "api/job/install": ("install", app.job_install),
                "api/job/remove": (
                    "remove", lambda: app.job_remove(effect_names(data))),
                "api/job/remove_stock": (
                    "remove_stock",
                    lambda: app.job_remove(effect_names(data), stock=True)),
                "api/job/restore_stock": (
                    "restore_stock",
                    lambda: app.job_restore_stock(effect_names(data))),
                "api/job/rescue": ("rescue", app.job_rescue),
                "api/job/restore": (
                    "restore", lambda: app.job_rescue(restore=True)),
            }
            if path in jobs:
                name, func = jobs[path]
                return {"ok": app.start_job(name, func)}
            return {"ok": False}

    return Handler


# --------------------------------------------------------- window / browser

def open_in_browser(url):
    """An app-style window without tabs and address bar where a Chromium
    browser offers one (Edge is part of Windows), else a normal tab."""
    size = "--window-size=1040,880"
    try:
        if sys.platform == "win32":
            for var in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
                exe = os.path.join(os.environ.get(var, ""), "Microsoft",
                                   "Edge", "Application", "msedge.exe")
                if os.path.isfile(exe):
                    subprocess.Popen([exe, "--app=" + url, size])
                    return
        elif sys.platform == "darwin":
            chrome = "/Applications/Google Chrome.app"
            if os.path.isdir(chrome):
                subprocess.Popen(["/usr/bin/open", "-na", chrome, "--args",
                                  "--app=" + url, size])
                return
    except OSError:
        pass
    webbrowser.open(url)


def run_window(app, url):
    """Native window (pywebview). False if that is not available."""
    try:
        import webview
    except Exception:
        return False

    def closing(*args):
        if app.job_running():
            with app.lock:
                waiting = app.question is not None
            app.notice = (
                "The program is waiting for your answer. Answer the question "
                "first - it comes back when you close this note."
                if waiting else
                "A transfer is running. The window stays open until it has "
                "finished.")
            return False
        return True

    app.window = webview.create_window(
        APP_NAME, url, width=1040, height=880, min_size=(780, 600))
    app.window.events.closing += closing
    hook = os.environ.get("ZOOM_MS_GUI_SELFCHECK")
    if hook:
        webview.start(lambda: selfcheck(app, hook))
    else:
        webview.start()
    return True


def selfcheck(app, out_path):
    """Build check: is the page really alive inside the native window? Asks
    the page a few things through the window itself, writes the answers to
    `out_path` and closes the window."""
    result = {}
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            ready = app.window.evaluate_js(
                "document.readyState === 'complete' && "
                "typeof window.zoomUiReady !== 'undefined'")
            if ready:
                break
            time.sleep(0.3)
        time.sleep(1.5)
        result = app.window.evaluate_js(
            "({title: document.title,"
            " ready: window.zoomUiReady === true,"
            " polls: window.zoomPolls || 0,"
            " connectButton: !!document.getElementById('btnConnect'),"
            " native: window.zoomState ? window.zoomState.native : null,"
            " version: window.zoomState ? window.zoomState.version : null,"
            " width: window.innerWidth, height: window.innerHeight,"
            " agent: navigator.userAgent})")
        # MIDI: only LIST the ports (nothing is opened or sent).
        result["midi_ports"] = [pair[0] for pair in m.find_ports()]
        result["stock_lists"] = sorted(
            f for f in os.listdir(m.STOCK_ROOT)
            if m.stock_catalog(f))
        result["blank_icon"] = os.path.isfile(os.path.join(m.HERE,
                                                           "BLANK.ZIC"))
    except Exception as e:
        result = dict(result or {},
                      error="%s: %s" % (type(e).__name__, e))
    with open(out_path, "w") as f:
        json.dump(result, f, indent=1)
    app.window.destroy()


def wait_for_page_gone(app, server, url=None):
    """Browser mode: the program ends when its page has been closed. If the
    red bar is up then (a transfer could not be verified), the page is
    opened once more, so that it is seen before the pedal is switched off."""
    reopened = False
    while True:
        time.sleep(1.0)
        if app.job_running():
            continue
        now = time.monotonic()
        with app.lock:
            said_bye = app.bye_at is not None and now - app.bye_at > 3.0
            silent = now - app.last_seen > IDLE_EXIT
            alarm = app.unresolved
        if not alarm:
            reopened = False           # a new alarm gets its own reopen
        if (said_bye or silent) and alarm and url and not reopened:
            reopened = True
            with app.lock:
                app.bye_at = None
                app.last_seen = now
            open_in_browser(url)
            continue
        if said_bye or silent:
            server.shutdown()
            return


def already_running(lock_path):
    """URL of a running instance of this program, or None. Two instances
    talking to the same pedal at once is exactly what must not happen."""
    try:
        with open(lock_path) as f:
            url = json.load(f)["url"]
        with urllib.request.urlopen(url + "api/ping", timeout=1.5) as r:
            if json.load(r).get("app") == APP_NAME:
                return url
    except Exception:
        pass
    return None


def make_demo_pedal(source):
    """A simulated pedal for trying the program without hardware: an empty
    argument gives a small made-up pedal, a backup's `files` folder gives a
    copy of a real one."""
    import collections
    try:
        import zoom_ms_selftest as selftest
    except ImportError:
        sys.exit("The demo needs zoom_ms_selftest.py; it is only available "
                 "when the program is run from its source folder.")
    if source:
        fs = collections.OrderedDict()
        for name in sorted(os.listdir(source)):
            with open(os.path.join(source, name), "rb") as f:
                fs[name] = f.read()
        selftest.m.stock_catalog = selftest._real_stock_catalog
    else:
        fs = selftest.stock_fs()
    pedal = selftest.MockPedal(fs)
    pedal.free = 2600 * 1024
    return pedal


def main(argv=None):
    parser = argparse.ArgumentParser(prog="zoom_ms_gui",
                                     description=APP_NAME + " (graphical)")
    parser.add_argument("--data", help="folder for Backups, Adapted and Logs "
                        "(default: '%s' in your home folder)" % APP_NAME)
    parser.add_argument("--browser", action="store_true",
                        help="use the browser instead of a native window")
    parser.add_argument("--demo", nargs="?", const="", default=None,
                        metavar="BACKUP_FILES_FOLDER",
                        help="use a simulated pedal (no hardware needed)")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-open", action="store_true",
                        help="do not open a window (for testing)")
    args = parser.parse_args(argv)

    if args.demo is not None and not args.data:
        # backups of a simulated pedal must never mix with real ones
        args.data = os.path.join(tempfile.gettempdir(), "zoom-ms-demo")
    data_root = os.path.abspath(args.data or default_data_root())
    try:
        os.makedirs(data_root, exist_ok=True)
        # it must also take files (read-only drive, permissions); "a"
        # keeps the address of another running instance
        open(os.path.join(data_root, "running.json"), "a").close()
    except OSError as e:
        # e.g. a link to a folder that was moved: say so instead of
        # closing without a word
        show_error("The folder for backups and logs cannot be used:\n%s\n\n"
                   "%s\n\nIf it is a link to a folder that was moved, point "
                   "it to the new place." % (data_root, e.strerror or e))
        return 1
    lock_path = os.path.join(data_root, "running.json")
    other = already_running(lock_path)
    if other and not args.no_open:
        open_in_browser(other)
        return 0

    m.set_data_root(data_root)
    m.open_log("gui")
    demo = make_demo_pedal(args.demo) if args.demo is not None else None
    app = App(data_root, demo)
    m.ui = app
    with open(os.path.join(m.HERE, "zoom_ms_gui.html"), "rb") as f:
        page = f.read()
    token = secrets.token_urlsafe(16)
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 make_handler(app, token, page))
    server.daemon_threads = True
    url = "http://127.0.0.1:%d/%s/" % (server.server_address[1], token)
    with open(lock_path, "w") as f:
        json.dump({"url": url, "pid": os.getpid()}, f)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    m.say("%s %s%s" % (APP_NAME, VERSION,
                       " - DEMO with a simulated pedal" if demo else ""))
    m.say("Backups, adapted copies and logs are kept in: %s" % data_root)
    try:
        if args.no_open:
            print(url)
            sys.stdout.flush()
            wait_for_page_gone(app, server)
        elif args.browser or not run_window(app, url):
            open_in_browser(url)
            wait_for_page_gone(app, server, url)
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
        try:
            os.remove(lock_path)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
