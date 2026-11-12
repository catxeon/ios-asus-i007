#!/usr/bin/env python3
"""Make a block patch that replaces one file of the iOS root in place (no allocation).

  hfs-replace-inplace.py (--phone | --image IMAGE) PATH NEWFILE OUTDIR [--expect-old-sha256 HEX]

PATH is the file on the volume (/asus/lib/libasus-gl.dylib). The file must sit in one
extent, and NEWFILE must fit into the blocks it already has: then only those data blocks and
the catalog node that holds the file's record (its logicalSize) change; the allocation
bitmap, the volume header and every other file stay as they are. --phone reads the iosroot
partition over adb (Magisk su, read only: dd); --image reads an HFS+ image file.

OUTDIR gets what tools/asus-iosroot-blockpatch.py takes (blocks.txt, payload.bin, old.hash,
new.hash) and info.txt; that tool checks the phone against old.hash again, keeps a backup,
writes only with --write and undoes with --restore.

Refuses: a volume that is not cleanly unmounted or has a journal with transactions in it,
a catalog with extents in the overflow file, a file in more than one extent, a NEWFILE
bigger than the file's blocks, and (with --expect-old-sha256) a file whose current bytes are
not those.
"""
import argparse
import hashlib
import os
import struct
import subprocess
import sys

PART = '/dev/block/by-name/iosroot'


class Phone:
    def __init__(self, bs=4096):
        self.bs = bs

    def blocks(self, start, n):
        r = subprocess.run(['adb', 'exec-out', f"su -c 'dd if={PART} bs={self.bs} skip={start} count={n} 2>/dev/null'"],
                           capture_output=True)
        if r.returncode or len(r.stdout) != n * self.bs:
            sys.exit(f'read of {n} blocks at {start} failed ({len(r.stdout)} bytes): {r.stderr.decode(errors="replace")}')
        return r.stdout


class Image:
    def __init__(self, path, bs=4096):
        self.f = open(path, 'rb')
        self.bs = bs

    def blocks(self, start, n):
        self.f.seek(start * self.bs)
        data = self.f.read(n * self.bs)
        if len(data) != n * self.bs:
            sys.exit(f'short read at block {start}')
        return data


def fork(vh, at):
    size, _, total = struct.unpack('>QII', vh[at:at + 16])
    ext = [struct.unpack('>II', vh[at + 16 + 8 * i:at + 24 + 8 * i]) for i in range(8)]
    return size, total, [(s, c) for s, c in ext if c]


def device_blocks(extents, first, n):
    """Device blocks of the fork's blocks first .. first+n-1."""
    out, base = [], 0
    for start, count in extents:
        for b in range(count):
            if first <= base + b < first + n:
                out.append(start + b)
        base += count
    if len(out) != n:
        sys.exit('fork block out of its extents')
    return out


def records(node, size):
    nrec = struct.unpack('>H', node[10:12])[0]
    offs = [struct.unpack('>H', node[size - 2 * (i + 1):size - 2 * i])[0] for i in range(nrec + 1)]
    return [(offs[i], offs[i + 1]) for i in range(nrec)]


