#!/bin/bash
# One unattended ASUS I007 test cycle from Windows Git Bash (adb, usbipd) + WSL (fastboot).
#   tools/asus-phone-cycle.sh IMAGE OUT_DIR [MAX_WAIT_SECS]
# 1. phone in Android (adb) -> reboot bootloader -> usbipd attach to WSL -> fastboot boot IMAGE
#    (RAM only, nothing is flashed);
# 2. the image resets itself (PSCI SYSTEM_RESET on panic, iOS reboot or boot-arg
#    asusreboot=N) and ABL boots Android again;
# 3. read the console log area (last 1025 4 KiB blocks of iosroot, kernel --ufs profile with
#    sweet 0047) over adb as root and decode it with tools/sweet-log.py into OUT_DIR/log.txt.
# Needs: adb + usbipd on Windows, fastboot in WSL (root), Magisk su on the phone.
set -uo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
img=${1:?usage: asus-phone-cycle.sh IMAGE OUT_DIR [MAX_WAIT_SECS]}
out=${2:?usage: asus-phone-cycle.sh IMAGE OUT_DIR [MAX_WAIT_SECS]}
max=${3:-2400}
mkdir -p "$out"
img_wsl=$(MSYS_NO_PATHCONV=1 wsl.exe wslpath -a "$(cygpath -w "$(realpath "$img")")" | tr -d '\r')
log() { echo "$(date +%T) $*" | tee -a "$out/cycle.txt"; }

log "image $img ($(sha256sum "$img" | cut -c1-16))"
timeout 600 adb wait-for-device || { log 'no adb device'; exit 1; }
adb reboot bootloader
for i in $(seq 60); do
    busid=$(usbipd list 2>/dev/null | tr -d '\r' | awk '/0b05:4daf/ {print $1; exit}')
    [ -n "$busid" ] && break
    sleep 2
done
[ -n "${busid:-}" ] || { log 'fastboot USB device not found'; exit 1; }
usbipd attach --wsl --busid "$busid" >/dev/null 2>&1
sleep 4
MSYS_NO_PATHCONV=1 wsl.exe -u root -e fastboot boot "$img_wsl" 2>&1 | tr -d '\r' | tee -a "$out/cycle.txt"
log 'boot sent; waiting for the phone to reset itself back into Android'
start=$(date +%s)
sleep 60
until adb get-state 2>/dev/null | grep -q device; do
    if [ $(( $(date +%s) - start )) -gt "$max" ]; then
        log "no Android after ${max}s: the phone did not reset itself (hang?)"
        exit 2
    fi
    sleep 10
done
for i in $(seq 90); do
    [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ] && break
    sleep 2
done
log "Android back after $(( $(date +%s) - start ))s"
blocks=$(adb shell "su -c 'blockdev --getsize64 /dev/block/by-name/iosroot'" | tr -d '\r')
skip=$(( blocks / 4096 - 1025 ))
adb exec-out "su -c 'dd if=/dev/block/by-name/iosroot bs=4096 skip=$skip count=1025 2>/dev/null'" > "$out/log.bin"
log "log area: $(stat -c %s "$out/log.bin") bytes from block $skip"
MSYS_NO_PATHCONV=1 wsl.exe python3 "$(MSYS_NO_PATHCONV=1 wsl.exe wslpath -a "$(cygpath -w "$repo/tools/sweet-log.py")" | tr -d '\r')" \
    "$(MSYS_NO_PATHCONV=1 wsl.exe wslpath -a "$(cygpath -w "$(realpath "$out")/log.bin")" | tr -d '\r')" \
    "$(MSYS_NO_PATHCONV=1 wsl.exe wslpath -a "$(cygpath -w "$(realpath "$out")")/log.txt" | tr -d '\r')" 2>&1 | tee -a "$out/cycle.txt"
log "decoded: $out/log.txt ($(wc -l < "$out/log.txt" 2>/dev/null || echo 0) lines)"
