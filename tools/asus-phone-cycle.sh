#!/bin/bash
# One unattended ASUS I007 test cycle from Windows Git Bash (adb, usbipd) + WSL (fastboot).
#   tools/asus-phone-cycle.sh IMAGE OUT_DIR [MAX_WAIT_SECS]
# 1. phone in Android (adb) -> reboot bootloader -> usbipd attach to WSL -> fastboot boot IMAGE
#    (RAM only, nothing is flashed); a phone already in fastboot (attached to WSL) is used as is;
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
# Git Bash /c/... -> WSL /mnt/c/... (backslashes do not survive wsl.exe arguments).
wsl_path() { realpath "$1" | sed 's#^/\([a-zA-Z]\)/#/mnt/\L\1/#'; }
img_wsl=$(wsl_path "$img")
log() { echo "$(date +%T) $*" | tee -a "$out/cycle.txt"; }
wfastboot() { MSYS_NO_PATHCONV=1 wsl.exe -u root -e fastboot "$@" 2>&1 | tr -d '\r'; }

# Root volume state (read-only): the HFS+ header at byte 1024 of iosroot. Run 0045 panicked
# with the root mounted read-write; the non-journaled volume kept a stale bitmap, later
# boots allocated blocks twice (8 files overlapped, Setup flags lost) and HFS marked the
# volume inconsistent, which also keeps hfs_unmount from marking it clean. An iOS boot on
# a dirty root makes it worse: fsck it first (pull, fsck.hfsplus, asus-iosroot-blockpatch.py).
root_state() {
    adb exec-out "su -c 'dd if=/dev/block/by-name/iosroot bs=4096 count=1 2>/dev/null'" > "$out/root-header-$1.bin"
    python -I -c '
import struct, sys
b = open(sys.argv[1], "rb").read()[1024:1024 + 0x34]
sig, ver, attr = struct.unpack(">2sHI", b[:8])
files, dirs, bsize, total, free = struct.unpack(">IIIII", b[0x20:0x34])
clean = attr & (1 << 8) and not attr & (1 << 14)
print("root volume (%s) %s v%d attr 0x%x: %s%s, %d files, %d dirs, %d of %d blocks free" % (
    sys.argv[2], sig.decode("latin-1"), ver, attr, "clean" if clean else "DIRTY",
    ", journaled" if attr & (1 << 13) else "", files, dirs, free, total))
sys.exit(0 if clean else 3)
' "$(cygpath -w "$out/root-header-$1.bin" 2>/dev/null || echo "$out/root-header-$1.bin")" "$1" 2>&1 | tee -a "$out/cycle.txt"
}

log "image $img ($(sha256sum "$img" | cut -c1-16)) -> $img_wsl"
if wfastboot devices | grep -q fastboot; then
    log 'phone already in fastboot (attached to WSL)'
else
    timeout 600 adb wait-for-device || { log 'no adb device'; exit 1; }
    # Images that mount iosroot read-write (all but ASUS_TOUCH_LAB ones) must not start on
    # a dirty root; ASUS_ALLOW_DIRTY_ROOT=1 skips the check (lab images, deliberate tests).
    if ! root_state before && [ "${ASUS_ALLOW_DIRTY_ROOT:-0}" != 1 ]; then
        log 'iosroot is dirty: fsck it before booting iOS (or ASUS_ALLOW_DIRTY_ROOT=1 for lab images)'
        exit 2
    fi
    # Qualcomm crash download mode is on in this Android (download_mode=1, mini dumps):
    # a fatal secure-side error under our kernel then parks the SoC in EDL (05c6:9008)
    # until a manual power-key reset. Turn it off for this boot only (runtime module
    # parameters, set again by the next Android boot), so such an error just resets
    # the phone back into Android and this script can read the log.
    adb shell "su -c 'for f in /sys/module/msm_poweroff/parameters/download_mode /sys/module/qcom_dload_mode/parameters/download_mode; do [ -e \$f ] && echo 0 > \$f; done; cat /sys/module/msm_poweroff/parameters/download_mode /sys/module/qcom_dload_mode/parameters/download_mode 2>/dev/null'" \
        | tr -d '\r' | tr '\n' ' ' | sed 's/^/download_mode now: /' | tee -a "$out/cycle.txt"; echo | tee -a "$out/cycle.txt"
    adb reboot bootloader
    busid=
    for i in $(seq 60); do
        busid=$(usbipd list 2>/dev/null | tr -d '\r' | awk '/0b05:4daf/ {print $1; exit}')
        [ -n "$busid" ] && break
        sleep 2
    done
    [ -n "$busid" ] || { log 'fastboot USB device not found'; exit 1; }
    usbipd attach --wsl --busid "$busid" >/dev/null 2>&1
    sleep 4
fi
wfastboot boot "$img_wsl" | tee -a "$out/cycle.txt"
grep -q 'Booting.*OKAY' "$out/cycle.txt" || { log 'fastboot boot failed'; exit 1; }
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
bytes=$(adb shell "su -c 'blockdev --getsize64 /dev/block/by-name/iosroot'" | tr -d '\r')
skip=$(( bytes / 4096 - 1025 ))
adb exec-out "su -c 'dd if=/dev/block/by-name/iosroot bs=4096 skip=$skip count=1025 2>/dev/null'" > "$out/log.bin"
log "log area: $(stat -c %s "$out/log.bin") bytes from block $skip"
MSYS_NO_PATHCONV=1 wsl.exe python3 "$(wsl_path "$repo/tools/sweet-log.py")" "$(wsl_path "$out/log.bin")" \
    "$(wsl_path "$out")/log.txt" 2>&1 | tee -a "$out/cycle.txt"
log "decoded: $out/log.txt ($(wc -l < "$out/log.txt" 2>/dev/null || echo 0) lines)"
root_state after
