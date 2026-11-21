"""TONE3000 in the workshop (tone3000.py) against a stand-in for its API.

The stand-in follows the published API: OAuth 2 authorization code with PKCE (S256), the Select
flow sending back a tone_id, tones and their models, and downloads that need the Bearer token. It
checks what a real server would refuse: a wrong verifier, a wrong redirect_uri, a stale refresh
token (answered as the real one does). Every request goes through the workshop's own address, as
the page and the window would."""
import base64
import hashlib
import http.client
import json
import os
import ssl
import stat
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import bridge
import tone3000
import workshop
from tests.sim import SimulatedCase

KEY = tone3000.KEY                    # built in: nobody enters a key
CAPTURE, CAB = b'{"architecture": "SlimmableContainer", "sample_rate": 48000}', b"RIFF\x24\x00\x00\x00WAVEfmt "


class Stand_in:
    """TONE3000's API as far as the workshop uses it; a CDN beside it on another address."""

    def __init__(self) -> None:
        self.codes, self.authorized, self.cdn_headers, self.agents, self.listed = {}, [], [], [], []
        self.cut = False                    # the CDN breaks off a download
        self.access, self.refresh, self.issued, self.expires_in = None, None, 0, 3600
        self.pick = "42"
        owner = self

        class CDN(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.cdn_headers.append(dict(self.headers))
                if self.headers.get("User-Agent", "").startswith("Python-urllib"):    # as files.tone3000.com does
                    return self.send_error(403, "error code: 1010")
                self.send_response(200)
                self.send_header("Content-Length", str(len(CAB) * (2 if owner.cut else 1)))
                self.end_headers()
                self.wfile.write(CAB)

        self.cdn = ThreadingHTTPServer(("127.0.0.1", 0), CDN)

        class API(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, body=b"", headers=None):
                body = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
                self.send_response(code)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def signed_in(self):
                return owner.access and self.headers.get("Authorization") == f"Bearer {owner.access}"

            def do_GET(self):
                owner.agents.append(self.headers.get("User-Agent", ""))
                address = urllib.parse.urlsplit(self.path)
                query = {key: values[0] for key, values in urllib.parse.parse_qs(address.query).items()}
                if address.path == "/api/v1/oauth/authorize":
                    owner.authorized.append(query)
                    if (query.get("client_id") != KEY or query.get("response_type") != "code"
                            or query.get("code_challenge_method") != "S256" or query.get("prompt") != "select_tone"
                            or not query.get("redirect_uri", "").startswith("http://127.0.0.1:")):
                        return self.reply(400, {"error": "invalid_request"})
                    code = f"code{len(owner.codes)}"
                    owner.codes[code] = (query["code_challenge"], query["redirect_uri"])
                    back = {"code": code, "state": query["state"], "tone_id": owner.pick}
                    return self.reply(302, headers={"Location": query["redirect_uri"] + "?" + urllib.parse.urlencode(back)})
                if not self.signed_in():
                    return self.reply(401, {"error": "unauthorized"})
                files = {"m1": ("Amp A2", "/files/m1.nam"), "m2": ("Clean/Crunch", "/files/m2"),
                         "c1": ("Cab 4x10 SM57", "/files/redirect.wav"), "x1": ("Elsewhere", "/elsewhere")}
                tones = {"42": ("Ampeg SVT", "t3k", ["m1", "m2"]), "43": ("Locked cab", "cc-by-nd", ["c1"]),
                         "44": ("Some cab", "cc-by", ["c1", "x1"])}
                if address.path.startswith("/api/v1/tones/"):
                    title, license, _ = tones[address.path.rsplit("/", 1)[1]]
                    return self.reply(200, {"id": 42, "title": title, "license": license, "gear": "amp", "format": "nam",
                                            "url": "https://www.tone3000.com/tones/42",
                                            "images": ["https://assets.tone3000.com/020cf6d1-e367-4e0d-a948-9bd992f85336.jpg"],
                                            "description": "Captured through the DI out, all knobs at noon.",
                                            "user": {"username": "someone", "url": "https://www.tone3000.com/someone",
                                                     "avatar_url": None}})
                if address.path == "/api/v1/models":
                    owner.listed.append(query)
                    base = f"http://127.0.0.1:{owner.api.server_address[1]}"
                    models = [{"id": model, "name": files[model][0], "size": None, "architecture_version": "2",
                               "model_url": ("https://other.example/x.wav" if model == "x1" else base + files[model][1])}
                              for model in tones[query["tone_id"]][2]]
                    return self.reply(200, {"data": models, "page": 1, "page_size": 100, "total": len(models)})
                if address.path == "/files/redirect.wav":       # the download handed to a CDN
                    return self.reply(302, headers={"Location": f"http://127.0.0.1:{owner.cdn.server_address[1]}/cab.wav"})
                if address.path.startswith("/files/"):
                    return self.reply(200, CAPTURE)
                self.reply(404)

            def do_POST(self):
                form = {key: values[0] for key, values in urllib.parse.parse_qs(
                    self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
                if form.get("client_id") != KEY:
                    return self.reply(401, {"error": "invalid_client"})
                if form.get("grant_type") == "authorization_code":
                    challenge, redirect_uri = owner.codes.pop(form.get("code"), (None, None))
                    verified = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode())
                                                        .digest()).decode().rstrip("=")
                    if challenge is None or verified != challenge or form.get("redirect_uri") != redirect_uri:
                        return self.reply(400, {"error": "invalid_grant"})
                elif form.get("grant_type") != "refresh_token":
                    return self.reply(400, {"error": "unsupported_grant_type"})
                elif form.get("refresh_token") != owner.refresh:          # what TONE3000 really answers
                    return self.reply(400, {"code": 400, "error_code": "refresh_token_not_found",
                                            "msg": "Invalid Refresh Token: Refresh Token Not Found"})
                owner.issued += 1
                owner.access, owner.refresh = f"access{owner.issued}", f"refresh{owner.issued}"
                self.reply(200, {"access_token": owner.access, "refresh_token": owner.refresh, "token_type": "bearer",
                                 "expires_in": owner.expires_in})

        self.api = ThreadingHTTPServer(("127.0.0.1", 0), API)
        for server in (self.api, self.cdn):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def close(self) -> None:
        for server in (self.api, self.cdn):
            server.shutdown()
            server.server_close()


def plain(url: str, method: str = "GET", body=None, headers=None) -> tuple:
    """One request without following a redirect: (status, headers, body)."""
    address = urllib.parse.urlsplit(url)
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=10)
    connection.request(method, address.path + ("?" + address.query if address.query else ""), body=body,
                       headers=headers or {})
    reply = connection.getresponse()
    result = reply.status, dict(reply.getheaders()), reply.read()
    connection.close()
    return result


