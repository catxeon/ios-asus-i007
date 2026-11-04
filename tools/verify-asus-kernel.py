#!/usr/bin/env python3
"""Refuse a generic/topaz kernel before creating an ASUS boot payload."""
from pathlib import Path
import argparse
import hashlib
import json
import struct
import sys
from mkpayload import parse_macho


def virtual_timer_only(data, header):
    """Audit the real fiq_context_init instructions, not a source-code string."""
    offset, symbols = 32, None
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, size = struct.unpack_from('<II', data, offset)
        if command == 2:
            symbols = struct.unpack_from('<4I', data, offset + 8)
        offset += size
    if not symbols:
        raise ValueError('No symbol table for timer audit')
    symoff, count, stroff, _ = symbols
    addresses, start = [], None
    for i in range(count):
        nameoff, kind, section, desc, address = struct.unpack_from('<IBBHQ', data, symoff + i * 16)
        if not address or kind & 0xe0 or (kind & 0xe) != 0xe:
            continue
        name = data[stroff+nameoff:data.index(b'\0', stroff+nameoff)]
        addresses.append(address)
        if name == b'_fiq_context_init':
            start = address
    if start is None:
        raise ValueError('No fiq_context_init symbol for timer audit')
    end = min((address for address in addresses if address > start), default=start+256)
    if not 0 < end - start <= 256 or (end - start) % 4:
        raise ValueError('Unexpected fiq_context_init layout')
    for segment in header['segs']:
        if segment['vmaddr'] <= start and end <= segment['vmaddr'] + segment['filesize']:
            fileoff = segment['fileoff'] + start - segment['vmaddr']
            for instruction, in struct.iter_unpack('<I', data[fileoff:fileoff + end - start]):
                if instruction & 0xffffffe0 in (0xd51be220, 0xd53be220):
                    raise ValueError('Trapped CNTP_CTL_EL0 access remains in fiq_context_init')
            return
    raise ValueError('Timer initialization is outside file-backed segments')


def verify(path, require_hfs=False, require_display=False):
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
    virtual_timer_only(data, header)
    has_hfs = all(marker in data for marker in
                  (b'_hfs_vfsops\0', b'_hfs_mountfs\0', b'_sweet_hfs_register\0'))
    if require_hfs and not has_hfs:
        raise ValueError('Expected the ASUS HFS RAM-root profile')
    has_display = all(marker in data for marker in
                      (b'ASUS FB: AppleCLCD', b'IOSurfaceRootUserClient', b'asus,display'))
    if require_display and not (has_display and has_hfs):
        raise ValueError('Expected the ASUS display and HFS RAM-root profile')
    return {'kernel_sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data), 'entry': hex(header['entry']), 'arm64e': False,
            'physical_timer_access': False, 'hfs': has_hfs, 'display': has_display}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel')
    parser.add_argument('--require-hfs', action='store_true')
    parser.add_argument('--require-display', action='store_true')
    args = parser.parse_args()
    print(json.dumps(verify(args.kernel, args.require_hfs, args.require_display), indent=2))
