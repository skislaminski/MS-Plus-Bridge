"""pi/install.sh, the setup of a Raspberry Pi, run against a stand-in for the Pi's system.

The commands it calls (apt-get, systemctl, getent …) are small scripts here that log their
arguments; EXPRESSION_BRIDGE_TARGET puts everything it writes into a folder instead of /etc,
/var/lib, /media and /sys. That shows the way through the script and what it leaves behind. How
the real Raspberry Pi OS takes it is only seen on a Pi.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bridge
import config_schema as cs
import export

ALL = "python3-mido python3-rtmidi python3-yaml bluez"   # every package missing: a fresh Pi
STUBS = {
    "id": 'echo 0',
    "getent": '[ -n "${OFFLINE:-}" ] && exit 2; echo "151.101.2.132 deb.debian.org"',
    "timedatectl": 'echo yes',
    "apt-get": 'echo "apt-get $*" >> "$CALLS"; [ -n "${APT_FAILS:-}" ] && [[ " $* " == *" install "* ]] && exit 100; exit 0',
    # the Pi's own WLAN as the settings file gives it (WLAN="name/password", its fingerprint in
    # WLAN_PRINT), for install.sh's reading of it
    "python3": '[ "${1:-}" = - ] && [ -n "${WLAN:-}" ] && printf "%s\\n%s\\n%s\\n" "${WLAN%%/*}" "${WLAN#*/}" "$WLAN_PRINT"; '
               'exit 0',
    # installed: every package except those in MISSING; then the versions
    "dpkg-query": 'if [[ "$*" == *Status* ]]; then [[ " ${MISSING:-} " == *" ${!#} "* ]] && exit 1; echo "install ok installed"; '
                  'else printf "python3-mido 1.3.3-0.1\\npython3-rtmidi 1.5.8-3+b3\\npython3-yaml 6.0.2-1+b2\\nbluez 5.82-1\\n"; fi',
    "nmcli": 'echo "nmcli $*" >> "$CALLS"',
    "rfkill": 'exit 0',
    "systemctl": 'echo "systemctl $*" >> "$CALLS"; exit 0',
    "udevadm": 'echo "udevadm $*" >> "$CALLS"',
    "modprobe": 'echo "modprobe $*" >> "$CALLS"',
    "logger": 'exit 0',
    "sleep": 'exit 0',
}


class Install(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp()).resolve()          # macOS: /var is /private/var
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.boot, self.system, bin_folder = self.folder / "bootfs", self.folder / "system", self.folder / "bin"
        for folder in (self.boot, self.system / "sys/class/leds/ACT", bin_folder):
            folder.mkdir(parents=True)
        (self.boot / "config.txt").write_text("# the Pi's own file\n", encoding="utf-8")
        export.write_program(self.boot)
        self.led = self.system / "sys/class/leds/ACT"
        (self.led / "trigger").write_text("none [mmc0] heartbeat timer\n", encoding="utf-8")
        for name, body in STUBS.items():
            (bin_folder / name).write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
            (bin_folder / name).chmod(0o755)
        self.calls = self.folder / "calls.txt"
        self.env = dict(os.environ, PATH=f"{bin_folder}:{os.environ['PATH']}", CALLS=str(self.calls),
                        EXPRESSION_BRIDGE_TARGET=str(self.system))

    def install(self, **env) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(self.boot / export.PROGRAM_FOLDER / "pi/install.sh")],
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=60)

    def report(self) -> str:
        return (self.boot / "expression-bridge-install.txt").read_text(encoding="utf-8")

    def test_it_installs_the_packages_and_the_service_that_runs_the_bridge_from_the_card(self):
        done = self.install(MISSING=ALL)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        service = (self.system / "etc/systemd/system/expression-bridge.service").read_text(encoding="utf-8")
        self.assertIn(f"ExecStart=/usr/bin/python3 {self.boot}/expression-bridge/bridge.py --settings {self.boot} "
                      "--stick /media/stick --listen", service)
        self.assertIn(f"ReadWritePaths={self.boot}", service)
        self.assertNotIn("@BOOT@", service)
        setup = (self.system / "etc/systemd/system/expression-bridge-setup.service").read_text(encoding="utf-8")
        self.assertIn(f"ExecStart=/bin/bash {self.boot}/expression-bridge/pi/install.sh", setup)
        bluetooth = (self.system / "etc/systemd/system/expression-bridge-bluetooth.service").read_text(encoding="utf-8")
        self.assertIn(f"ExecStart=/bin/bash {self.boot}/expression-bridge/pi/bluetooth-midi.sh", bluetooth)
        self.assertTrue((self.system / "etc/udev/rules.d/90-expression-bridge-stick.rules").exists())
        self.assertIn("Storage=volatile",
                      (self.system / "etc/systemd/journald.conf.d/expression-bridge.conf").read_text(encoding="utf-8"))
        self.assertTrue((self.system / "media/stick").is_dir())
        self.assertEqual((self.system / "etc/modules-load.d/expression-bridge.conf").read_text(encoding="utf-8"),
                         "snd-seq\n")
        self.assertEqual(self.marker()[:2], ["level=2", "wlan="])
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn("install -y --no-install-recommends python3-mido python3-rtmidi python3-yaml bluez", calls)
        for wanted in ("systemctl enable expression-bridge-setup.service", "systemctl enable expression-bridge.service",
                       "systemctl disable expression-bridge-setup.service", "systemctl restart expression-bridge.service",
                       "systemctl enable expression-bridge-bluetooth.service", "systemctl disable --now apt-daily.timer"):
            self.assertIn(wanted, calls)
        self.assertLess(calls.index("enable expression-bridge-setup"), calls.index("apt-get -q -o DPkg::Lock::Timeout=600 update"))
        self.assertIn("Installed (level 2). The bridge runs", self.report())
        self.assertIn("Packages: python3-mido 1.3.3-0.1, python3-rtmidi 1.5.8-3+b3, python3-yaml 6.0.2-1+b2, bluez 5.82-1",
                      self.report())
        self.assertEqual((self.led / "trigger").read_text(encoding="utf-8").strip(), "mmc0")   # its old job back

        self.calls.write_text("", encoding="utf-8")
        again = self.install(OFFLINE="1")                        # all there: no internet needed, nothing loaded
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertNotIn("apt-get", self.calls.read_text(encoding="utf-8"))

    def test_only_the_packages_that_are_missing_are_installed(self):
        done = self.install(MISSING="bluez")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("apt-get -q -o DPkg::Lock::Timeout=600 install -y --no-install-recommends bluez\n",
                      self.calls.read_text(encoding="utf-8"))

    def test_without_internet_it_says_so_and_tries_again_at_the_next_start(self):
        done = self.install(OFFLINE="1", MISSING=ALL)
        self.assertEqual(done.returncode, 1)
        self.assertIn("NOT INSTALLED: no internet", self.report())
        self.assertIn("systemctl enable expression-bridge-setup.service", self.calls.read_text(encoding="utf-8"))
        self.assertFalse((self.system / "var/lib/expression-bridge/installed").exists())
        self.assertFalse((self.system / "etc/systemd/system/expression-bridge.service").exists())
        self.assertEqual((self.led / "trigger").read_text(encoding="utf-8").strip(), "timer")    # fast blinking
        self.assertEqual((self.led / "delay_on").read_text(encoding="utf-8").strip(), "100")

    def test_packages_that_cannot_be_installed_stop_it(self):
        done = self.install(APT_FAILS="1", MISSING=ALL)
        self.assertEqual(done.returncode, 1)
        self.assertIn("NOT INSTALLED: the packages could not be installed", self.report())
        self.assertFalse((self.system / "var/lib/expression-bridge/installed").exists())

    def test_the_pi_gets_its_own_wlan_from_the_settings_file(self):
        done = self.install(WLAN="MS Plus Bridge/kq7m2p9xwt", WLAN_PRINT="0123456789abcdef")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn("nmcli connection delete ms-plus-bridge-wlan", calls)
        self.assertIn("nmcli connection add type wifi ifname wlan0 con-name ms-plus-bridge-wlan autoconnect yes "
                      "connection.autoconnect-priority -10 ssid MS Plus Bridge 802-11-wireless.mode ap", calls)
        self.assertIn("ipv4.method shared ipv6.method disabled wifi-sec.key-mgmt wpa-psk wifi-sec.psk kq7m2p9xwt", calls)
        self.assertEqual(self.marker()[1], "wlan=0123456789abcdef")       # as config_schema gave it
        self.assertIn("Own WLAN: “MS Plus Bridge”", self.report())

    def test_a_wlan_that_cannot_be_set_up_is_noted_all_the_same(self):
        """Noted as not there, the bridge would run this again at every start, and this restarts it."""
        (self.folder / "bin" / "nmcli").write_text('#!/bin/bash\nexit 4\n', encoding="utf-8")
        done = self.install(WLAN="MS Plus Bridge/kq7m2p9xwt", WLAN_PRINT="0123456789abcdef")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.marker()[1], "wlan=0123456789abcdef")
        self.assertIn("could not be set up", self.report())

    def test_the_fingerprint_reads_what_install_sh_reads(self):
        """install.sh runs config_schema.wlan_fingerprint on the settings file, as the bridge does."""
        script = (export.PROJECT / "pi" / "install.sh").read_text(encoding="utf-8")
        self.assertIn("config_schema.wlan_fingerprint(settings)", script)
        self.assertIn("config_schema.load_with_fallback(", script)          # the file the bridge runs with
        self.assertIn("cs.wlan_fingerprint(settings)", (export.PROJECT / "bridge.py").read_text(encoding="utf-8"))

    def test_an_update_from_the_bridge_leaves_the_light_and_the_first_start_alone(self):
        done = self.install_update(OFFLINE="1", MISSING="bluez")
        self.assertEqual(done.returncode, 1)
        self.assertIn("Updating the MS Plus Bridge", self.report())
        self.assertEqual((self.led / "trigger").read_text(encoding="utf-8").strip(), "none [mmc0] heartbeat timer")
        self.assertNotIn("expression-bridge-setup", self.calls.read_text(encoding="utf-8") if self.calls.exists() else "")
        done = self.install_update()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.marker()[0], "level=2")

    def marker(self) -> list:
        return (self.system / "var/lib/expression-bridge/installed").read_text(encoding="utf-8").splitlines()

    def install_update(self, **env) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(self.boot / export.PROGRAM_FOLDER / "pi/install.sh"), "--update"],
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=60)


class Bluetooth(unittest.TestCase):
    """pi/bluetooth-midi.sh against a stand-in for bluetoothctl that knows two devices: a foot
    controller that offers MIDI and headphones that do not."""

    BLUETOOTHCTL = r"""#!/bin/bash