def h(blk):
    return hashlib.blake2b(blk, digest_size=16).digest()


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--phone', action='store_true')
    g.add_argument('--image')
    ap.add_argument('path')
    ap.add_argument('newfile')
    ap.add_argument('outdir')
    ap.add_argument('--expect-old-sha256')
    a = ap.parse_args()

    dev = Phone() if a.phone else Image(a.image)
    vh = dev.blocks(0, 1)[1024:1536]
    sig, version, attrs, _, jib = struct.unpack('>HHIII', vh[:16])
    bs, total_blocks = struct.unpack('>II', vh[0x28:0x30])
    if sig != 0x482B or version != 4 or bs != dev.bs:
        sys.exit(f'not the HFS+ volume expected: signature {sig:#x} version {version} block size {bs}')
    if not attrs & 0x100:
        sys.exit(f'volume attributes {attrs:#x}: not cleanly unmounted')
    if attrs & 0x2000:
        jinfo = dev.blocks(jib, 1)
        joff = struct.unpack('>Q', jinfo[36:44])[0]
        jhdr = dev.blocks(joff // bs, 1)[joff % bs:joff % bs + 32]
        for e in ('<', '>'):
            magic, endian, start, end = struct.unpack(e + 'IIQQ', jhdr[:24])
            if magic == 0x4A4E4C78 and endian == 0x12345678:
                break
        else:
            sys.exit('journal header not found')
        if start != end:
            sys.exit(f'the journal has transactions in it (start {start} end {end}): not clean')

    csize, ctotal, cext = fork(vh, 0x110)
    if sum(c for _, c in cext) != ctotal:
        sys.exit('catalog has extents in the overflow file: not handled')
    cat = b''.join(dev.blocks(s, c) for s, c in cext)[:csize]
    node_size = struct.unpack('>H', cat[32:34])[0]
    first_leaf = struct.unpack('>I', cat[24:28])[0]

    # every leaf record: (parentID, name) -> (node number, data offset in the node, data)
    index, n, seen = {}, first_leaf, 0
    while n:
        node = cat[n * node_size:(n + 1) * node_size]
        flink = struct.unpack('>I', node[0:4])[0]
        kind = struct.unpack('>b', node[8:9])[0]           # (after fLink and bLink)
        if kind != -1:
            sys.exit(f'node {n} is not a leaf ({kind})')
        for lo, hi in records(node, node_size):
            klen, parent, nlen = struct.unpack('>HIH', node[lo:lo + 8])
            name = node[lo + 8:lo + 8 + 2 * nlen].decode('utf-16-be', errors='replace')
            index[(parent, name)] = (n, lo + 2 + klen, node[lo + 2 + klen:hi])
        n, seen = flink, seen + 1

    parent = 2
    parts = [p for p in a.path.split('/') if p]
    for i, part in enumerate(parts):
        if (parent, part) not in index:
            sys.exit(f'{part} not found under CNID {parent}')
        node_no, data_off, data = index[(parent, part)]
        rtype = struct.unpack('>h', data[:2])[0]
        if i < len(parts) - 1:
            if rtype != 1:
                sys.exit(f'{part} is not a folder')
            parent = struct.unpack('>I', data[8:12])[0]
    if rtype != 2:
        sys.exit(f'{a.path} is not a file')
    file_id = struct.unpack('>I', data[8:12])[0]
    lsize, _, ftotal = struct.unpack('>QII', data[88:104])
    fext = [struct.unpack('>II', data[104 + 8 * i:112 + 8 * i]) for i in range(8)]
    fext = [(s, c) for s, c in fext if c]
    if len(fext) != 1 or fext[0][1] != ftotal:
        sys.exit(f'{a.path}: {len(fext)} extents, {ftotal} blocks: only a file in one extent is handled')
    start, count = fext[0]

    new = open(a.newfile, 'rb').read()
    if len(new) > count * bs:
        sys.exit(f'{a.newfile}: {len(new)} bytes do not fit into the {count} blocks of {a.path}')
    old_data = dev.blocks(start, count)
    old_sha = hashlib.sha256(old_data[:lsize]).hexdigest()
    if a.expect_old_sha256 and not old_sha.startswith(a.expect_old_sha256.lower()):
        sys.exit(f'{a.path} now holds sha256 {old_sha}, not {a.expect_old_sha256}: not touched')

    # the catalog node with the new logicalSize, on its device blocks
    per_node = node_size // bs
    node_blocks = device_blocks(cext, node_no * per_node, per_node)
    old_node = cat[node_no * node_size:(node_no + 1) * node_size]
    if b''.join(dev.blocks(b, 1) for b in node_blocks) != old_node:
        sys.exit('catalog node read back differs (the volume changed while reading)')
    new_node = bytearray(old_node)
    struct.pack_into('>Q', new_node, data_off + 88, len(new))

    blocks = list(range(start, start + count)) + node_blocks
    old_payload = old_data + bytes(old_node)
    new_payload = new + bytes(count * bs - len(new)) + bytes(new_node)
    order = sorted(range(len(blocks)), key=lambda i: blocks[i])
    if len(set(blocks)) != len(blocks):
        sys.exit('data and catalog blocks overlap')
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, 'blocks.txt'), 'w') as f:
        f.write('\n'.join(str(blocks[i]) for i in order) + '\n')
    with open(os.path.join(a.outdir, 'payload.bin'), 'wb') as f:
        f.write(b''.join(new_payload[i * bs:(i + 1) * bs] for i in order))
    with open(os.path.join(a.outdir, 'old.hash'), 'wb') as f:
        f.write(b''.join(h(old_payload[i * bs:(i + 1) * bs]) for i in order))
    with open(os.path.join(a.outdir, 'new.hash'), 'wb') as f:
        f.write(b''.join(h(new_payload[i * bs:(i + 1) * bs]) for i in order))
    info = (f'{a.path}: CNID {file_id}, {lsize} bytes in blocks {start}..{start + count - 1} (sha256 {old_sha});\n'
            f'new: {len(new)} bytes (sha256 {hashlib.sha256(new).hexdigest()});\n'
            f'catalog node {node_no} ({node_size} bytes) at blocks {node_blocks}, logicalSize at {data_off + 88};\n'
            f'{seen} leaf nodes read, volume attributes {attrs:#x}; {len(blocks)} blocks in the patch\n')
    with open(os.path.join(a.outdir, 'info.txt'), 'w') as f:
        f.write(info)
    print(info, end='')


if __name__ == '__main__':
    main()
