#!/bin/bash
# Linux/WSL: build a RAM-only image from the verified ASUS kernel artifact.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
kernel=$(realpath "${1:?Usage: tools/build-asus-image.sh ASUS_KERNEL [HFS_RAM_ROOT]}")
out="$repo/shim/asus-xnu/out"
mkdir -p "$out"
if [ "$#" -gt 1 ]; then
    ramdisk=$(realpath "$2")
    python3 "$repo/tools/verify-asus-kernel.py" "$kernel" --require-hfs
    python3 - "$ramdisk" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
assert 2048 <= p.stat().st_size <= 96 * 1024 * 1024, 'HFS root must be at most 96 MiB for this loader'
with p.open('rb') as f:
    f.seek(1024)
    assert f.read(2) in (b'H+', b'HX'), 'Expected a raw HFS+/HFSX volume, not a DMG container or APFS'
PY
else
    python3 "$repo/tools/verify-asus-kernel.py" "$kernel"
    clang -target arm64-apple-ios16.0 -DASUS_I007=1 -c "$repo/tools/sweet-init.S" -o "$out/asus-init.o"
    python3 "$repo/tools/mkinit.py" "$out/asus-init.o" "$out/asus-init"
    ramdisk="$out/asus-init"
fi
python3 "$repo/tools/mkadt-asus.py" "$out/asus.adt"
python3 "$repo/tools/mkpayload.py" "$kernel" "$out/asus.adt" "$out/payload.bin" --ramdisk "$ramdisk" --cmdline 'debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=md0'
bash "$repo/shim/asus-xnu/build.sh" "$out/payload.bin"
