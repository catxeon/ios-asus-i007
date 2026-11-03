#!/bin/bash
# Linux/WSL: build a RAM-only image from the verified ASUS kernel artifact.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
kernel=$(realpath "${1:?Usage: tools/build-asus-image.sh ASUS_KERNEL}")
out="$repo/shim/asus-xnu/out"
mkdir -p "$out"
python3 "$repo/tools/verify-asus-kernel.py" "$kernel"
python3 "$repo/tools/mkadt-asus.py" "$out/asus.adt"
clang -target arm64-apple-ios16.0 -DASUS_I007=1 -c "$repo/tools/sweet-init.S" -o "$out/asus-init.o"
python3 "$repo/tools/mkinit.py" "$out/asus-init.o" "$out/asus-init"
python3 "$repo/tools/mkpayload.py" "$kernel" "$out/asus.adt" "$out/payload.bin" --ramdisk "$out/asus-init" --cmdline 'debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=md0'
bash "$repo/shim/asus-xnu/build.sh" "$out/payload.bin"
