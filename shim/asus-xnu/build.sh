#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p out
version="${ASUS_XNU_VERSION:-0001}"
[[ "$version" =~ ^[0-9]{4}$ ]] || { echo 'ASUS_XNU_VERSION must be four digits' >&2; exit 1; }
flags=(-O2 -ffreestanding -nostdlib -fno-pic -fno-builtin -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra -Werror -I.. -DFB_BASE=0xe5000000UL -DFB_H=2448 "-DASUS_XNU_VERSION=\"$version\"")
for spec in 'start:../start.S' 'main:main.c' 'console:../console.c' 'fdt:../fdt.c' 'loader:../loader.c'; do
    aarch64-linux-gnu-gcc "${flags[@]}" -c "${spec#*:}" -o "out/${spec%%:*}.o"
done
if aarch64-linux-gnu-readelf -r out/main.o out/console.o out/fdt.o out/loader.o | grep ABS64 >/dev/null; then
    echo 'Unexpected absolute pointer relocation' >&2
    exit 1
fi
aarch64-linux-gnu-gcc "${flags[@]}" -static -T ../shim.ld -Wl,--build-id=none -Wl,--no-warn-rwx-segments -Wl,-N out/start.o out/main.o out/console.o out/fdt.o out/loader.o -o out/shim.elf
aarch64-linux-gnu-objcopy -O binary out/shim.elf out/Image.base
if [ "$#" = 0 ]; then
    echo 'Compile-only check passed; no bootable image created.'
    exit 0
fi
python3 - "$1" <<'PY'
from pathlib import Path
import struct, sys
p = Path(sys.argv[1]).read_bytes()
assert len(p) >= 0x2a8 and p[:8] == b'SWEETXNU' and struct.unpack_from('<I', p, 8)[0] == 1
base, size = struct.unpack_from('<QQ', p, 0x38)
# Phone RAM bank e3000000-27fffffff: no reserved region from 0x140000000 up to 0x280000000.
assert base == 0x140000000 and 0x20000000 <= size <= 0x140000000 and size % 0x1000000 == 0, \n    'Not an ASUS payload (RAM 0x140000000, 512 MiB..5 GiB)'
assert len(p) < 0x8000000, 'Payload exceeds the loader bound'
PY
size=$(aarch64-linux-gnu-nm out/shim.elf | awk '$3=="_end" {print $1}')
cp out/Image.base out/Image
truncate -s "$(( (16#$size + 4095) & ~4095 ))" out/Image
cat "$1" >> out/Image
python3 - <<'PY'
import gzip, struct
from pathlib import Path
b = bytearray(Path('out/Image').read_bytes())
struct.pack_into('<Q', b, 16, len(b))
Path('out/Image').write_bytes(b)
h = b'070701' + b'00000000'*4 + b'00000001' + b'00000000'*6 + b'0000000b' + b'00000000'
c = h + b'TRAILER!!!\0'
c += b'\0' * (-len(c) % 4)
Path('out/ramdisk.gz').write_bytes(gzip.compress(c, mtime=0))
PY
python3 "${MKBOOTIMG:-../../../mkbootimg/mkbootimg.py}" --header_version 3 --kernel out/Image --ramdisk out/ramdisk.gz --os_version 11.0.0 --os_patch_level 2023-07 --cmdline 'console=null' -o "out/asus-i007-xnu-$version.img"
sha256sum "out/asus-i007-xnu-$version.img"
