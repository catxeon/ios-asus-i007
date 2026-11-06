#!/usr/bin/env python3
"""Pack driver firmware for the ASUS I007 kernel's asus_fw_load() (bsd/dev/sweet_ufs.c).

  asus-fw-pack.py OUT.bin FILE...

The kernel reads a 256 KiB area right before the console log at the end of the
iosroot partition: "ASUSFW01", u32 count, count x { char name[32]; u32 offset;
u32 size } (offsets from the area start), then the files. The firmware comes from
the phone's own /vendor/firmware and never goes into git or the public CI build.

Write it from Android as root (iosroot = sda24 on LUN 0, 4 KiB blocks). Android's
mksh arithmetic is 32-bit: compute the block on the PC. iosroot is 34359717888
bytes = 8388603 blocks, the log starts at 8387578, so the area is at 8387514:
  dd if=OUT.bin of=/dev/block/sda24 bs=4096 seek=8387514 count=64 conv=notrunc,fsync
"""
import struct
import sys
from pathlib import Path

AREA = 64 * 4096
out = Path(sys.argv[1])
files = [Path(p) for p in sys.argv[2:]]
header = b'ASUSFW01' + struct.pack('<I', len(files))
table_end = len(header) + 40 * len(files)
offset = (table_end + 4095) & ~4095
entries, blobs = [], []
for f in files:
    data = f.read_bytes()
    name = f.name.encode()
    assert len(name) < 32, f.name
    entries.append(name.ljust(32, b'\0') + struct.pack('<II', offset, len(data)))
    blobs.append((offset, data))
    offset = (offset + len(data) + 63) & ~63
assert offset <= AREA, f'{offset} bytes do not fit the {AREA}-byte area'
area = bytearray(AREA)
area[:table_end] = header + b''.join(entries)
for off, data in blobs:
    area[off:off + len(data)] = data
out.write_bytes(area)
print(f'{out}: {len(files)} files, {offset} of {AREA} bytes used')
