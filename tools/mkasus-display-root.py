#!/usr/bin/env python3
"""Add the display probe and its libraries to a private ASUS launchd HFS root."""
import argparse
import hashlib
import importlib
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

from collections import deque

dependencies = importlib.import_module('mkasus-dyld-root').dependencies
PROBE = '/usr/libexec/asus-probe'
JOB = '/System/Library/LaunchDaemons/dev.asus.launchd-probe.plist'
ROOT_LIMIT = 104 << 20


def closure(root, probe):
    files = {PROBE: probe}
    queue = deque([probe])
    while queue:
        for name, weak in dependencies(queue.popleft().read_bytes()):
            if name in files:
                continue
            path = (root / name.lstrip('/')).resolve()
            if not path.is_relative_to(root):
                raise ValueError(f'Dependency resolves outside the restore root: {name}')
            if weak and not path.is_file():
                continue
            if not path.is_file():
                raise ValueError(f'Missing dependency: {name}')
            files[name] = path
            queue.append(path)
    return files


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def build(args):
    root, probe, base, output = (p.resolve() for p in
                                (args.restore_root, args.probe, args.base, args.output))
    if output.exists():
        raise ValueError('Refusing to overwrite an existing HFS image')
    files = closure(root, probe)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='asus-display-', dir=output.parent) as tmp:
        tmp = Path(tmp)
        image, roundtrip = tmp / 'root.hfs', tmp / 'extract'
        shutil.copyfile(base, image)

        def hfs(*commands):
            return subprocess.run([str(args.hfsplus.resolve()), str(image), *commands],
                                  check=True, text=True, capture_output=True).stdout

        def extract(name):
            roundtrip.unlink(missing_ok=True)
            hfs('extract', name, str(roundtrip))
            return roundtrip.is_file() and roundtrip.stat().st_size > 0

        if not extract(JOB) or plistlib.loads(roundtrip.read_bytes()).get('ProgramArguments') != [PROBE]:
            raise ValueError('Base image must contain the ASUS launchd probe job')
        hfs('grow', str(ROOT_LIMIT))
        stage = tmp / 'stage'
        stage.mkdir()
        hashes = {name: digest(path) for name, path in files.items()}
        for name, source in files.items():
            if name != PROBE and extract(name):
                if digest(roundtrip) != hashes[name]:
                    raise ValueError(f'Existing library differs from this restore: {name}')
                continue
            target = stage / name.lstrip('/')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        hfs('addall', str(stage))
        # addall skips existing file records. Replace only the job executable.
        hfs('add', str(probe), PROBE)
        hfs('chmod', '0755', PROBE)
        if not any(line.startswith('100755 ') and line.endswith(' asus-probe')
                   for line in hfs('ls', '/usr/libexec').splitlines()):
            raise ValueError('Display probe is not executable')
        for name, expected in hashes.items():
            if not extract(name) or digest(roundtrip) != expected:
                raise ValueError(f'Packed file hash mismatch: {name}')
        if image.stat().st_size != ROOT_LIMIT:
            raise ValueError('HFS grow did not produce the expected size')
        subprocess.run([args.fsck, '-f', '-n', str(image)], check=True)
        image.rename(output)
    print(f'PASS: {len(files)} Mach-O files verified, HFS SHA256 {digest(output)}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('restore_root', 'probe', 'base', 'output', 'hfsplus'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--fsck', default='/usr/sbin/fsck.hfsplus')
    build(parser.parse_args())
