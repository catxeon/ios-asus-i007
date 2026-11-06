#!/usr/bin/env python3
"""Add an in-filesystem journal to an existing non-journaled HFS+ image (offline).

  hfs-add-journal.py IMAGE HFSPLUS_TOOL [--mib 8]

The XNU HFS code then finds the journal at the next read-write mount: lastMountedVersion is
not 'HFSJ', so hfs_mounthfsplus runs hfs_late_journal_init, which looks up
/.journal_info_block and /.journal by name, fixes up offset and size from the files'
first extents and, with kJIJournalNeedInitMask set, creates an empty journal
(journal_create). After that a panic or reset only needs a journal replay at the next
mount, not an fsck.

Steps: find a free run of 1 + N blocks in the allocation bitmap, point the header's
nextAllocation at it so the libdmg-hfsplus tool (HFSPLUS_TOOL, its linear allocator)
places both files there, add the files, verify on disk where they landed (marker
pattern), zero the journal area, write the journal info block, set kHFSVolumeJournaledBit
and journalInfoBlock in the volume header.
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile

JOURNALED = 1 << 13
JIB_IN_FS = 1
JIB_NEED_INIT = 4


def header(f):
    f.seek(1024)
    return bytearray(f.read(512))


def fork_extents(vh, off):
    return [struct.unpack('>II', vh[off + 16 + 8 * i:off + 24 + 8 * i]) for i in range(8)]


def read_bitmap(f, vh, bsize):
    data = b''
    for start, count in fork_extents(vh, 0x70):    # allocationFile fork
        if count:
            f.seek(start * bsize)
            data += f.read(count * bsize)
    return data


def used(bitmap, blk):
    return bitmap[blk >> 3] & (0x80 >> (blk & 7))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('hfsplus')
    ap.add_argument('--mib', type=int, default=8)
    a = ap.parse_args()

    with open(a.image, 'r+b') as f:
        vh = header(f)
        sig, ver, attr = struct.unpack('>2sHI', vh[:8])
        if sig != b'H+':
            sys.exit('not an HFS+ volume')
        if attr & JOURNALED:
            sys.exit('already journaled')
        bsize, total, free = struct.unpack('>III', vh[0x28:0x34])
        nblocks = a.mib * 1024 * 1024 // bsize
        need = 1 + nblocks
        bitmap = read_bitmap(f, vh, bsize)
        run = None
        b = 1
        while b + need <= total:
            if used(bitmap, b):
                b += 1
                continue
            e = b
            while e < total and e - b < need and not used(bitmap, e):
                e += 1
            if e - b >= need:
                run = b
                break
            b = e + 1
        if run is None:
            sys.exit(f'no free run of {need} blocks')
        print(f'block size {bsize}, {free} of {total} free; free run at {run} for 1 + {nblocks} blocks')
        struct.pack_into('>I', vh, 0x34, run)      # nextAllocation
        f.seek(1024)
        f.write(vh)

    with tempfile.TemporaryDirectory() as tmp:
        jib_src = os.path.join(tmp, 'jib')
        jnl_src = os.path.join(tmp, 'jnl')
        open(jib_src, 'wb').write(b'ASUSJIB\0' + bytes(bsize - 8))
        with open(jnl_src, 'wb') as j:
            for i in range(nblocks):
                j.write(b'ASUSJNL\0' + struct.pack('>I', i) + bytes(bsize - 12))
        for src, dst in ((jib_src, '/.journal_info_block'), (jnl_src, '/.journal')):
            r = subprocess.run([a.hfsplus, a.image, 'add', src, dst], capture_output=True, text=True)
            if r.returncode:
                sys.exit(f'hfsplus add {dst} failed: {r.stdout}{r.stderr}')

    with open(a.image, 'r+b') as f:
        # Where did the files land? Both must be contiguous; the journal only uses the
        # first extent of /.journal.
        f.seek(run * bsize)
        area = f.read((need + 64) * bsize)
        jib_blk = None
        jnl_blk = None
        for i in range(need + 64):
            blk = area[i * bsize:i * bsize + 12]
            if blk[:8] == b'ASUSJIB\0' and jib_blk is None:
                jib_blk = run + i
            if blk == b'ASUSJNL\0' + struct.pack('>I', 0) and jnl_blk is None:
                jnl_blk = run + i
        if jib_blk is None or jnl_blk is None:
            sys.exit('files not found in the free run (allocator went elsewhere); image changed, fsck it')
        f.seek(jnl_blk * bsize)
        for i in range(nblocks):
            if f.read(bsize)[:12] != b'ASUSJNL\0' + struct.pack('>I', i):
                sys.exit(f'journal file not contiguous at block {jnl_blk + i}')
        print(f'/.journal_info_block at block {jib_blk}, /.journal at {jnl_blk}..{jnl_blk + nblocks - 1}')
        f.seek(jnl_blk * bsize)
        for i in range(nblocks):
            f.write(bytes(bsize))
        jib = struct.pack('>I32xQQ', JIB_IN_FS | JIB_NEED_INIT, jnl_blk * bsize, nblocks * bsize)
        f.seek(jib_blk * bsize)
        f.write(jib + bytes(bsize - len(jib)))
        vh = header(f)
        attr = struct.unpack('>I', vh[4:8])[0] | JOURNALED
        struct.pack_into('>I', vh, 4, attr)
        struct.pack_into('>I', vh, 0x0c, jib_blk)  # journalInfoBlock
        f.seek(1024)
        f.write(vh)
        # The alternate volume header (1024 bytes before the end) gets the same fields.
        size = total * bsize
        f.seek(size - 1024)
        alt = bytearray(f.read(512))
        if alt[:2] == b'H+':
            struct.pack_into('>I', alt, 4, attr)
            struct.pack_into('>I', alt, 0x0c, jib_blk)
            f.seek(size - 1024)
            f.write(alt)
        print(f'volume attributes 0x{attr:x}, journalInfoBlock {jib_blk}: journal created at the next read-write mount')


if __name__ == '__main__':
    main()
