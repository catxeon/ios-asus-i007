#!/usr/bin/env python3
"""Prepare the input of tools/asus-iosroot-blockpatch.py from a pulled iosroot image edited in place.

  asus-iosroot-blockdiff.py hash IMAGE HASHFILE        before editing: blake2b-16 of every 4K block
  asus-iosroot-blockdiff.py diff IMAGE HASHFILE DIR    after editing: the blocks that changed

`diff` writes DIR/blocks.txt (changed block numbers, ascending), payload.bin (their new
contents), old.hash (from HASHFILE) and new.hash. No second copy of the 12 GB image is needed;
the old contents are saved from the phone itself by asus-iosroot-blockpatch.py (backup.bin).
"""
import hashlib
import os
import sys

BS = 4096
CHUNK = 4096 * BS


def blocks(path):
    with open(path, 'rb') as f:
        n = 0
        while True:
            data = f.read(CHUNK)
            if not data:
                return
            assert len(data) % BS == 0, 'image size is not a multiple of 4096'
            for i in range(0, len(data), BS):
                yield n, data[i:i + BS]
                n += 1


def h(block):
    return hashlib.blake2b(block, digest_size=16).digest()


def main():
    if len(sys.argv) == 4 and sys.argv[1] == 'hash':
        with open(sys.argv[3], 'wb') as out:
            count = 0
            for _, block in blocks(sys.argv[2]):
                out.write(h(block))
                count += 1
        print(f'{count} blocks hashed')
    elif len(sys.argv) == 5 and sys.argv[1] == 'diff':
        image, hashfile, d = sys.argv[2:5]
        old = open(hashfile, 'rb').read()
        assert len(old) == os.path.getsize(image) // BS * 16, 'hash file does not belong to this image size'
        os.makedirs(d, exist_ok=True)
        changed = []
        with open(os.path.join(d, 'payload.bin'), 'wb') as payload, open(os.path.join(d, 'old.hash'), 'wb') as oh, \
                open(os.path.join(d, 'new.hash'), 'wb') as nh:
            for n, block in blocks(image):
                new = h(block)
                if new != old[n * 16:n * 16 + 16]:
                    changed.append(n)
                    payload.write(block)
                    oh.write(old[n * 16:n * 16 + 16])
                    nh.write(new)
        with open(os.path.join(d, 'blocks.txt'), 'w') as f:
            f.write('\n'.join(map(str, changed)) + '\n')
        runs = sum(1 for i, b in enumerate(changed) if i == 0 or changed[i - 1] + 1 != b)
        print(f'{len(changed)} changed blocks in {runs} runs ({len(changed) * BS // 1024} KiB) -> {d}')
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
