#!/usr/bin/env python3
"""Back up the iOS root volume's own structures from the phone (read only), small enough for C:.

  asus-iosroot-metabackup.py OUTDIR

What HFS+'s live resize (boot-arg asushfsgrow, hfs_extendfs) changes on the old volume: the
volume header (block 0), the allocation bitmap file, the extents overflow file, the journal
info block and the journal, and the alternate volume header in the volume's last block. These
are read with adb (Magisk su, dd) into OUTDIR/blocks.bin with OUTDIR/ranges.txt ("start
count" in 4 KiB blocks, as stored) and a sha256 of each range; together tens of MB where a full
pull is 12 GiB. Writing them back (the same ranges, in order) gives the volume back its
12 GiB header, bitmap and journal; blocks of the added space are then simply outside it.
"""
import hashlib
import os
import struct
import subprocess
import sys

PART = '/dev/block/by-name/iosroot'
BS = 4096


def read(start, count):
    r = subprocess.run(['adb', 'exec-out', f"su -c 'dd if={PART} bs={BS} skip={start} count={count} 2>/dev/null'"],
                       capture_output=True, stdin=subprocess.DEVNULL)
    if r.returncode or len(r.stdout) != count * BS:
        sys.exit(f'read of {count} blocks at {start} failed ({len(r.stdout)} bytes)')
    return r.stdout


def fork_extents(vh, at):
    return [struct.unpack('>II', vh[at + 16 + 8 * i:at + 24 + 8 * i]) for i in range(8)]


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    b0 = read(0, 1)
    vh = b0[1024:1536]
    sig, ver, attr = struct.unpack('>2sHI', vh[:8])
    jib = struct.unpack('>I', vh[0x0c:0x10])[0]
    bsize, total, free = struct.unpack('>III', vh[0x28:0x34])
    if sig != b'H+' or bsize != BS:
        sys.exit(f'not an HFS+ volume with 4 KiB blocks: {sig} {bsize}')
    ranges = [(0, 1)]
    for name, at in (('allocation', 0x70), ('extents', 0xc0)):
        for s, c in fork_extents(vh, at):
            if c:
                ranges.append((s, c))
    if jib:
        ranges.append((jib, 1))
        j = read(jib, 1)
        joff, jsize = struct.unpack('>QQ', j[36:52])
        if joff % BS or jsize % BS:
            sys.exit(f'journal not block aligned: {joff} {jsize}')
        ranges.append((joff // BS, jsize // BS))
    ranges.append((total - 1, 1))
    lines, blob = [], bytearray()
    for s, c in ranges:
        data = read(s, c)
        blob += data
        lines.append(f'{s} {c} {hashlib.sha256(data).hexdigest()}')
    open(os.path.join(out, 'blocks.bin'), 'wb').write(blob)
    open(os.path.join(out, 'ranges.txt'), 'w').write('\n'.join(lines) + '\n')
    info = (f'volume {sig.decode()} v{ver} attr {attr:#x}: {total} blocks of {bsize}, {free} free, journal info block '
            f'{jib}; {len(ranges)} ranges, {len(blob) // BS} blocks ({len(blob) >> 20} MiB), sha256 of all '
            f'{hashlib.sha256(blob).hexdigest()}')
    open(os.path.join(out, 'info.txt'), 'w').write(info + '\n')
    print(info)
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
