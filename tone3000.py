"""TONE3000 (https://www.tone3000.com) in the NAM and IR Loader tabs of `bridge.py --workshop`.

You pick a tone in TONE3000's own window ("Select"), and the bridge fetches what you picked:
NAM captures to train for an N2Z Bank, cab IRs for the IR Loader. It uses the public API (v1,
still marked beta) with OAuth 2 authorization code + PKCE as a public client, with this program's
own publishable key (KEY, the client_id): public by design, so nobody needs a key of their own,
and a secret key is never used.

As the API's terms ask:
- Only what you pick is fetched, nothing is cached.
- No file is passed on, and every file keeps its creator and license.
- Tones whose license allows no changes (CC BY-ND) are neither trained nor fitted.

The sign-in is kept in the installer's folder (tone3000.json, readable by you only) until you sign
out. "Powered by TONE3000" is shown where the tabs offer it."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import secrets
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

BASE = "https://www.tone3000.com"
KEY = "t3k_pub_W7b0r7AWIiR_ENDji3hCKwdnnJNwbopE"   # this program's publishable key (TONE3000: "safe to include in client-side code")
NO_CHANGES = {"cc-by-nd", "cc-by-nc-nd"}        # training a capture or fitting an IR is a change
STATE_SECONDS = 3600                             # a sign-in (email, code, browsing, listening) not finished by then is forgotten
EARLY = 60                                       # refresh the access token this long before it ends
MAX_FILE = 64 * 1024 * 1024                      # nam2zoom's own limit for a NAM to train
PAGE_SIZE = 300                                  # the most /models gives in one page
MAX_KNOWN = 1000                                 # files of tones looked at, kept by model id (addresses only)
# files.tone3000.com refuses urllib's own "Python-urllib/3.x" (403, error code 1010)
HEADERS = {"User-Agent": "MS-Plus-Bridge (+https://www.tone3000.com/api)"}
KINDS = {"nam": {"format": "nam", "architecture": "2"}, "ir": {"format": "ir", "gears": "cab"}}


class NotSignedIn(Exception):
    pass


_tls: Optional[ssl.SSLContext] = None


def tls() -> ssl.SSLContext:
    """How TONE3000's certificate is checked. python.org's Python on macOS has no certificates of its
    own until its "Install Certificates.command" has run; until then those macOS trusts are taken."""
    global _tls
    if _tls is None:
        context, paths = ssl.create_default_context(), ssl.get_default_verify_paths()
        if sys.platform == "darwin" and not (paths.cafile or paths.capath):
            try:
                roots = subprocess.run(["/usr/bin/security", "find-certificate", "-a", "-p",
                                        "/System/Library/Keychains/SystemRootCertificates.keychain",
                                        "/Library/Keychains/System.keychain"],
                                       capture_output=True, text=True, timeout=30).stdout
            except (OSError, subprocess.SubprocessError):
                roots = ""
            end = "-----END CERTIFICATE-----"
            for pem in roots.split(end)[:-1]:            # one by one: an odd one does not cost the others
                try:
                    context.load_verify_locations(cadata=pem + end + "\n")
                except (ssl.SSLError, ValueError):
                    pass
        _tls = context
    return _tls


def _fetch(request: urllib.request.Request, seconds: float, most: Optional[int] = None) -> bytes:
    """The body of one answer (at most most + 1 bytes). An HTTP error comes as it is; a network that
    fails, and a download cut off on the way, say so in words."""
    try:
        with urllib.request.urlopen(request, timeout=seconds, context=tls()) as reply:
            data = reply.read() if most is None else reply.read(most + 1)
            if most is not None and len(data) <= most and reply.length:     # less than its Content-Length
                raise ValueError("The download was cut off on the way: fetch it again.")
            return data
    except urllib.error.HTTPError:
        raise
    except http.client.IncompleteRead as error:          # ended before its Content-Length or last chunk
        raise ValueError("The download was cut off on the way: fetch it again.") from error
    except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
        reason = getattr(error, "reason", None) or error
        raise ValueError(f"TONE3000 cannot be reached ({reason}): is this computer online?") from error


def _name(model: dict, url: str, kind: str) -> str:
    """A file name for a model: its name on TONE3000 (where "/" is common: "Clean/Crunch") and the
    file's extension, or the kind's own."""
    base = str(model.get("name") or "model").replace("/", "-").replace("\\", "-").strip(" .") or "model"
    return base + (os.path.splitext(urllib.parse.urlsplit(url).path)[1] or (".nam" if kind == "nam" else ".wav"))


class Tone3000:
    def __init__(self, store: Path) -> None:
        self.store = Path(store)
        self.lock = threading.RLock()
        self.pending: dict = {}           # state -> (verifier, redirect_uri, kind, when)
        self.access: Optional[str] = None
        self.expires = self.margin = 0.0
        self.files: dict = {}             # model id -> (url, name, credit) of the tones looked at (both tabs)

    # --- the store: the refresh token ---

    def _saved(self) -> dict:
        try:
            saved = json.loads(self.store.read_text(encoding="utf-8"))
            return saved if isinstance(saved, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, **changes) -> None:
        saved = dict(self._saved(), **changes)
        saved = {key: value for key, value in saved.items() if value is not None}
        temporary = self.store.with_suffix(".tmp")
        with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            json.dump(saved, f)
        os.replace(temporary, self.store)

    def status(self) -> dict:
        return {"signed_in": bool(self._saved().get("refresh_token"))}

    def sign_out(self) -> None:
        """Forgets the sign-in (what the terms ask when you end the access)."""
        with self.lock:
            self.access, self.expires = None, 0.0
            self._save(refresh_token=None)

    # --- signing in and picking: TONE3000's own window ---

    def start(self, kind: str, redirect_uri: str) -> str:
        """The address of TONE3000's sign-in and pick window for kind "nam" (A2 captures) or "ir" (cab IRs)."""
        if kind not in KINDS:
            raise ValueError("Unknown kind of tone.")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(32)
        with self.lock:
            now = time.monotonic()
            self.pending = {name: value for name, value in self.pending.items() if now - value[3] < STATE_SECONDS}
            self.pending[state] = (verifier, redirect_uri, kind, now)
        query = dict(client_id=KEY, redirect_uri=redirect_uri, response_type="code", state=state,
                     code_challenge=challenge, code_challenge_method="S256", prompt="select_tone",
                     menubar="true", preview="true", **KINDS[kind])
        return f"{BASE}/api/v1/oauth/authorize?{urllib.parse.urlencode(query)}"

    def callback(self, query: dict) -> dict:
        """TONE3000 sends the window back here: a code for the tokens, and the tone that was picked."""
        with self.lock:
            pending = self.pending.pop(str(query.get("state", "")), None)
        if pending is None or time.monotonic() - pending[3] >= STATE_SECONDS:
            return {"ok": False, "error": "This sign-in is not known (or too old): start it again."}
        verifier, redirect_uri, kind, _ = pending
        if query.get("error"):
            return {"ok": False, "kind": kind,
                    "error": f"TONE3000 says: {query.get('error_description') or query['error']}"}
        if query.get("code"):                             # also with canceled: the sign-in is kept
            try:
                self._take(self._post_token(grant_type="authorization_code", code=query["code"],
                                            code_verifier=verifier, redirect_uri=redirect_uri))
            except (ValueError, KeyError, OSError, NotSignedIn) as error:
                return {"ok": False, "kind": kind, "error": str(error) or "TONE3000 did not sign you in: start it again."}
        if query.get("canceled") == "true" or not query.get("tone_id"):
            return {"ok": False, "kind": kind, "canceled": True, "error": "Nothing was picked."}
        return {"ok": True, "kind": kind, "tone_id": str(query["tone_id"])}

    # --- tokens ---

    def _post_token(self, **fields) -> dict:
        body = urllib.parse.urlencode(dict(fields, client_id=KEY)).encode()
        request = urllib.request.Request(f"{BASE}/api/v1/oauth/token", data=body,
                                         headers=dict(HEADERS, **{"Content-Type": "application/x-www-form-urlencoded"}))
        try:
            return json.loads(_fetch(request, 30))
        except urllib.error.HTTPError as error:
            with error:
                answer = error.read().decode("utf-8", "replace")
            # a refresh token that ended: its docs say invalid_grant, TONE3000 also answers
            # {"error_code": "refresh_token_not_found", …}; either way only a new sign-in helps
            if error.code in (400, 401) and (fields.get("grant_type") == "refresh_token" or "invalid_grant" in answer):
                raise NotSignedIn from error
            raise ValueError(f"TONE3000 did not sign you in (HTTP {error.code}).") from error

    def _take(self, tokens: dict) -> None:
        with self.lock:
            lifetime = float(tokens.get("expires_in") or 3600)
            self.access = tokens["access_token"]
            self.expires, self.margin = time.monotonic() + lifetime, min(EARLY, lifetime / 2)
            if tokens.get("refresh_token"):
                self._save(refresh_token=tokens["refresh_token"])

    def _token(self, again: bool = False) -> str:
        with self.lock:                                      # one refresh at a time
            if self.access and not again and time.monotonic() < self.expires - self.margin:
                return self.access
            refresh = self._saved().get("refresh_token")
            if not refresh:
                raise NotSignedIn
            try:
                self._take(self._post_token(grant_type="refresh_token", refresh_token=refresh))
            except NotSignedIn:
                self.sign_out()
                raise
            return self.access

    def _get(self, path: str, query: dict) -> dict:
        for again in (False, True):                          # a 401: the token once more, then give up
            request = urllib.request.Request(f"{BASE}{path}?{urllib.parse.urlencode(query)}", headers=HEADERS)
            request.add_unredirected_header("Authorization", f"Bearer {self._token(again)}")
            try:
                return json.loads(_fetch(request, 30))
            except urllib.error.HTTPError as error:
                error.close()
                if error.code == 401 and not again:
                    continue
                raise ValueError({429: "TONE3000 asks to wait a moment (too many requests).",
                                  404: "TONE3000 does not have that tone (any more)."}
                                 .get(error.code, f"TONE3000 answered HTTP {error.code}.")) from error
        raise NotSignedIn

    # --- the tone that was picked, and its files ---

    def tone(self, tone_id: str, kind: str) -> dict:
        """The tone and its models, as far as the page shows them; the files' addresses stay here."""
        if kind not in KINDS or not str(tone_id).isdigit():
            raise ValueError("Unknown tone.")
        extra = {"architecture": "2"} if kind == "nam" else {}
        tone = self._get(f"/api/v1/tones/{tone_id}", extra)
        listed = self._get("/api/v1/models", dict(extra, tone_id=tone_id, page_size=PAGE_SIZE)).get("data", [])
        user, images = tone.get("user") or {}, tone.get("images") or []
        credit = {"tone": tone.get("title"), "tone_url": tone.get("url"), "tone_id": tone.get("id"),
                  "creator": user.get("username"), "creator_url": user.get("url"), "license": tone.get("license"),
                  "gear": tone.get("gear"), "format": tone.get("format"),
                  "image": images[0] if images and isinstance(images[0], str) else None,
                  "source": "TONE3000 (https://www.tone3000.com)"}
        models = []
        with self.lock:
            if len(self.files) > MAX_KNOWN:
                self.files.clear()
            for model in listed:
                url = str(model.get("model_url") or "")
                name = _name(model, url, kind)
                self.files[str(model.get("id"))] = (url, name, dict(credit, model=model.get("name"), model_id=model.get("id")))
                models.append({"id": str(model.get("id")), "name": name, "size": model.get("size"),
                               "architecture": model.get("architecture_version")})
        return {"tone": dict(credit, avatar=user.get("avatar_url"), description=tone.get("description"),
                             changes_allowed=tone.get("license") not in NO_CHANGES),
                "models": models}

    def file(self, model_id: str) -> tuple:
        """(file name, bytes, credit) of one model of a tone looked at. Refused when its license
        allows no changes: both uses here change it (training a capture, fitting an IR)."""
        with self.lock:
            found = self.files.get(str(model_id))
        if found is None:
            raise ValueError("Pick the tone again: this file is not known.")
        url, name, credit = found
        if credit.get("license") in NO_CHANGES:
            raise ValueError(f"The license of this tone ({credit['license'].upper()}) allows no changes, and "
                             "training a capture or fitting an IR changes it.")
        if urllib.parse.urlsplit(url).netloc != urllib.parse.urlsplit(BASE).netloc:
            raise ValueError("This file is not on TONE3000.")         # the token goes to TONE3000 only
        for again in (False, True):
            request = urllib.request.Request(url, headers=HEADERS)
            # not passed on when TONE3000 sends the download elsewhere (files.tone3000.com)
            request.add_unredirected_header("Authorization", f"Bearer {self._token(again)}")
            try:
                data = _fetch(request, 60, MAX_FILE)
            except urllib.error.HTTPError as error:
                error.close()
                if error.code == 401 and not again:
                    continue
                raise ValueError(f"TONE3000 did not give the file (HTTP {error.code}).") from error
            if len(data) > MAX_FILE:
                raise ValueError("The file is larger than 64 MiB.")
            return name, data, credit
        raise NotSignedIn


def callback_page(result: dict) -> bytes:
    """The window TONE3000 sends back: it tells the tab that opened it what was picked, and closes."""
    message = json.dumps(result).replace("<", "\\u003c")       # no way out of the script
    return ("<!doctype html><html lang='en'><meta charset='utf-8'><title>TONE3000</title>"
            "<body style='font:14px -apple-system,system-ui,sans-serif;margin:2em'><p id='m'></p><script>"
            f"const message = Object.assign({message}, {{type: 't3k'}});"
            "try { if (window.opener) window.opener.postMessage(message, location.origin);"
            " else new BroadcastChannel('t3k').postMessage(message); } catch (e) {}"   # an opener lost on the way
            "document.getElementById('m').textContent = message.ok ? 'Picked: this window closes.'"
            " : message.canceled ? 'Nothing was picked: this window closes.' : (message.error || 'Nothing was picked.');"
            "if (message.ok || message.canceled) setTimeout(() => window.close(), 500);"
            "</script></body></html>").encode("utf-8")
