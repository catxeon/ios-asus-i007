#!/usr/bin/env python3
"""Pull only what an offline edit of the iOS root needs, into a sparse image of the whole volume.

  asus-iosroot-sparsepull.py IMAGE [--file PATH ...] [--replace PATH NEWSIZE] [--adb ADB]

Run in WSL (adb.exe through interop; IMAGE on ext4, where the holes cost nothing). The
volume is 32 GiB since asushfsgrow; a full pull no longer fits on the PC's disk. An edit with
the libdmg-hfsplus tool, fsck.hfsplus -n and tools/asus-iosroot-blockdiff.py only read the
volume's own structures and the files touched, so IMAGE gets, read from the phone (Magisk su,
dd, read only):

- block 0 (volume header), the allocation, extents, catalog, attributes and startup files
  (the extents in the volume header; files with overflow extents are refused), the journal
  info block and the journal, and the last block (alternate volume header);
- with --file PATH: that file's data (one extent or several, from its catalog record);
- with --replace PATH NEWSIZE (implies --file PATH): the blocks tools/hfs-add-contiguous.py
  will put a NEWSIZE-byte file into once PATH is removed: its first-fit free run of
  ceil(NEWSIZE/4096) + 256 blocks in the bitmap with PATH's blocks freed, plus 256 blocks.

Everything else stays a hole (zeros). So the per-block hashes made from IMAGE before the edit
are right for every block the edit can change, which is what asus-iosroot-blockpatch.py checks
on the phone before it writes. The volume must be cleanly unmounted with an empty journal.
"""
import argparse
import os
import struct
import subprocess
import sys

PART = '/dev/block/by-name/iosroot'
BS = 4096


class Phone:
    def __init__(self, adb):
        self.adb = adb
        self.read_blocks = 0

    def blocks(self, start, n):
        out = bytearray()
        while n:
            k = min(n, 16384)                       # 64 MiB a call
            r = subprocess.run([self.adb, 'exec-out', f"su -c 'dd if={PART} bs={BS} skip={start} count={k} 2>/dev/null'"],
                               capture_output=True, stdin=subprocess.DEVNULL)
            if r.returncode or len(r.stdout) != k * BS:
                sys.exit(f'read of {k} blocks at {start} failed ({len(r.stdout)} bytes)')
            out += r.stdout
            start, n = start + k, n - k
            self.read_blocks += k
        return bytes(out)


def fork(vh, at):
    size, _, total = struct.unpack('>QII', vh[at:at + 16])
    ext = [struct.unpack('>II', vh[at + 16 + 8 * i:at + 24 + 8 * i]) for i in range(8)]
    return size, total, [(s, c) for s, c in ext if c]


def records(node, size):
    nrec = struct.unpack('>H', node[10:12])[0]
    offs = [struct.unpack('>H', node[size - 2 * (i + 1):size - 2 * i])[0] for i in range(nrec + 1)]
    return [(offs[i], offs[i + 1]) for i in range(nrec)]


