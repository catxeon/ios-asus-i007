#!/bin/bash
# Prints the boot-arg that gives XNU the wall clock: asusrtc=N, where N is the UTC time in
# seconds minus the counter of the PMK8350 RTC (seconds since its last reset; neither Android
# nor XNU writes it). Read from the running Android (Magisk su), so run it right before the
# image is built: the counter starts again at 0 after a hard reset of the PMIC (seen
# 2026-11-10), and the RTC's crystal drifts by a second or two a day.
#
#   ASUS_EXTRA_ARGS="... $(bash tools/asus-rtc-offset.sh)" bash tools/build-asus-image.sh ...
#
# Needs: adb (Windows side or WSL, wherever the phone is attached), Magisk su on the phone.
set -euo pipefail
out=$(adb shell "su -c 'cat /sys/class/rtc/rtc0/since_epoch; date +%s'" | tr -d '\r')
raw=$(sed -n 1p <<<"$out")
now=$(sed -n 2p <<<"$out")
if ! [[ $raw =~ ^[0-9]+$ && $now =~ ^[0-9]+$ ]]; then
    echo "asus-rtc-offset: no answer from the phone: $out" >&2
    exit 1
fi
here=$(date +%s)
d=$((now - here))
if [ "${d#-}" -gt 5 ]; then
    echo "asus-rtc-offset: the phone's time differs from this computer's by $d s" >&2
fi
echo "asusrtc=$((now - raw))"
