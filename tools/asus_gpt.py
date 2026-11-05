"""Parse (and later rebuild) the ASUS I007 LUN 0 GPT from 4 KiB-block dumps.

usage: asus_gpt.py show PRIMARY.bin BACKUP.bin
PRIMARY = LBA 0..5 of sda (4 KiB blocks), BACKUP = the last 6 LBAs.
Checks header and entry-array CRC32s of both copies and prints the entries.
"""
import struct
import sys
import uuid
import zlib
from pathlib import Path

BS = 4096


def header(block):
    if block[:8] != b'EFI PART':
        raise ValueError('no GPT signature')
    (rev, hsize, hcrc, _, cur, alt, first, last, guid, ent_lba, nent, esz,
     ecrc) = struct.unpack_from('<IIIIQQQQ16sQIII', block, 8)
    h = bytearray(block[:hsize])
    struct.pack_into('<I', h, 16, 0)
    return dict(rev=rev, hsize=hsize, hcrc=hcrc, hcrc_ok=zlib.crc32(h) == hcrc, cur=cur, alt=alt,
                first=first, last=last, disk=str(uuid.UUID(bytes_le=guid)), ent_lba=ent_lba,
                nent=nent, esz=esz, ecrc=ecrc)


def entries(raw, h):
    arr = raw[:h['nent'] * h['esz']]
    out = []
    for i in range(h['nent']):
        e = arr[i * h['esz']:(i + 1) * h['esz']]
        tguid, uguid, first, last, attr = struct.unpack_from('<16s16sQQQ', e)
        if tguid == bytes(16):
            continue
        name = e[56:128].decode('utf-16-le').split('\0')[0]
        out.append(dict(i=i, name=name, first=first, last=last, attr=attr,
                        type=str(uuid.UUID(bytes_le=tguid)), uuid=str(uuid.UUID(bytes_le=uguid))))
    return out, zlib.crc32(arr) == h['ecrc']


def load(primary, backup):
    p, b = Path(primary).read_bytes(), Path(backup).read_bytes()
    hp = header(p[BS:2 * BS])
    pe, pok = entries(p[hp['ent_lba'] * BS:], hp)
    hb = header(b[-BS:])
    nblocks = hp['alt'] + 1
    off = (hb['ent_lba'] - (nblocks - len(b) // BS)) * BS
    be, bok = entries(b[off:], hb)
    return hp, pe, pok, hb, be, bok


if __name__ == '__main__' and sys.argv[1] == 'show':
    hp, pe, pok, hb, be, bok = load(sys.argv[2], sys.argv[3])
    print(f"primary: hdr crc {'ok' if hp['hcrc_ok'] else 'BAD'}, entries crc {'ok' if pok else 'BAD'}, "
          f"cur {hp['cur']} alt {hp['alt']} usable {hp['first']}..{hp['last']} entries@{hp['ent_lba']} "
          f"{hp['nent']}x{hp['esz']}")
    print(f"backup:  hdr crc {'ok' if hb['hcrc_ok'] else 'BAD'}, entries crc {'ok' if bok else 'BAD'}, "
          f"cur {hb['cur']} alt {hb['alt']} entries@{hb['ent_lba']}")
    print('entries identical:', pe == be)
    for e in pe:
        print(f"#{e['i']:2} {e['name']:<18} {e['first']:>10} {e['last']:>10} "
              f"{(e['last'] - e['first'] + 1) * BS / 2**20:>10.1f} MiB attr {e['attr']:#x}")
