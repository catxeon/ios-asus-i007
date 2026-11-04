#!/usr/bin/env python3
"""Prepare a private QEMU root containing the iOS shared cache and backboardd."""
import argparse
import hashlib
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile


CACHE_DIR = '/System/Library/Caches/com.apple.dyld'
CACHE_NAME = 'dyld_shared_cache_arm64'
JOB = '/System/Library/LaunchDaemons/com.apple.backboardd.plist'
PROBE_JOB = '/System/Library/LaunchDaemons/dev.asus.launchd-probe.plist'
CHUNK = 32 << 20


def sha256(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def build(args):
    if args.output.exists():
        raise ValueError('Refusing to overwrite an existing HFS image')
    cache_dir = args.systemos_root / CACHE_DIR.lstrip('/')
    files = sorted(p for p in cache_dir.glob(CACHE_NAME + '*')
                   if p.is_file() and not p.name.endswith('.symbols'))
    with (cache_dir / CACHE_NAME).open('rb') as f:
        if f.read(16).rstrip(b'\0 ') != b'dyld_v1   arm64':
            raise ValueError('Expected a plain arm64 dyld shared cache')
    if len(files) < 2:
        raise ValueError('Missing dyld shared cache subcaches')
    cache_bytes = sum(p.stat().st_size for p in files)
    size = (args.base.stat().st_size + cache_bytes + (96 << 20) + CHUNK - 1) // CHUNK * CHUNK
    args.output.parent.mkdir(parents=True, exist_ok=True)
    scratch = args.scratch or args.output.parent
    with tempfile.TemporaryDirectory(prefix='asus-compositor-', dir=scratch) as temp:
        tmp = Path(temp)
        image = tmp / 'root.hfs'
        shutil.copyfile(args.base, image)

        def hfs(*commands):
            return subprocess.run([str(args.hfsplus.resolve()), str(image), *commands],
                                  check=True, text=True, capture_output=True).stdout

        def own(path, *flags):
            subprocess.run([str(args.hfs_chown.resolve()), str(image), path, '0', '0', *flags],
                           check=True)

        def add(source, destination, mode, restricted=False):
            hfs('add', str(source), destination)
            hfs('chmod', mode, destination)
            own(destination, *(['--restricted'] if restricted else []))
            check = tmp / 'roundtrip'
            check.unlink(missing_ok=True)
            hfs('extract', destination, str(check))
            if not check.is_file() or sha256(check) != sha256(source):
                raise ValueError(f'HFS roundtrip failed for {destination}')

        hfs('grow', str(size))
        if image.stat().st_size != size:
            raise ValueError('HFS grow did not produce the expected size')
        for folder in ('/System/Library/Caches', CACHE_DIR):
            hfs('mkdir', folder)
            own(folder)
        for source in files:
            add(source, f'{CACHE_DIR}/{source.name}', '0644', restricted=True)
        print(f'Packed {len(files)} cache files ({cache_bytes} bytes)', flush=True)

        if args.os_root:
            os_root = args.os_root.resolve()
            stock = plistlib.loads((os_root / JOB.lstrip('/')).read_bytes())
            program = stock.get('Program') or stock['ProgramArguments'][0]
            if program != '/usr/libexec/backboardd':
                raise ValueError(f'Unexpected backboardd executable: {program}')
            add(os_root / program.lstrip('/'), program, '0755')
            job = {key: stock[key] for key in
                   ('Label', 'Program', 'ProgramArguments', 'MachServices', 'UserName',
                    'GroupName', 'EnvironmentVariables', 'ProcessType', 'POSIXSpawnType') if key in stock}
            job['RunAtLoad'] = True
            plist = tmp / 'backboardd.plist'
            plist.write_bytes(plistlib.dumps(job))
            # Replace the old probe job while keeping all other Apple launch jobs out.
            add(plist, PROBE_JOB, '0644')
            folder = '/System/Library/CoreServices'
            hfs('mkdir', folder)
            own(folder)
            version = f'{folder}/SystemVersion.plist'
            add(os_root / version.lstrip('/'), version, '0644')

        subprocess.run([args.fsck, '-f', '-n', str(image)], check=True)
        if args.scratch:
            with tempfile.TemporaryDirectory(prefix='asus-compositor-output-', dir=args.output.parent) as staging:
                destination = Path(staging) / 'root.hfs'
                shutil.copyfile(image, destination)
                destination.rename(args.output)
        else:
            image.rename(args.output)
    print(f'PASS: verified QEMU root SHA256 {sha256(args.output)}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('systemos_root', 'base', 'output', 'hfsplus', 'hfs_chown'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--os-root', type=Path, help='include the stock backboardd job')
    parser.add_argument('--scratch', type=Path, help='temporary build directory on a local Linux filesystem')
    parser.add_argument('--fsck', default='/usr/sbin/fsck.hfsplus')
    build(parser.parse_args())
