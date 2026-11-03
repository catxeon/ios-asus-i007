#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p out
flags=(-O2 -ffreestanding -nostdlib -fno-pic -fno-builtin -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra -Werror -I..)
aarch64-linux-gnu-gcc "${flags[@]}" -c ../start.S -o out/start.o
aarch64-linux-gnu-gcc "${flags[@]}" -c probe.c -o out/probe.o
if aarch64-linux-gnu-readelf -r out/probe.o | grep -q ABS64; then
    echo 'Unexpected absolute pointer relocation' >&2
    exit 1
fi
aarch64-linux-gnu-gcc "${flags[@]}" -static -T ../shim.ld -Wl,--build-id=none -Wl,--no-warn-rwx-segments -Wl,-N out/start.o out/probe.o -o out/probe.elf
aarch64-linux-gnu-objcopy -O binary out/probe.elf out/Image
size=$(aarch64-linux-gnu-nm out/probe.elf | awk '$3=="_end" {print $1}')
truncate -s "$((16#$size))" out/Image
# Match this ASUS's stock header v3, Android version and patch level.
python3 -c 'import gzip; from pathlib import Path; h=b"070701" + b"00000000"*4 + b"00000001" + b"00000000"*6 + b"0000000b" + b"00000000"; c=h+b"TRAILER!!!\0"; c+=b"\0"*(-len(c)%4); Path("out/ramdisk.gz").write_bytes(gzip.compress(c, mtime=0))'
python3 "${MKBOOTIMG:-../../../mkbootimg/mkbootimg.py}" --header_version 3 --kernel out/Image --ramdisk out/ramdisk.gz --os_version 11.0.0 --os_patch_level 2023-07 --cmdline 'console=null' -o out/asus-i007-probe-0001.img
sha256sum out/asus-i007-probe-0001.img
