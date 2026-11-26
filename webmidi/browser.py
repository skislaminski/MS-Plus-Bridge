"""The bridge in a phone's browser: Chrome on Android, the pedal on USB-OTG, no computer.

Runs in Pyodide (webmidi/index.html), which puts the program's files beside this one. MIDI goes
through Web MIDI (mido_webmidi.py); the interface, ui.html unchanged, gets its answers from
ui.answer through the page instead of over HTTP. The settings are those of the profile "phone",
which the page keeps in the browser's storage. What probe.py setup asks in a terminal, the page
asks: the approvals, then the pedal's device ID from its identity.
"""
import js
import mido
from pyodide.ffi import to_js

import bridge
import probe
import ui
import zoom_sysex as zs

mido.set_backend("mido_webmidi")
bridge.use_profile("phone")
probe.CONFIG, probe.APPROVALS, probe.MEASUREMENTS = bridge.CONFIG, bridge.APPROVALS, bridge.MEASUREMENTS
running = None


def approvals() -> dict:
    """What is approved, for the page's form: the backup, the kinds, which of them the bridge needs."""
    given = zs.Approvals(bridge.APPROVALS)
    return to_js({"backup": given.backup_confirmed, "kinds": zs.KINDS, "labels": zs.KIND_LABELS,
                  "required": list(bridge.REQUIRED_KINDS),
                  "approved": [kind for kind in zs.KINDS if given.is_approved(kind)]},
                 dict_converter=js.Object.fromEntries)


def approve(backup: bool, kinds) -> None:
    given = zs.Approvals(bridge.APPROVALS)
    if backup:
        given.confirm_backup()
    for kind in kinds:
        given.approve(kind)


def start():
    """config.yaml from the example and the device ID from the pedal, if it has none yet; then the
    bridge. Called through callPromising (its waits for the pedal sleep). Returns why it cannot
    start, or None."""
    global running
    try:
        if probe.load_config()["zoom"]["device_id"] is None:
            probe.cmd_identity(None)
        running = bridge.Bridge()
    except (SystemExit, zs.PortError, zs.NotAllowed, zs.NotApproved) as error:
        return str(error)
    return None


def run() -> None:
    """The bridge's loop, for good; through callPromising, so that its time.sleep lets the page run."""
    running.run()


def request(method: str, target: str, body: str):
    """A request of ui.html → [status, body, content type, file name, Cache-Control] (ui.answer)."""
    status, data, content_type, filename, cache = ui.answer(running, method, target, body.encode("utf-8"), here=True)
    return to_js([status, to_js(memoryview(data)), content_type, filename, cache])

