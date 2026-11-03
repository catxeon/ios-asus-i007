#!/usr/bin/env python3
"""Pack a private HFS RAM root for our dyld probe, without restore jobs.

Inputs: extracted Apple restore root, own linked init, known-good empty/own-init
HFS+ base from asus-ramroot CI, output image, and libdmg-hfsplus's hfsplus tool.
Apple files are local inputs and must never be uploaded to public CI or git.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


def dependencies(data):
    if data[:4] == b'\xca\xfe\xba\xbe':
        count = struct.unpack_from('>I', data, 4)[0]
        arches = [struct.unpack_from('>5I', data, 8+20*i) for i in range(count)]
        arch = next(a for a in arches if a[0] == 0x100000c and a[1] & 255 == 0)
        data = data[arch[2]:arch[2]+arch[3]]
    magic, cpu, sub, _, count, cmds_size, _, _ = struct.unpack_from('<8I', data)
    if magic != 0xfeedfacf or cpu != 0x100000c or sub & 255:
        raise ValueError('Every input must have a plain arm64 Mach-O slice')
    out, offset = [], 32
    for _ in range(count):
        cmd, size = struct.unpack_from('<II', data, offset)
        if size < 8 or offset+size > 32+cmds_size:
            raise ValueError('Invalid Mach-O load command')
        if cmd in (0xe, 0xc, 0x80000018, 0x8000001f, 0x80000023):
            nameoff = struct.unpack_from('<I', data, offset+8)[0]
            if not 8 <= nameoff < size:
                raise ValueError('Invalid Mach-O dependency name')
            end = data.index(b'\0', offset+nameoff, offset+size)
            name = data[offset+nameoff:end].decode()
            if not name.startswith('/') or '..' in Path(name).parts:
                raise ValueError('Only absolute dependency paths are supported')
            out.append((name, cmd == 0x80000018))
        if cmd == 0x2c and struct.unpack_from('<I', data, offset+16)[0]:
            raise ValueError('Encrypted Mach-O input is not supported')
        offset += size
    return out


def build(args):
    root, init, base, output = (p.resolve() for p in
                               (args.restore_root, args.init, args.base, args.output))
    if output.exists():
        raise ValueError('Refusing to overwrite an existing root image')
    with base.open('rb') as f:
        f.seek(1024)
        if f.read(2) != b'H+':
            raise ValueError('Base must be a raw HFS+ volume')
    files, queue = {'/sbin/launchd': init}, ['/sbin/launchd']
    while queue:
        name = queue.pop(0)
        for dep, weak in dependencies(files[name].read_bytes()):
            if dep in files:
                continue
            path = (root / dep.lstrip('/')).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Dependency resolves outside the extracted root')
            if weak and not path.is_file():
                continue
            if not path.is_file():
                raise ValueError(f'Missing dependency: {dep}')
            files[dep] = path
            queue.append(dep)
    if '/usr/lib/dyld' not in files:
        raise ValueError('Init is not dynamically linked through /usr/lib/dyld')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='asus-dyld-root-', dir=output.parent) as tmp:
        stage = Path(tmp) / 'stage'
        for directory in ('dev', 'sbin', 'private/var', 'private/tmp',
                          'System/Library/LaunchDaemons'):
            (stage / directory).mkdir(parents=True, exist_ok=True)
        hashes = {}
        for name, source in files.items():
            target = stage / name.lstrip('/')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
        shutil.copyfile(base, output)
        def hfs(*commands, capture=False):
            return subprocess.run([str(args.hfsplus.resolve()), str(output), *commands],
                                  check=True, text=True, capture_output=capture)
        hfs('grow', str(32 << 20))
        hfs('addall', str(stage))
        for name in ('/sbin/launchd', '/usr/lib/dyld'):
            hfs('chmod', '0755', name)
            folder, filename = name.rsplit('/', 1)
            listing = hfs('ls', folder, capture=True).stdout.splitlines()
            if not any(line.startswith('100755 ') and line.endswith(' '+filename) for line in listing):
                raise ValueError(f'Execute mode was not preserved for {name}')
        for name, expected in hashes.items():
            target = Path(tmp) / 'roundtrip'
            hfs('extract', name, str(target))
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError(f'Packed file hash mismatch: {name}')
        subprocess.run([args.fsck, '-f', '-n', str(output)], check=True)
    manifest = {'purpose': 'own PID 1 through Apple dyld and standalone libSystem',
                'files': hashes, 'executable_modes': {'/sbin/launchd': '0755', '/usr/lib/dyld': '0755'},
                'image_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                'apple_launchd_or_restore_jobs_included': False}
    output.with_suffix('.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(f'PASS: {len(files)} files, execute modes, file hashes and HFS consistency checked')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('restore_root', 'init', 'base', 'output', 'hfsplus'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--fsck', default='/usr/sbin/fsck.hfsplus')
    build(parser.parse_args())
