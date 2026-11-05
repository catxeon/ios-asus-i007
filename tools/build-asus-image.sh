#!/bin/bash
# Linux/WSL: build a RAM-only image from the verified ASUS kernel artifact.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
kernel=$(realpath "${1:?Usage: tools/build-asus-image.sh ASUS_KERNEL [HFS_RAM_ROOT]}")
out="$repo/shim/asus-xnu/out"
mkdir -p "$out"
verify_flags=(--reject-compositor)
adt_flags=()
root_limit=96
if [ "${ASUS_DISPLAY:-0}" = 1 ]; then
    [ "$#" -gt 1 ] || { echo 'ASUS display requires an explicit HFS RAM root' >&2; exit 1; }
    verify_flags+=(--require-display)
    adt_flags+=(--display)
    # The shim still checks the exact combined payload against 128 MiB.
    root_limit=104
fi
if [ "${ASUS_UFS:-0}" = 1 ]; then
    # UFS: the kernel must carry the iosroot-only write guard. Without ASUS_PHONE_IOS the
    # root stays the RAM disk and the driver only reads the LUN 0 GPT (ufsprobe=1, ufslist=1).
    verify_flags+=(--require-ufs-read-only)
    adt_flags+=(--ufs)
fi
if [ "$#" -gt 1 ]; then
    ramdisk=$(realpath "$2")
    python3 "$repo/tools/verify-asus-kernel.py" "$kernel" --require-hfs "${verify_flags[@]}"
    python3 - "$ramdisk" "$root_limit" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
limit = int(sys.argv[2])
assert 2048 <= p.stat().st_size <= limit * 1024 * 1024, f'HFS root must be at most {limit} MiB'
with p.open('rb') as f:
    f.seek(1024)
    assert f.read(2) in (b'H+', b'HX'), 'Expected a raw HFS+/HFSX volume, not a DMG container or APFS'
PY
else
    python3 "$repo/tools/verify-asus-kernel.py" "$kernel" "${verify_flags[@]}"
    clang -target arm64-apple-ios16.0 -DASUS_I007=1 -c "$repo/tools/sweet-init.S" -o "$out/asus-init.o"
    python3 "$repo/tools/mkinit.py" "$out/asus-init.o" "$out/asus-init"
    ramdisk="$out/asus-init"
fi
if [ "${ASUS_PHONE_IOS:-0}" = 1 ]; then
    # iOS from the iosroot partition on the phone: needs ASUS_UFS=1 ASUS_DISPLAY=1
    # ASUS_IOS_USERLAND=1, a userland kernel, the private D22AP ADT and OS trust cache.
    [ "${ASUS_UFS:-0}" = 1 ] && [ "${ASUS_DISPLAY:-0}" = 1 ] && [ "${ASUS_IOS_USERLAND:-0}" = 1 ] || {
        echo 'ASUS_PHONE_IOS needs ASUS_UFS=1 ASUS_DISPLAY=1 ASUS_IOS_USERLAND=1' >&2; exit 1; }
    verify_flags+=(--require-userland)
    adt_flags+=(--ios-gui "${ASUS_IOS_GUI:?D22AP ADT}" --trust-cache "${ASUS_TRUST_CACHE:?OS trust cache}"
                --mem-mib "${ASUS_MEM_MIB:-4096}")
    python3 "$repo/tools/verify-asus-kernel.py" "$kernel" --require-hfs "${verify_flags[@]}"
fi
python3 "$repo/tools/mkadt-asus.py" "$out/asus.adt" "${adt_flags[@]}"
cmdline='debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=md0'
if [ "${ASUS_UFS:-0}" = 1 ]; then
    if [ "${ASUS_PHONE_IOS:-0}" = 1 ]; then
        # Root on iosroot, writable (the kernel refuses writes to any other partition).
        cmdline="${cmdline/rd=md0/rd=ufsroot0} ufsroot=iosroot ufsrw=1 rootrw=1 ufslist=1"
        cmdline+=' ignition_level=1 interrupt-masked-debug-mode=0 sched-preemption-disable-debug-mode=0'
    else
        cmdline+=" ufsprobe=1 ufslist=1 ufsroot=${ASUS_UFS_ROOT:-iosroot}"
    fi
fi
if [ "${ASUS_IOS_USERLAND:-0}" = 1 ]; then
    [ "$#" -gt 1 ] || { echo 'iOS userspace requires an explicit HFS RAM root' >&2; exit 1; }
    # XNU's DEVELOPMENT macOS profile otherwise refuses an iOS-platform exec.
    cmdline+=' unentitled_ios_sim_launch=1'
fi
python3 "$repo/tools/mkpayload.py" "$kernel" "$out/asus.adt" "$out/payload.bin" --ramdisk "$ramdisk" --cmdline "$cmdline"
bash "$repo/shim/asus-xnu/build.sh" "$out/payload.bin"