class TONE3000(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.t3k = Stand_in()
        self.addCleanup(self.t3k.close)
        patcher = mock.patch.object(tone3000, "BASE", f"http://127.0.0.1:{self.t3k.api.server_address[1]}")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = workshop.start(str(bridge.INSTALLER_DATA))
        self.addCleanup(self.app.stop)

    def call(self, path: str, body=None) -> tuple:
        status, headers, raw = plain(self.app.url + path, "POST" if body is not None else "GET",
                                     None if body is None else json.dumps(body).encode())
        return status, headers, (json.loads(raw) if headers.get("Content-Type", "").startswith("application/json") else raw)

    def sign_in(self, kind: str = "nam") -> dict:
        """The click on the page, TONE3000's window and its way back, as a browser goes them."""
        status, headers, _ = self.call(f"t3k/start?kind={kind}")
        self.assertEqual(status, 302)
        status, back, _ = plain(headers["Location"])                 # TONE3000: signed in, a tone picked
        self.assertEqual(status, 302)
        status, _, page = plain(back["Location"])                     # the window comes back to the workshop
        self.assertEqual(status, 200)
        return json.loads(page.decode().split("Object.assign(", 1)[1].split(", {type:", 1)[0])

    def test_signing_in_and_picking_a_capture(self):
        self.assertEqual(self.sign_in(), {"ok": True, "kind": "nam", "tone_id": "42"})
        asked = self.t3k.authorized[-1]
        self.assertEqual({key: asked[key] for key in ("prompt", "format", "architecture", "menubar")},
                         {"prompt": "select_tone", "format": "nam", "architecture": "2", "menubar": "true"})
        self.assertEqual(asked["redirect_uri"], f"http://127.0.0.1:{urllib.parse.urlsplit(self.app.url).port}/t3k/callback")
        self.assertEqual(self.call("t3k/status")[2], {"signed_in": True})
        store = bridge.INSTALLER_DATA / "tone3000.json"
        self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o600)
        self.assertEqual(json.loads(store.read_text()), {"refresh_token": "refresh1"})
        tone = self.call("t3k/tone?id=42&kind=nam")[2]
        self.assertEqual((tone["tone"]["tone"], tone["tone"]["creator"], tone["tone"]["license"], tone["tone"]["changes_allowed"]),
                         ("Ampeg SVT", "someone", "t3k", True))
        # a "/" of TONE3000's names is no folder; an address without an extension gets the kind's own
        self.assertEqual([model["name"] for model in tone["models"]], ["Amp A2.nam", "Clean-Crunch.nam"])
        self.assertNotIn("model_url", json.dumps(tone))                # the files' addresses stay in the workshop
        self.assertEqual(self.t3k.listed[-1]["page_size"], "300")      # the most one page gives
        status, headers, data = self.call("t3k/file?model=m1")
        self.assertEqual((status, data, urllib.parse.unquote(headers["X-File-Name"])), (200, CAPTURE, "Amp A2.nam"))
        credit = json.loads(urllib.parse.unquote(headers["X-Credit"]))
        self.assertEqual((credit["creator"], credit["license"], credit["model"]), ("someone", "t3k", "Amp A2"))

    def test_cab_irs_are_asked_for_as_cabs(self):
        self.t3k.pick = "44"
        self.assertEqual(self.sign_in("ir")["tone_id"], "44")
        asked = self.t3k.authorized[-1]
        self.assertEqual((asked["format"], asked["gears"], "architecture" in asked), ("ir", "cab", False))

    def test_the_token_goes_to_tone3000_only(self):
        self.t3k.pick = "44"
        self.sign_in("ir")
        self.call("t3k/tone?id=44&kind=ir")
        status, _, data = self.call("t3k/file?model=c1")              # TONE3000 hands the download to a CDN
        self.assertEqual((status, data), (200, CAB))
        self.assertNotIn("Authorization", self.t3k.cdn_headers[-1])
        self.assertFalse([agent for agent in self.t3k.agents if agent.startswith("Python-urllib")])
        self.assertEqual(self.call("t3k/file?model=x1")[2], {"ok": False, "error": "This file is not on TONE3000."})
        self.t3k.cut = True                                            # broken off on the way
        self.assertIn("cut off", self.call("t3k/file?model=c1")[2]["error"])

    def test_a_tone_whose_license_allows_no_changes(self):
        self.t3k.pick = "43"
        self.sign_in("ir")
        tone = self.call("t3k/tone?id=43&kind=ir")[2]
        self.assertFalse(tone["tone"]["changes_allowed"])
        status, _, refused = self.call("t3k/file?model=c1")
        self.assertEqual(status, 400)
        self.assertIn("(CC-BY-ND) allows no changes", refused["error"])
        self.assertEqual(self.t3k.cdn_headers, [])                     # nothing was fetched

    def test_a_sign_in_is_taken_once_and_only_when_started_here(self):
        _, headers, _ = self.call("t3k/start?kind=nam")
        _, back, _ = plain(headers["Location"])
        self.assertIn(b'"ok": true', plain(back["Location"])[2])
        for again in (back["Location"], back["Location"].replace("state=", "state=x")):
            self.assertIn(b"This sign-in is not known", plain(again)[2])

    def test_the_access_is_renewed_and_ends_with_signing_out(self):
        self.sign_in()
        self.app.t3k.expires = 0                                       # the access ran out: renewed once, then used
        self.call("t3k/tone?id=42&kind=nam")
        self.assertEqual(self.t3k.issued, 2)                           # the sign-in, then one renewal
        self.t3k.refresh = "revoked"                                   # TONE3000 ended the access
        self.app.t3k.expires = 0                                       # noticed at the next renewal
        self.assertEqual(self.call("t3k/tone?id=42&kind=nam")[:3:2], (401, {"ok": False, "error": "Sign in to TONE3000 again."}))
        self.assertEqual(self.call("t3k/status")[2], {"signed_in": False})
        self.sign_in()
        self.assertEqual(self.call("t3k/signout", {})[2], {"ok": True})
        self.assertEqual(json.loads((bridge.INSTALLER_DATA / "tone3000.json").read_text()), {})

    def callback(self, **answer) -> dict:
        """TONE3000's window back at the workshop with these parameters, for a sign-in started here."""
        _, headers, _ = self.call("t3k/start?kind=ir")
        state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(headers["Location"]).query))["state"]
        port = urllib.parse.urlsplit(self.app.url).port
        _, _, page = plain(f"http://127.0.0.1:{port}/t3k/callback?" + urllib.parse.urlencode(dict(answer, state=state)))
        return json.loads(page.decode().split("Object.assign(", 1)[1].split(", {type:", 1)[0])

    def test_what_comes_back_without_a_pick_reaches_the_tab_that_asked(self):
        self.assertEqual(self.callback(error="access_denied"),
                         {"ok": False, "kind": "ir", "error": "TONE3000 says: access_denied"})
        self.assertEqual(self.callback(error="access_denied", error_description="You said no.")["error"],
                         "TONE3000 says: You said no.")
        self.assertEqual(self.callback(canceled="true"),                  # closed before signing in
                         {"ok": False, "kind": "ir", "canceled": True, "error": "Nothing was picked."})
        self.assertEqual(self.callback(code="unknown"),                   # a code TONE3000 does not take
                         {"ok": False, "kind": "ir", "error": "TONE3000 did not sign you in: start it again."})

    def test_a_cancel_after_signing_in_keeps_the_sign_in(self):
        _, headers, _ = self.call("t3k/start?kind=nam")
        _, back, _ = plain(headers["Location"])                       # TONE3000 signed in; then × instead of a pick
        location = back["Location"].replace("tone_id=42", "canceled=true")
        self.assertIn(b'"canceled": true', plain(location)[2])
        self.assertEqual(self.call("t3k/status")[2], {"signed_in": True})

    def test_both_tabs_keep_the_files_of_their_tones(self):
        self.sign_in()
        self.call("t3k/tone?id=42&kind=nam")                          # the NAM tab
        self.call("t3k/tone?id=44&kind=ir")                           # then the IR Loader
        self.assertEqual(self.call("t3k/file?model=m1")[0], 200)      # the NAM tab's Fetch still works

    def test_without_a_network(self):
        self.sign_in()
        self.t3k.close()
        self.app.t3k.expires = 0
        status, _, reply = self.call("t3k/tone?id=42&kind=nam")
        self.assertEqual(status, 400)
        self.assertIn("TONE3000 cannot be reached", reply["error"])
        self.assertEqual(self.call("t3k/status")[2], {"signed_in": True})   # no network is no sign-out

    def test_the_pages_may_show_tone3000_pictures(self):
        for page in ("nam", "ir-loader"):
            headers = self.call(page)[1]
            self.assertIn(workshop.T3K_IMAGES, headers["Content-Security-Policy"].split("img-src", 1)[1])

    def test_the_key_is_built_in(self):
        status, headers, _ = self.call("t3k/start?kind=nam")         # nothing entered before
        self.assertEqual(status, 302)
        asked = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(headers["Location"]).query))
        self.assertTrue(asked["client_id"].startswith("t3k_pub_"))  # a publishable key, never a secret one


@unittest.skipUnless(sys.platform == "darwin", "the certificates of macOS")
class Certificates(unittest.TestCase):
    def test_a_python_without_certificates_of_its_own_takes_those_of_macos(self):
        none = ssl.DefaultVerifyPaths(None, None, "SSL_CERT_FILE", "", "SSL_CERT_DIR", "")
        with mock.patch.object(tone3000, "_tls", None), \
                mock.patch.object(tone3000.ssl, "get_default_verify_paths", return_value=none):
            self.assertGreater(tone3000.tls().cert_store_stats()["x509_ca"], 50)


if __name__ == "__main__":
    unittest.main()
