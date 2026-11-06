#!/usr/bin/env python3
"""Write a small block-level fix into the ASUS I007 iosroot partition over adb (Magisk su).

DIR holds what tools/blkdiff-style preparation made on the PC from a pulled root image:
  blocks.txt   changed 4K block numbers (relative to the start of iosroot), ascending
  payload.bin  the new contents of those blocks, in the same order
  old.hash     blake2b-16 of each listed block in the pulled image before the edit
  new.hash     the same after the edit (16 bytes per listed block, same order)

Default (read-only): checks the partition, reads the target blocks from the phone into
DIR/backup.bin and verifies them against old.hash, i.e. that the phone still holds exactly
the pulled image there. With --write it then writes payload.bin run by run (dd to
/dev/block/by-name/iosroot only), reads the blocks back and verifies them against new.hash.
--restore writes DIR/backup.bin back instead (undo).

Nothing else on the phone is touched; the partition must not be mounted (Android never
mounts it).
"""
import argparse
import hashlib
import os
import subprocess
import sys

PART = '/dev/block/by-name/iosroot'
PART_BYTES = 34359717888
BS = 4096
TMP = '/data/local/tmp/asusfix'


def adb(*args, data=None):
    r = subprocess.run(['adb', *args], input=data, capture_output=True)
    if r.returncode:
        sys.exit(f'adb {" ".join(args)} failed: {r.stderr.decode(errors="replace")}')
    return r.stdout


def su(cmd):
    return adb('shell', f"su -c '{cmd}'").decode(errors='replace').replace('\r', '')


def h(blk):
    return hashlib.blake2b(blk, digest_size=16).digest()


def runs_of(blocks):
    runs = []
    for b in blocks:
        if runs and runs[-1][0] + runs[-1][1] == b:
            runs[-1][1] += 1
        else:
            runs.append([b, 1])
    return runs


def read_blocks(runs):
    out = b''
    for start, n in runs:
        part = adb('exec-out', f"su -c 'dd if={PART} bs={BS} skip={start} count={n} 2>/dev/null'")
        if len(part) != n * BS:
            sys.exit(f'short read at block {start}: {len(part)} bytes')
        out += part
    return out


def verify(data, blocks, hashes, what):
    bad = [b for i, b in enumerate(blocks)
           if h(data[i * BS:(i + 1) * BS]) != hashes[i * 16:i * 16 + 16]]
    if bad:
        print(f'{what}: {len(bad)} of {len(blocks)} blocks differ, first {bad[:10]}')
        return False
    print(f'{what}: all {len(blocks)} blocks match')
    return True


def write_runs(src_local, runs):
    data = open(src_local, 'rb').read()
    adb('shell', f'mkdir -p {TMP}')
    adb('push', src_local, f'{TMP}/data.bin')
    remote = su(f'sha256sum {TMP}/data.bin').split()[0]
    if remote != hashlib.sha256(data).hexdigest():
        sys.exit('pushed data does not match (sha256)')
    off, cmds = 0, []
    for start, n in runs:
        # No pipe after dd: set -e must see dd's own exit status.
        cmds.append(f'dd if={TMP}/data.bin of={PART} bs={BS} skip={off} seek={start} count={n} conv=notrunc 2>/dev/null')
        off += n
    script = '\n'.join(['set -e', *cmds, 'sync', 'echo WRITE-DONE']) + '\n'
    open(src_local + '.sh', 'w', newline='\n').write(script)
    adb('push', src_local + '.sh', f'{TMP}/write.sh')
    out = su(f'sh {TMP}/write.sh')
    print(out.strip().splitlines()[-1])
    if 'WRITE-DONE' not in out:
        sys.exit('write script did not finish:\n' + out)
    su(f'rm -f {TMP}/data.bin {TMP}/write.sh; echo 3 > /proc/sys/vm/drop_caches')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dir')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--write', action='store_true')
    g.add_argument('--restore', action='store_true')
    a = ap.parse_args()
    d = a.dir
    blocks = [int(x) for x in open(os.path.join(d, 'blocks.txt')).read().split()]
    assert blocks == sorted(set(blocks)) and blocks, 'blocks.txt must be ascending and unique'
    old = open(os.path.join(d, 'old.hash'), 'rb').read()
    new = open(os.path.join(d, 'new.hash'), 'rb').read()
    payload = os.path.join(d, 'payload.bin')
    assert os.path.getsize(payload) == len(blocks) * BS
    assert len(old) == len(new) == len(blocks) * 16 and blocks[-1] < PART_BYTES // BS - 2048
    for i in range(len(blocks)):
        assert h(open(payload, 'rb').read()[i * BS:(i + 1) * BS]) == new[i * 16:i * 16 + 16]
    runs = runs_of(blocks)
    print(f'{len(blocks)} blocks in {len(runs)} runs ({len(blocks) * BS // 1024} KiB)')

    size = su(f'blockdev --getsize64 {PART}').strip()
    if size != str(PART_BYTES):
        sys.exit(f'{PART}: unexpected size {size}')
    real = su(f'readlink -f {PART}').strip()
    if real in su('cat /proc/mounts'):
        sys.exit(f'{real} is mounted')

    cur = read_blocks(runs)
    if a.restore:
        backup = os.path.join(d, 'backup.bin')
        if not verify(cur, blocks, new, 'phone vs new.hash'):
            sys.exit('phone does not hold the fix; nothing to restore')
        write_runs(backup, runs)
        verify(read_blocks(runs), blocks, old, 'after restore vs old.hash') or sys.exit(1)
        return
    if verify(cur, blocks, new, 'phone vs new.hash (already fixed?)'):
        return
    with open(os.path.join(d, 'backup.bin'), 'wb') as f:
        f.write(cur)
    if not verify(cur, blocks, old, 'phone vs old.hash'):
        sys.exit('the phone root differs from the pulled image here; not writing')
    if not a.write:
        print('read-only check passed; rerun with --write to apply')
        return
    write_runs(payload, runs)
    verify(read_blocks(runs), blocks, new, 'after write vs new.hash') or sys.exit(1)
    print('fix written and verified')


if __name__ == '__main__':
    main()