def catalog_lookup(cat, path):
    node_size = struct.unpack('>H', cat[32:34])[0]
    n = struct.unpack('>I', cat[24:28])[0]
    index = {}
    while n:
        node = cat[n * node_size:(n + 1) * node_size]
        flink = struct.unpack('>I', node[0:4])[0]
        if struct.unpack('>b', node[8:9])[0] != -1:
            sys.exit(f'catalog node {n} is not a leaf')
        for lo, hi in records(node, node_size):
            klen, parent, nlen = struct.unpack('>HIH', node[lo:lo + 8])
            name = node[lo + 8:lo + 8 + 2 * nlen].decode('utf-16-be', errors='replace')
            index[(parent, name)] = node[lo + 2 + klen:hi]
        n = flink
    parent, data = 2, None
    parts = [p for p in path.split('/') if p]
    for i, part in enumerate(parts):
        data = index.get((parent, part))
        if data is None:
            sys.exit(f'{path}: {part} not found')
        rtype = struct.unpack('>h', data[:2])[0]
        if i < len(parts) - 1:
            if rtype != 1:
                sys.exit(f'{path}: {part} is not a folder')
            parent = struct.unpack('>I', data[8:12])[0]
    if struct.unpack('>h', data[:2])[0] != 2:
        sys.exit(f'{path} is not a file')
    lsize, _, ftotal = struct.unpack('>QII', data[88:104])
    ext = [struct.unpack('>II', data[104 + 8 * i:112 + 8 * i]) for i in range(8)]
    ext = [(s, c) for s, c in ext if c]
    if sum(c for _, c in ext) != ftotal:
        sys.exit(f'{path}: extents in the overflow file: not handled')
    return lsize, ext


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--file', action='append', default=[])
    ap.add_argument('--replace', nargs=2, metavar=('PATH', 'NEWSIZE'))
    ap.add_argument('--adb', default='adb.exe')
    a = ap.parse_args()
    dev = Phone(a.adb)
    b0 = dev.blocks(0, 1)
    vh = b0[1024:1536]
    sig, ver, attr, _, jib = struct.unpack('>2sHIII', vh[:16])
    bs, total, free = struct.unpack('>III', vh[0x28:0x34])
    if sig != b'H+' or ver != 4 or bs != BS:
        sys.exit(f'not the HFS+ volume expected: {sig} v{ver} block size {bs}')
    if not attr & 0x100 or attr & 0x4000:
        sys.exit(f'volume attributes {attr:#x}: not cleanly unmounted')
    ranges = [(0, 1), (total - 1, 1)]
    special = {}
    for name, at in (('allocation', 0x70), ('extents', 0xc0), ('catalog', 0x110), ('attributes', 0x160),
                     ('startup', 0x1b0)):
        size, ftotal, ext = fork(vh, at)
        if sum(c for _, c in ext) != ftotal:
            sys.exit(f'the {name} file has extents in the overflow file: not handled')
        special[name] = (size, ext)
        ranges += ext
    if attr & 0x2000:
        jinfo = dev.blocks(jib, 1)
        joff, jsize = struct.unpack('>QQ', jinfo[36:52])
        jhdr = dev.blocks(joff // BS, 1)
        for e in ('<', '>'):
            magic, endian, start, end = struct.unpack(e + 'IIQQ', jhdr[:24])
            if magic == 0x4A4E4C78 and endian == 0x12345678:
                break
        else:
            sys.exit('journal header not found')
        if start != end:
            sys.exit(f'the journal has transactions in it (start {start} end {end}): not clean')
        ranges += [(jib, 1), (joff // BS, jsize // BS)]

    cat_size, cat_ext = special['catalog']
    cat = b''.join(dev.blocks(s, c) for s, c in cat_ext)[:cat_size]
    files = list(a.file) + ([a.replace[0]] if a.replace else [])
    found = {}
    for path in files:
        lsize, ext = catalog_lookup(cat, path)
        found[path] = ext
        ranges += ext
        print(f'{path}: {lsize} bytes in {ext}')
    if a.replace:
        path, newsize = a.replace[0], int(a.replace[1])
        size, ext = special['allocation']
        bitmap = bytearray(b''.join(dev.blocks(s, c) for s, c in ext)[:size])
        for s, c in found[path]:
            for blk in range(s, s + c):
                bitmap[blk >> 3] &= ~(0x80 >> (blk & 7)) & 0xff
        need = (newsize + BS - 1) // BS + 256          # as tools/hfs-add-contiguous.py
        run, b = None, 1
        used = lambda blk: bitmap[blk >> 3] & (0x80 >> (blk & 7))
        while b + need <= total:
            if used(b):
                b += 1
                continue
            e = b
            while e < total and e - b < need and not used(e):
                e += 1
            if e - b >= need:
                run = b
                break
            b = e + 1
        if run is None:
            sys.exit(f'no free run of {need} blocks')
        print(f'{path} -> {newsize} bytes: hfs-add-contiguous will take the free run at {run} ({need} blocks); '
              f'pulled with 256 more')
        ranges.append((run, need + 256))

    # merge and pull
    ranges = sorted(set(ranges))
    merged = []
    for s, c in ranges:
        if merged and s <= merged[-1][0] + merged[-1][1]:
            ms, mc = merged[-1]
            merged[-1] = (ms, max(ms + mc, s + c) - ms)
        else:
            merged.append((s, c))
    with open(a.image, 'wb') as f:
        f.truncate(total * BS)
        for s, c in merged:
            f.seek(s * BS)
            f.write(dev.blocks(s, c))
    print(f'{a.image}: {total} blocks of {BS} ({total * BS} bytes, sparse); {free} free; {len(merged)} ranges, '
          f'{sum(c for _, c in merged)} blocks pulled ({dev.read_blocks} read in all)')


if __name__ == '__main__':
    main()
