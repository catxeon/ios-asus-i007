#!/usr/bin/env python3
"""Refuse a generic/topaz kernel before creating an ASUS boot payload."""
from pathlib import Path
import hashlib
import json
import sys
from mkpayload import parse_macho


def verify(path):
    data = Path(path).read_bytes()
    header = parse_macho(data)
    if header['cputype'] != 0x100000c or header['cpusub'] != 0 or not header['entry']:
        raise ValueError('Expected a plain arm64 ASUS kernel with a valid entry')
    # Clang can inline/unroll constant early-console strings into glyph stores.
    # Require both unique ASUS platform strings and the linked console symbols.
    for marker in (b'Cortex-A55 (ASUS I007)', b'ASUS Smartphone for Snapdragon Insiders',
                   b'_sweet_early_mark\0', b'_sweet_early_relocate\0'):
        if marker not in data:
            raise ValueError(f'Missing ASUS build marker: {marker!r}')
    for marker in (b'SWEET UFS:', b'TOPAZ GPU', b'TOPAZ MODEM'):
        if marker in data:
            raise ValueError(f'Unexpected board peripheral code: {marker!r}')
    return {'kernel_sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data), 'entry': hex(header['entry']), 'arm64e': False}


if __name__ == '__main__':
    print(json.dumps(verify(sys.argv[1]), indent=2))
