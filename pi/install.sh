#!/bin/bash
# Sets the MS Plus Bridge up on a Raspberry Pi with Raspberry Pi OS Lite. Runs as root, by
# itself at the first start (export.py puts it into the card's user-data for cloud-init), and
# again at every start until it has worked, for example when there was no internet yet. By hand:
#     sudo bash /boot/firmware/expression-bridge/pi/install.sh
# With --update the bridge runs it (bridge.py, pi_update) when the card was written with a program
# that needs more of the system than this Pi has (LEVEL below), or with another WLAN of its own;
# then the light stays the bridge's.
#
# The program stays where export.py wrote it, on the card's boot partition, and runs from there,
# so that writing the card again brings settings and program up to date alike. This script only
# adds what the system needs: four packages (from the internet, once), the service that starts the
# bridge at power-on, mounting USB sticks, Bluetooth MIDI devices, the Pi's own WLAN, and less
# writing to the card. What it did is in expression-bridge-install.txt on the boot partition,
# readable on any computer the card is put into.
set -u

LEVEL=2                                                     # 2: Bluetooth MIDI, own WLAN, interface on the network
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"     # …/expression-bridge/pi
APP="$(dirname "$HERE")"                                    # …/expression-bridge
BOOT="$(dirname "$APP")"                                    # /boot/firmware
TARGET="${EXPRESSION_BRIDGE_TARGET:-}"                      # the system to set up; empty: this one (tests give a folder)
REPORT="$BOOT/expression-bridge-install.txt"
DONE="$TARGET/var/lib/expression-bridge/installed"
UNITS="$TARGET/etc/systemd/system"
PACKAGES=(python3-mido python3-rtmidi python3-yaml bluez)
ACCESS_POINT="ms-plus-bridge-wlan"                          # NetworkManager's name for the Pi's own WLAN
UPDATE=""
[ "${1:-}" = "--update" ] && UPDATE=1
LED=""
for name in ACT led0; do [ -e "$TARGET/sys/class/leds/$name/trigger" ] && LED="$TARGET/sys/class/leds/$name" && break; done
LED_JOB="$( [ -n "$LED" ] && sed -n 's/.*\[\([^]]*\)\].*/\1/p' "$LED/trigger")"   # usually: SD card activity

say() {
    echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" | tee -a "$REPORT"
    logger -t expression-bridge-install -- "$*" 2>/dev/null || true
}

led() {   # heartbeat while installing, fast blinking when it failed (as the bridge's "nothing is sent")
    [ -n "$LED" ] && [ -z "$UPDATE" ] || return 0
    echo "$1" > "$LED/trigger" 2>/dev/null || return 0
    if [ "$1" = timer ]; then
        echo 100 > "$LED/delay_on" 2>/dev/null
        echo 100 > "$LED/delay_off" 2>/dev/null
    fi
    return 0
}

fail() {
    say "NOT INSTALLED: $* – it is tried again at the next start."
    led timer
    exit 1
}

unit() {   # a unit file of this folder into the system, with the place of the boot partition filled in
    sed "s#@BOOT@#$BOOT#g" "$HERE/$1" > "$UNITS/$1"
}

installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "ok installed"; }

if [ "$(id -u)" != 0 ]; then
    echo "Run it as root: sudo bash $0" >&2
    exit 1
fi
[ -f "$APP/bridge.py" ] || { echo "bridge.py is not next to this folder ($APP)" >&2; exit 1; }

say "$([ -n "$UPDATE" ] && echo Updating || echo Installing) the MS Plus Bridge ($(cat "$APP/VERSION" 2>/dev/null || echo 'version unknown'))"
led heartbeat

# 1. If anything below fails, this runs again at the next start (an update: whenever the bridge starts).
mkdir -p "$UNITS"
if [ -z "$UPDATE" ]; then
    unit expression-bridge-setup.service
    systemctl daemon-reload
    systemctl enable expression-bridge-setup.service >/dev/null 2>&1
fi

