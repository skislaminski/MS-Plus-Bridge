#!/bin/bash
# Connects Bluetooth MIDI devices (BLE MIDI: keyboards, foot controllers such as the Chocolate
# Plus) to the Raspberry Pi, without a screen: a device that offers MIDI and is switched on near
# the Pi is trusted, paired if it asks for it, and connected. BlueZ then makes it a MIDI port like
# one on USB, and the bridge takes it up under the name chosen in its interface. Known devices are
# connected again whenever they come back.
#
# While a MIDI device is connected, nothing is searched for: searching shares the radio with the
# connection (and with the WLAN) and would delay notes. Other known devices are then tried only
# once a minute, as every try to connect uses the radio as well. Runs as the service
# expression-bridge-bluetooth (pi/install.sh).
set -u

MIDI_SERVICE="03b80e5a-ede8-4b33-a751-6ce34ec4c700"     # the Bluetooth LE MIDI service
LOOK_SECONDS=8                                           # one search for devices
REST_SECONDS="${EXPRESSION_BRIDGE_BLUETOOTH_REST:-5}"    # between two looks
OTHERS_SECONDS=60                                        # with one connected: how often the others are tried

say() { echo "$*"; }

midi_devices() {   # the addresses of the devices BlueZ knows that offer MIDI
    bluetoothctl devices 2>/dev/null | awk '{print $2}' | while read -r address; do
        bluetoothctl info "$address" 2>/dev/null | grep -qi "$MIDI_SERVICE" && echo "$address"
    done
}

rfkill unblock bluetooth 2>/dev/null || true
for _ in $(seq 1 30); do                                  # the adapter needs a moment after the start
    bluetoothctl power on >/dev/null 2>&1 && break
    sleep 2
done

playing=0                                                # a MIDI device was connected at the last look
last_try=0
while true; do
    connected=0
    now=$(date +%s)
    try_others=$(( playing == 0 || now - last_try >= OTHERS_SECONDS ))
    [ "$try_others" = 1 ] && last_try=$now
    for address in $(midi_devices); do
        info="$(bluetoothctl info "$address" 2>/dev/null)"
        name="$(sed -n 's/^\s*Name: //p' <<<"$info" | head -n 1)"
        if grep -q "Connected: yes" <<<"$info"; then
            connected=1
            continue
        fi
        [ "$try_others" = 1 ] || continue
        grep -q "Trusted: yes" <<<"$info" || bluetoothctl trust "$address" >/dev/null 2>&1
        if ! grep -q "Paired: yes" <<<"$info"; then        # most MIDI devices need none; some ask for it
            timeout 20 bluetoothctl --agent NoInputNoOutput pair "$address" >/dev/null 2>&1 || true
        fi
        if timeout 20 bluetoothctl connect "$address" >/dev/null 2>&1; then
            say "Connected: ${name:-$address}"
            connected=1
        fi
    done
    if [ "$connected" = 0 ]; then
        timeout $((LOOK_SECONDS + 5)) bluetoothctl --timeout "$LOOK_SECONDS" scan on >/dev/null 2>&1 || true
    fi
    playing=$connected
    sleep "$REST_SECONDS"
done
