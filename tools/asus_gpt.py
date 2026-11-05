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


HFS_TYPE = '48465300-0000-11aa-aa11-00306543ecac'    # Apple HFS+


def build(primary, backup, part, new_last, name, first, last, out_primary, out_backup):
    """New primary/backup GPT images: entry `part` ends at new_last, a new entry
    `name` covers first..last (Apple HFS+ type, random unique GUID). LBA 0 (protective
    MBR) and every other byte of the entry arrays are kept."""
    p, b = bytearray(Path(primary).read_bytes()), bytearray(Path(backup).read_bytes())
    hp = header(p[BS:2 * BS])
    pe, pok = entries(p[hp['ent_lba'] * BS:], hp)
    if not (hp['hcrc_ok'] and pok):
        raise SystemExit('input GPT is not valid')
    arr = bytearray(p[hp['ent_lba'] * BS:hp['ent_lba'] * BS + hp['nent'] * hp['esz']])
    target = [e for e in pe if e['name'] == part]
    if len(target) != 1 or any(e['name'] == name for e in pe):
        raise SystemExit('partition names do not fit the request')
    t = target[0]
    if not (t['first'] <= new_last < t['last'] and new_last < first <= last <= hp['last']):
        raise SystemExit('new layout overlaps or leaves the usable range')
    struct.pack_into('<Q', arr, t['i'] * hp['esz'] + 40, new_last)
    free = next(i for i in range(hp['nent']) if arr[i * hp['esz']:i * hp['esz'] + 16] == bytes(16))
    if free != max(e['i'] for e in pe) + 1:
        raise SystemExit('unexpected free slot')
    e = bytearray(hp['esz'])
    e[0:16] = uuid.UUID(HFS_TYPE).bytes_le
    e[16:32] = uuid.uuid4().bytes_le
    struct.pack_into('<QQQ', e, 32, first, last, 0)
    e[56:56 + 2 * len(name)] = name.encode('utf-16-le')
    arr[free * hp['esz']:(free + 1) * hp['esz']] = e
    ecrc = zlib.crc32(arr)

    def hdr(block, cur, alt, ent_lba):
        h = bytearray(block[:BS])
        struct.pack_into('<QQ', h, 24, cur, alt)
        struct.pack_into('<Q', h, 72, ent_lba)
        struct.pack_into('<I', h, 88, ecrc)
        struct.pack_into('<I', h, 16, 0)
        struct.pack_into('<I', h, 16, zlib.crc32(h[:hp['hsize']]))
        return h

    nblocks = hp['alt'] + 1
    base = nblocks - len(b) // BS                    # LBA of the backup dump's first block
    hb = header(b[-BS:])
    p[BS:2 * BS] = hdr(p[BS:2 * BS], hp['cur'], hp['alt'], hp['ent_lba'])
    p[hp['ent_lba'] * BS:hp['ent_lba'] * BS + len(arr)] = arr
    off = (hb['ent_lba'] - base) * BS
    b[off:off + len(arr)] = arr
    b[-BS:] = hdr(b[-BS:], hb['cur'], hb['alt'], hb['ent_lba'])
    Path(out_primary).write_bytes(p)
    Path(out_backup).write_bytes(b)


if __name__ == '__main__' and sys.argv[1] == 'build':
    # build PRIMARY BACKUP PART NEW_LAST NAME FIRST LAST OUT_PRIMARY OUT_BACKUP
    a = sys.argv[2:]
    build(a[0], a[1], a[2], int(a[3]), a[4], int(a[5]), int(a[6]), a[7], a[8])
    print('built', a[7], a[8])

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