# 2. The packages that are missing: from the internet, so wait for it and for the clock (a Pi has
#    no clock of its own, and with a wrong date the package lists are refused as "not valid yet").
missing=()
for package in "${PACKAGES[@]}"; do installed "$package" || missing+=("$package"); done
if [ ${#missing[@]} -gt 0 ]; then
    online() { getent hosts deb.debian.org >/dev/null 2>&1; }
    for _ in $(seq 1 90); do
        online && break
        sleep 10
    done
    online || fail "no internet (WLAN or a network cable is needed once, for ${missing[*]})"
    for _ in $(seq 1 30); do
        [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ] && break
        sleep 2
    done
    export DEBIAN_FRONTEND=noninteractive
    apt=(apt-get -q -o DPkg::Lock::Timeout=600)
    "${apt[@]}" update >>"$REPORT" 2>&1 || fail "the package lists could not be loaded (apt-get update)"
    "${apt[@]}" install -y --no-install-recommends "${missing[@]}" >>"$REPORT" 2>&1 \
        || fail "the packages could not be installed (${missing[*]})"
fi
# The ALSA sequencer, through which rtmidi reaches every MIDI port, loaded at every start.
mkdir -p "$TARGET/etc/modules-load.d"
echo snd-seq > "$TARGET/etc/modules-load.d/expression-bridge.conf"
modprobe snd-seq 2>/dev/null || true
python3 -c "import mido, rtmidi, yaml; mido.get_input_names()" >>"$REPORT" 2>&1 \
    || fail "the Python packages are installed but do not work"
# The versions of the Debian packages (Debian's mido tells Python it is 0.0.0).
say "Packages: $(dpkg-query -W -f='${Package} ${Version}\n' "${PACKAGES[@]}" 2>/dev/null | paste -sd, - | sed 's/,/, /g')"

# 3. A USB stick is mounted read-only at /media/stick/<partition> as soon as it is put in.
mkdir -p "$TARGET/media/stick" "$TARGET/etc/udev/rules.d"
cp "$HERE/90-expression-bridge-stick.rules" "$TARGET/etc/udev/rules.d/"
udevadm control --reload-rules 2>/dev/null || true

# 4. Less writing to the card, so that pulling the plug is harmless: the system's log is kept in
#    memory, and the daily package and manual-page jobs do not run.
mkdir -p "$TARGET/etc/systemd/journald.conf.d"
cat > "$TARGET/etc/systemd/journald.conf.d/expression-bridge.conf" <<'JOURNAL'
# MS Plus Bridge: the log lives in memory only (it is gone after a restart).
[Journal]
Storage=volatile
RuntimeMaxUse=20M
JOURNAL
systemctl restart systemd-journald 2>/dev/null || true
for timer in apt-daily.timer apt-daily-upgrade.timer man-db.timer; do
    systemctl disable --now "$timer" >/dev/null 2>&1 || true
done

# 5. Bluetooth MIDI: a keyboard or foot controller that offers it (BLE MIDI) is connected when it
#    is switched on near the Pi, and then is a MIDI port like one on USB.
rfkill unblock bluetooth 2>/dev/null || true
systemctl enable bluetooth.service >/dev/null 2>&1 || true
unit expression-bridge-bluetooth.service

# 6. The Pi's own WLAN, opened when none it knows (the one set in Raspberry Pi Imager) is around:
#    name and password come from the settings file (`pi:`, made by export.py) – the one the bridge
#    runs with: the current file, or the previous one if that is not usable. What is noted for the
#    bridge is the WLAN asked for, set up or not: compared at its next start with the settings, a
#    difference would run this again, and that restarts the bridge (over and over, if it stayed).
wlan_name="" wlan_password="" wlan=""
{ IFS= read -r wlan_name; IFS= read -r wlan_password; IFS= read -r wlan; } < <(
    python3 - "$APP" "$BOOT" <<'PY' 2>/dev/null
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import config_schema
settings = config_schema.load_with_fallback(Path(sys.argv[2]))[0] or {}
wlan = settings.get("pi") or {}
print(wlan.get("wlan_name") or "")
print(wlan.get("wlan_password") or "")
print(config_schema.wlan_fingerprint(settings))          # the bridge compares with the same function
PY
)
if [ -n "$wlan_name" ] && [ -n "$wlan_password" ]; then
    if command -v nmcli >/dev/null 2>&1; then
        # Without a WLAN country (set with the WLAN in Raspberry Pi Imager) the radio stays blocked.
        rfkill unblock wifi 2>/dev/null || true
        if command -v raspi-config >/dev/null 2>&1; then
            country="$(raspi-config nonint get_wifi_country 2>/dev/null || true)"
            if [ -z "$country" ] || [ "$country" = "00" ]; then
                say "No WLAN country is set (Raspberry Pi Imager: the WLAN with its country) – the own WLAN may not appear"
            fi
        fi
        nmcli connection delete "$ACCESS_POINT" >/dev/null 2>&1 || true
        if nmcli connection add type wifi ifname wlan0 con-name "$ACCESS_POINT" autoconnect yes \
                connection.autoconnect-priority -10 ssid "$wlan_name" 802-11-wireless.mode ap \
                802-11-wireless.band bg ipv4.method shared ipv6.method disabled \
                wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$wlan_password" \
                wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp wifi-sec.pmf disable >>"$REPORT" 2>&1; then
            say "Own WLAN: “${wlan_name}” where none it knows is around (its password is shown on the computer)"
        else
            say "The Pi's own WLAN could not be set up – the bridge works without it"
        fi
    else
        say "No NetworkManager – the Pi gets no WLAN of its own"
    fi
elif command -v nmcli >/dev/null 2>&1; then
    nmcli connection delete "$ACCESS_POINT" >/dev/null 2>&1 || true
fi

# 7. The bridge, started at power-on.
unit expression-bridge.service
systemctl daemon-reload
systemctl enable expression-bridge.service >/dev/null 2>&1 || fail "the service could not be enabled"
systemctl enable expression-bridge-bluetooth.service >/dev/null 2>&1 || true
systemctl restart expression-bridge-bluetooth.service 2>/dev/null || true
mkdir -p "$(dirname "$DONE")"
printf 'level=%s\nwlan=%s\n%s\n' "$LEVEL" "$wlan" "$(date '+%Y-%m-%d %H:%M:%S')" > "$DONE"
systemctl disable expression-bridge-setup.service >/dev/null 2>&1 || true
led "${LED_JOB:-none}"                                      # the bridge takes the light over itself
systemctl restart expression-bridge.service 2>/dev/null || true
sleep 3
if systemctl is-active --quiet expression-bridge.service; then
    say "Installed (level $LEVEL). The bridge runs and starts by itself whenever the Pi is switched on."
else
    say "Installed, but the bridge is not running: $(systemctl status expression-bridge.service --no-pager -n 5 2>&1 | tail -n 5)"
fi
exit 0
