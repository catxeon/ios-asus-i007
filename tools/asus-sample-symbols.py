#!/usr/bin/env python3
"""Name the addresses of the kernel's process samples and crash reports (SMP / CRASH lines).

  asus-sample-symbols.py CACHE LOG [NAME ...]

CACHE  dyld_shared_cache_arm64 with its sub-cache files next to it (taken out of the root
       image: /System/Library/Caches/com.apple.dyld/)
LOG    the kernel log (log.txt of tools/asus-phone-cycle.sh)
NAME   only these processes (as in the SMP lines)

The log's lines (boot-arg asussample=NAME,..., xnu/asus/9999-asus-i007-zcrash.patch):

  SMP name[pid] slide 0x... mh 0x...
  SMP name tN pc 0x... lr 0x... sp 0x...
  SMP name tN bt 0x... 0x... ...            (six return addresses a line)

An address in the shared cache is unslid (address - slide) and named by the nearest symbol
below it in its library's symbol table (the cache on the phone has no local symbols file, so
static functions and most Objective-C methods carry the name of the last exported symbol
before them: read "symbol + offset" with that in mind). An address in the main binary is
shown as its offset from the Mach-O header. No ipsw needed.
"""
import bisect, re, struct, sys
from pathlib import Path


class Cache:
    def __init__(self, path):
        path = Path(path)
        self.maps = []
        self.files = {}
        for p in sorted(path.parent.glob(path.name + '*')):
            with p.open('rb') as f:
                head = f.read(0x200)
                if head[:7] != b'dyld_v1':
                    continue
                start, count = struct.unpack_from('<II', head, 16)
                f.seek(start)
                table = f.read(32 * count)
            for i in range(count):
                va, size, off, _, _ = struct.unpack_from('<QQQII', table, 32 * i)
                self.maps.append((va, size, off, p))
        self.maps.sort()
        main = path.read_bytes()
        off, count = struct.unpack_from('<II', main, 0x1c0)
        self.images = []                # (text start, text end, name, header)
        for i in range(count):
            addr, _, _, name_off, _ = struct.unpack_from('<QQQII', main, off + 32 * i)
            name = main[name_off:main.index(b'\0', name_off)].decode()
            size = self.text_size(addr)
            self.images.append((addr, addr + size, name, addr))
        self.images.sort()
        self.starts = [i[0] for i in self.images]
        self.symbols = {}

    def read(self, va, size):
        for start, length, off, p in self.maps:
            if start <= va and va + size <= start + length:
                f = self.files.get(p)
                if f is None:
                    f = self.files[p] = p.open('rb')
                f.seek(off + va - start)
                return f.read(size)
        raise KeyError(hex(va))

    def commands(self, header):
        _, _, _, _, count, size, _, _ = struct.unpack('<8I', self.read(header, 32))
        cmds = self.read(header + 32, size)
        off = 0
        for _ in range(count):
            cmd, n = struct.unpack_from('<II', cmds, off)
            yield cmd, cmds[off:off + n]
            off += n

    def text_size(self, header):
        for cmd, c in self.commands(header):
            if cmd == 0x19 and c[8:24].rstrip(b'\0') == b'__TEXT':
                return struct.unpack_from('<Q', c, 32)[0]
        return 0

    def table(self, header):
        if header in self.symbols:
            return self.symbols[header]
        sym = link = None
        for cmd, c in self.commands(header):
            if cmd == 2:
                sym = struct.unpack_from('<4I', c, 8)
            if cmd == 0x19 and c[8:24].rstrip(b'\0') == b'__LINKEDIT':
                link = struct.unpack_from('<3Q', c, 24)          # vmaddr, vmsize, fileoff
        out = []
        if sym and link:
            symoff, nsyms, stroff, strsize = sym
            base = link[0] - link[2]
            try:
                nlist = self.read(base + symoff, nsyms * 16)
                strings = self.read(base + stroff, strsize)
            except KeyError:
                nlist = strings = b''
            for strx, kind, _, _, va in struct.iter_unpack('<IBBHQ', nlist):
                if va and not kind & 0xe0 and (kind & 0x0e) == 0x0e and strx < len(strings):
                    out.append((va, strings[strx:strings.index(b'\0', strx)].decode(errors='replace')))
        out.sort()
        self.symbols[header] = (out, [s[0] for s in out])
        return self.symbols[header]

    def name(self, va):
        i = bisect.bisect_right(self.starts, va) - 1
        if i < 0 or not self.images[i][0] <= va < self.images[i][1]:
            return None
        start, _, path, header = self.images[i]
        syms, addrs = self.table(header)
        j = bisect.bisect_right(addrs, va) - 1
        lib = path.rsplit('/', 1)[-1]
        if j < 0:
            return '%s +%#x' % (lib, va - start)
        if syms[j][1] == '<redacted>':          # a local function: its start is all there is
            return '%s +%#x (function at +%#x, +%d)' % (lib, va - start, syms[j][0] - start, va - syms[j][0])
        return '%s %s+%d' % (lib, syms[j][1], va - syms[j][0])


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cache = Cache(sys.argv[1])
    only = set(sys.argv[3:])
    slide = mh = 0
    known = {}
    for line in open(sys.argv[2], errors='replace'):
        line = line.rstrip('\n')
        m = re.search(r'(SMP|CRASH) (\S+?)(\[\d+\])? ', line)
        if not m:
            continue
        if only and m.group(2) not in only:
            continue
        print(line[m.start():])
        s = re.search(r'slide (0x[0-9a-f]+) mh (0x[0-9a-f]+)', line)
        if s:
            slide, mh = int(s.group(1), 16), int(s.group(2), 16)
            continue
        for a in re.findall(r'0x[0-9a-f]{8,}', line[line.find(' pc ') if ' pc ' in line else line.find(' bt'):]):
            v = int(a, 16)
            if v not in known:
                n = cache.name(v - slide) if v >= slide else None
                if n is None and mh and mh <= v < mh + 0x4000000:
                    n = 'main binary +%#x' % (v - mh)
                known[v] = n
            if known[v]:
                print('    %s  %s' % (a, known[v]))


if __name__ == '__main__':
    main()
