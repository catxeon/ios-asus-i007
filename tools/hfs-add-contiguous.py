#!/usr/bin/env python3
"""Add files to an HFS+ image (offline) so that each one lands in a single extent.

  hfs-add-contiguous.py IMAGE HFSPLUS_TOOL CHOWN_TOOL DEST_DIR FILE... [--mode 0755] [--uid 0 --gid 0]

The libdmg-hfsplus tool (HFSPLUS_TOOL) allocates linearly from the volume header's
nextAllocation. On a used root the free space there is fragmented; a 15 MB file then got
dozens of extents, the tool wrote them inconsistently ("inconsistent extents information!",
fsck: orphaned blocks). So, as tools/hfs-add-journal.py does for the journal: make DEST_DIR
(and its parents), find a free run in the allocation bitmap big enough for all files, point
nextAllocation at it, add the files, then check on disk that every file is there in one
piece (its bytes, contiguous, inside the run) and read each one back with the tool.

The image must be clean (unmounted, empty journal). fsck it (-n) afterwards.
"""
import argparse
import hashlib
import os
import struct
import subprocess
import sys


def header(f):
    f.seek(1024)
    return bytearray(f.read(512))


def read_bitmap(f, vh, bsize):
    data = b''
    for i in range(8):                                  # allocationFile fork at 0x70
        start, count = struct.unpack('>II', vh[0x70 + 16 + 8 * i:0x70 + 24 + 8 * i])
        if count:
            f.seek(start * bsize)
            data += f.read(count * bsize)
    return data


def tool(a, *args, check=True):
    r = subprocess.run([a.hfsplus, a.image, *args], capture_output=True, text=True, errors='replace')
    if check and r.returncode:
        sys.exit(f'hfsplus {" ".join(args)} failed: {r.stdout}{r.stderr}')
    return r.stdout + r.stderr


def names(a, path):
    out = []
    for line in tool(a, 'ls', path, check=False).splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 7:
            out.append(parts[-1])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('hfsplus')
    ap.add_argument('chown')
    ap.add_argument('dest')
    ap.add_argument('files', nargs='+')
    ap.add_argument('--mode', default='0755')
    ap.add_argument('--uid', default='0')
    ap.add_argument('--gid', default='0')
    a = ap.parse_args()
    dest = '/' + a.dest.strip('/')

    # the directories first (their catalog records may grow the catalog: before the run is chosen)
    path = ''
    for part in dest.strip('/').split('/'):
        parent = path or '/'
        path += '/' + part
        if part not in names(a, parent):
            tool(a, 'mkdir', path)
            tool(a, 'chmod', '0755', path)
            subprocess.run([a.chown, a.image, path, a.uid, a.gid], check=True)
            print(f'mkdir {path}')
    present = names(a, dest)
    for src in a.files:
        if os.path.basename(src) in present:
            sys.exit(f'{dest}/{os.path.basename(src)} exists already')

    with open(a.image, 'r+b') as f:
        vh = header(f)
        sig, _, attr = struct.unpack('>2sHI', vh[:8])
        if sig != b'H+' or not attr & (1 << 8):
            sys.exit('not a cleanly unmounted HFS+ volume')
        bsize, total, free = struct.unpack('>III', vh[0x28:0x34])
        sizes = [os.path.getsize(s) for s in a.files]
        need = sum((s + bsize - 1) // bsize for s in sizes) + 256     # + room for B-tree nodes
        bitmap = read_bitmap(f, vh, bsize)

        def used(blk):
            return bitmap[blk >> 3] & (0x80 >> (blk & 7))
        run, b = None, 1
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
        print(f'block size {bsize}, {free} of {total} free; free run at {run} for {need} blocks')
        struct.pack_into('>I', vh, 0x34, run)          # nextAllocation
        f.seek(1024)
        f.write(vh)

    # largest first: the run is used from its start
    order = sorted(range(len(a.files)), key=lambda i: -sizes[i])
    for i in order:
        src = a.files[i]
        dst = f'{dest}/{os.path.basename(src)}'
        tool(a, 'add', src, dst)
        tool(a, 'chmod', a.mode, dst)
        subprocess.run([a.chown, a.image, dst, a.uid, a.gid], check=True)

    ok = True
    with open(a.image, 'rb') as f:
        f.seek(run * bsize)
        area = f.read(need * bsize)
    for i in order:
        src = a.files[i]
        data = open(src, 'rb').read()
        at = area.find(data[:bsize]) if len(data) >= bsize else area.find(data)
        while at >= 0 and at % bsize:
            at = area.find(data[:bsize], at + 1)
        inside = at >= 0 and area[at:at + len(data)] == data
        back = os.path.join('/tmp', 'hfs-add-check.bin')
        if os.path.exists(back):
            os.unlink(back)
        tool(a, 'extract', f'{dest}/{os.path.basename(src)}', back, check=False)
        same = os.path.exists(back) and open(back, 'rb').read() == data
        print(f'{os.path.basename(src)}: {len(data)} bytes, ' +
              (f'contiguous at block {run + at // bsize}' if inside else 'NOT contiguous in the run') +
              f', read back {"equal" if same else "DIFFERENT"} (sha256 {hashlib.sha256(data).hexdigest()[:16]})')
        ok = ok and inside and same
    print(tool(a, 'ls', dest, check=False).strip())
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