echo "$*" >> "$CALLS"
state="$STATE/$2"
case "$1" in
  devices) echo "Device AA:00:00:00:00:01 Chocolate Plus"; echo "Device AA:00:00:00:00:02 Headphones" ;;
  info) [ "$2" = AA:00:00:00:00:01 ] && echo "	Name: Chocolate Plus" && echo "	UUID: Vendor specific (03b80e5a-ede8-4b33-a751-6ce34ec4c700)"
        [ "$2" = AA:00:00:00:00:02 ] && echo "	Name: Headphones" && echo "	UUID: Audio Sink (0000110b-0000-1000-8000-00805f9b34fb)"
        [ -e "$state.connected" ] && echo "	Connected: yes" || echo "	Connected: no"
        [ -e "$state.trusted" ] && echo "	Trusted: yes" || echo "	Trusted: no"
        echo "	Paired: yes" ;;
  trust) touch "$state.trusted" ;;
  connect) [ -n "${OUT_OF_REACH:-}" ] && exit 1; touch "$state.connected" ;;
esac
exit 0
"""

    def run_script(self, looks: int, **env) -> str:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        bin_folder, state = folder / "bin", folder / "state"
        bin_folder.mkdir()
        state.mkdir()
        stubs = {"bluetoothctl": self.BLUETOOTHCTL, "rfkill": "#!/bin/bash\nexit 0\n",
                 "timeout": '#!/bin/bash\nshift\nexec "$@"\n',             # macOS has none of its own
                 # each rest between two looks is counted; after the last one the script is ended
                 "sleep": f'#!/bin/bash\necho rest >> "$CALLS"\n'
                          f'[ "$(grep -c "^rest" "$CALLS")" -ge {looks} ] && kill -TERM $PPID\nexit 0\n'}
        for name, body in stubs.items():
            (bin_folder / name).write_text(body, encoding="utf-8")
            (bin_folder / name).chmod(0o755)
        calls = folder / "calls.txt"
        calls.write_text("", encoding="utf-8")
        subprocess.run(["bash", str(export.PROJECT / "pi" / "bluetooth-midi.sh")], capture_output=True, timeout=30,
                       env=dict(os.environ, PATH=f"{bin_folder}:{os.environ['PATH']}", CALLS=str(calls),
                                STATE=str(state), **env))
        return calls.read_text(encoding="utf-8")

    def test_a_midi_device_is_trusted_and_connected_and_then_nothing_is_searched(self):
        calls = self.run_script(looks=3)
        self.assertIn("trust AA:00:00:00:00:01", calls)
        self.assertEqual(calls.count("connect AA:00:00:00:00:01"), 1)
        self.assertNotIn("AA:00:00:00:00:02\n".join(("connect ", "")), calls)     # headphones are no MIDI device
        self.assertNotIn("trust AA:00:00:00:00:02", calls)
        self.assertNotIn("scan on", calls)                                # connected at the first look

    def test_while_none_is_in_reach_it_searches_between_tries(self):
        calls = self.run_script(looks=2, OUT_OF_REACH="1")
        self.assertEqual(calls.count("--timeout 8 scan on"), 2)
        self.assertEqual(calls.count("connect AA:00:00:00:00:01"), 2)


class Update(unittest.TestCase):
    """bridge.pi_update: the bridge on the Pi runs install.sh again when the card needs more."""

    def setUp(self) -> None:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.script, self.marker = folder / "install.sh", folder / "installed"
        self.script.write_text("#!/bin/bash\nLEVEL=2\n", encoding="utf-8")
        self.started = []
        for patcher in (mock.patch.object(bridge.sys, "platform", "linux"),
                        mock.patch.object(bridge.os, "geteuid", lambda: 0, create=True),
                        mock.patch.object(bridge.subprocess, "run", lambda command, **_: self.started.append(command)),
                        mock.patch.object(bridge, "say", lambda text: None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def update(self, settings=None, folder=None) -> bool:
        return bridge.pi_update(settings or {}, self.script, self.marker, folder)

    def test_a_pi_set_up_before_levels_is_brought_up_to_date(self):
        self.marker.write_text("2026-10-18 03:42:04\n", encoding="utf-8")
        self.assertTrue(self.update())
        self.assertEqual(self.started, [["systemd-run", "--unit", "expression-bridge-update", "--collect", "--no-block",
                                         "/bin/bash", str(self.script), "--update"]])

    def test_nothing_happens_when_level_and_wlan_are_as_set_up(self):
        settings = {"pi": {"wlan_name": "MS Plus Bridge", "wlan_password": "kq7m2p9xwt"}}
        self.marker.write_text(f"level=2\nwlan={cs.wlan_fingerprint(settings)}\n", encoding="utf-8")
        self.assertFalse(self.update(settings))
        self.assertTrue(self.update({"pi": {"wlan_name": "MS Plus Bridge", "wlan_password": "another-one"}}))

    def test_not_for_a_wlan_install_sh_would_not_find_on_the_card(self):
        # settings from a USB stick that could not be stored: install.sh notes the card's WLAN, and
        # the bridge started it again at every start
        on_card = {"pi": {"wlan_name": "MS Plus Bridge", "wlan_password": "kq7m2p9xwt"}}
        self.marker.write_text(f"level=2\nwlan={cs.wlan_fingerprint(on_card)}\n", encoding="utf-8")
        stick = {"pi": {"wlan_name": "MS Plus Bridge", "wlan_password": "another-one"}}
        with mock.patch.object(bridge.cs, "load_with_fallback", lambda folder: (on_card, "current", [])):
            self.assertFalse(self.update(stick, folder=Path("card")))
        self.assertEqual(self.started, [])
        with mock.patch.object(bridge.cs, "load_with_fallback", lambda folder: (stick, "current", [])):
            self.assertTrue(self.update(stick, folder=Path("card")))      # stored on the card: set up

    def test_not_on_a_computer_that_was_never_set_up_by_install_sh(self):
        self.assertFalse(self.update())                          # no marker: not a Pi installed by it
        self.assertEqual(self.started, [])


if __name__ == "__main__":
    unittest.main()
