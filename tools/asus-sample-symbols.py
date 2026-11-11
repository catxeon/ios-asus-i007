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
        named = dict(out)
        try:
            for va, name in self.objc_methods(header):
                if named.get(va, '<redacted>') == '<redacted>':
                    named[va] = name
        except (KeyError, struct.error, ValueError):
            pass
        out = sorted(named.items())
        self.symbols[header] = (out, [s[0] for s in out])
        return self.symbols[header]

    # Objective-C methods: the cache keeps their names though the symbol table does not.
    # A pointer in the cache's data holds its address in the low 36 bits; a relative method
    # list (12-byte entries) names its selectors by offsets from one base for the whole
    # cache (libobjc's __objc_opt_ro, the 64-bit field at +40), checked on iOS 16.7's cache.
    PTR = 0xFFFFFFFFF

    def ptr(self, va):
        return struct.unpack('<Q', self.read(va, 8))[0] & self.PTR

    def cstring(self, va):
        return self.read(va, 200).split(b'\0')[0].decode(errors='replace')

    def sections(self, header):
        out = {}
        for cmd, c in self.commands(header):
            if cmd == 0x19:
                for k in range(struct.unpack_from('<I', c, 64)[0]):
                    so = 72 + 80 * k
                    out[c[so:so + 16].rstrip(b'\0').decode()] = struct.unpack_from('<QQ', c, so + 32)
        return out

    def selector_base(self):
        if not hasattr(self, 'selbase'):
            self.selbase = None
            for _, _, path, header in self.images:
                if path.endswith('/libobjc.A.dylib'):
                    opt = self.sections(header).get('__objc_opt_ro')
                    if opt:
                        self.selbase = opt[0] + struct.unpack('<q', self.read(opt[0] + 40, 8))[0]
        return self.selbase

    def method_list(self, ml, prefix, cls):
        if not ml:
            return
        flags, count = struct.unpack('<II', self.read(ml, 8))
        size = flags & 0xfffc                   # (the low two bits are flags too)
        if count > 5000 or size not in (12, 24):
            return
        for k in range(count):
            e = ml + 8 + size * k
            if flags & 0x80000000:
                name, _, imp = struct.unpack('<iii', self.read(e, 12))
                if not imp:
                    continue
                if flags & 0x40000000:
                    sel = self.cstring(self.selector_base() + name)
                else:
                    sel = self.cstring(self.ptr(e + name))
                va = e + 8 + imp
            else:
                sel = self.cstring(self.ptr(e))
                va = self.ptr(e + 16)
            if va:
                yield va, '%s[%s %s]' % (prefix, cls, sel)

    def class_name(self, cls):
        return self.cstring(self.ptr((self.ptr(cls + 32) & ~7) + 24))

    def objc_methods(self, header):
        secs = self.sections(header)
        addr, size = secs.get('__objc_classlist', (0, 0))
        for i in range(size // 8):
            cls = self.ptr(addr + 8 * i)
            name = self.class_name(cls)
            yield from self.method_list(self.ptr((self.ptr(cls + 32) & ~7) + 32), '-', name)
            meta = self.ptr(cls)
            if meta:
                yield from self.method_list(self.ptr((self.ptr(meta + 32) & ~7) + 32), '+', name)
        addr, size = secs.get('__objc_catlist', (0, 0))
        for i in range(size // 8):
            cat = self.ptr(addr + 8 * i)
            try:
                name = self.class_name(self.ptr(cat + 8))
            except (KeyError, struct.error):
                name = '?'
            name += '(' + self.cstring(self.ptr(cat)) + ')'
            yield from self.method_list(self.ptr(cat + 16), '-', name)
            yield from self.method_list(self.ptr(cat + 24), '+', name)

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
