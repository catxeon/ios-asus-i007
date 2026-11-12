#!/usr/bin/env python3
"""Read the kernel's screenshots (boot-arg asusshot=SECONDS) from the end of iosroot as PNGs.

  asus-shot-pull.py OUTDIR [--since-seq N]

From Android (adb, Magisk su, read only: dd). The area is the one bsd/dev/sweet_ufs.c
writes: 64 slots of 145 blocks right before the 64-block firmware area and the 1025-block
console log at the partition's end; a slot is a header block ("ASUSSHOT", version, width,
height, bytes, seq, uptime) and RGB565 pixels. Every slot with a header becomes
OUTDIR/shot-SEQ-UPTIMEs.png (newest boot only: a slot of an older boot has a seq that does
not fit the run of the newest; see --since-seq). No PIL needed.
"""
import argparse
import os
import struct
import subprocess
import sys
import zlib

PART = '/dev/block/by-name/iosroot'
BS = 4096
SLOTS, SLOT_BLOCKS, FW_BLOCKS, LOG_BLOCKS = 64, 145, 64, 1025


def adb(*args):
    r = subprocess.run(['adb', *args], capture_output=True)
    if r.returncode:
        sys.exit(f'adb {" ".join(args)} failed: {r.stderr.decode(errors="replace")}')
    return r.stdout


def png(path, width, height, rgb):
    raw = b''.join(b'\0' + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)

    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
                chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('outdir')
    ap.add_argument('--since-seq', type=int, default=None)
    ap.add_argument('--count', type=int, default=None,
                    help="only seq 0..COUNT-1: a boot that took fewer shots than the last one leaves that one's "
                         "later slots behind, which look like its continuation")
    a = ap.parse_args()
    size = int(adb('shell', f"su -c 'blockdev --getsize64 {PART}'").decode().strip())
    base = size // BS - LOG_BLOCKS - FW_BLOCKS - SLOTS * SLOT_BLOCKS
    data = adb('exec-out', f"su -c 'dd if={PART} bs={BS} skip={base} count={SLOTS * SLOT_BLOCKS} 2>/dev/null'")
    if len(data) != SLOTS * SLOT_BLOCKS * BS:
        sys.exit(f'short read: {len(data)} bytes')
    shots = []
    for slot in range(SLOTS):
        off = slot * SLOT_BLOCKS * BS
        magic, version, w, h, nbytes, seq, ns = struct.unpack_from('<8sIIIIQQ', data, off)
        if magic != b'ASUSSHOT' or version != 1 or nbytes != w * h * 2 or nbytes > (SLOT_BLOCKS - 1) * BS:
            continue
        if seq % SLOTS != slot:
            continue
        shots.append((seq, ns, w, h, data[off + BS:off + BS + nbytes]))
    if not shots:
        print('no screenshots in the area')
        return
    shots.sort()
    # the newest boot's shots: seq counts up from 0 in each boot, uptime with it
    newest = [shots[-1]]
    for s in reversed(shots[:-1]):
        if s[0] == newest[-1][0] - 1 and s[1] < newest[-1][1]:
            newest.append(s)
        else:
            break
    keep = [s for s in reversed(newest) if (a.since_seq is None or s[0] >= a.since_seq) and
            (a.count is None or s[0] < a.count)]
    os.makedirs(a.outdir, exist_ok=True)
    for seq, ns, w, h, px in keep:
        rgb = bytearray(w * h * 3)
        for i in range(w * h):
            v = px[2 * i] | (px[2 * i + 1] << 8)
            r, g, b = (v >> 11) & 0x1f, (v >> 5) & 0x3f, v & 0x1f
            rgb[3 * i:3 * i + 3] = bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
        name = os.path.join(a.outdir, 'shot-%03d-%04ds.png' % (seq, ns // 1000000000))
        png(name, w, h, bytes(rgb))
    print(f'{len(keep)} screenshots ({len(shots)} slots used) -> {a.outdir}: seq {keep[0][0]}..{keep[-1][0]}, '
          f'uptime {keep[0][1] // 10**9}..{keep[-1][1] // 10**9} s')


if __name__ == '__main__':
    main()
