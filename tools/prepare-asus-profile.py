#!/usr/bin/env python3
"""Stage the minimal ASUS profile into a fresh pinned darwin-xnu-build checkout."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

repo = Path(__file__).resolve().parents[1]
dxb = Path(sys.argv[1]).resolve()
if not (dxb / 'build.sh').is_file() or not (dxb / 'patches').is_dir():
    raise SystemExit('Expected a fresh darwin-xnu-build checkout')
if any(p.name[:4].isdigit() for p in (dxb / 'patches').glob('*')):
    raise SystemExit('Refusing to mix ASUS and an existing numbered patch profile')
selected = sorted(p for p in (repo / 'xnu/patches').glob('*.patch')
                  if p.name[:4].isdigit() and 1 <= int(p.name[:4]) <= 25)
if [int(p.name[:4]) for p in selected] != list(range(1, 26)):
    raise SystemExit('Incomplete minimal kernel patch series')
selected += [repo / 'xnu/patches/0019-stage-libpthread.sh',
             repo / 'xnu/patches/0020-stage-bearssl.sh',
             repo / 'xnu/asus/9999-asus-i007-bringup.patch']
manifest = []
for path in selected:
    data = path.read_bytes().replace(b'\r\n', b'\n')
    (dxb / 'patches' / path.name).write_bytes(data)
    manifest.append({'file': path.name, 'sha256': hashlib.sha256(data).hexdigest()})

script = (dxb / 'build.sh').read_text()
old = 'ARCHS="x86_64 arm64e" VALID_ARCHS="x86_64 arm64e"'
if script.count(old) != 1:
    raise SystemExit('Pinned libfirehose build command changed')
script = script.replace(old, 'ARCHS="arm64" VALID_ARCHS="arm64" OTHER_CFLAGS="-mkernel -nostdinc -Wno-packed -mcpu=cortex-a55"')
old = '                warning "Skipping patch ${PATCH} (already upstream, previously applied, or malformed): ${APPLY_CHECK}"'
if script.count(old) != 1:
    raise SystemExit('Pinned patch application loop changed')
script = script.replace(old, '''                if [[ "$(basename "$PATCH")" =~ ^[0-9] ]]; then
                    error "Required ASUS profile patch failed: ${PATCH}: ${APPLY_CHECK}"
                    exit 1
                fi
''' + old)
(dxb / 'build.sh').write_text(script, newline='\n')
(dxb / 'asus-profile.json').write_text(json.dumps({
    'device': 'ASUS_I007D', 'soc': 'SM8350', 'xnu': 'xnu-8796.141.3',
    'cpu': 'cortex-a55', 'cpus': 1, 'page_size': 16384,
    'root': 'mockfs RAM disk', 'storage_driver': False, 'patches': manifest,
}, indent=2) + '\n', newline='\n')
print(f'Staged {len(selected)} ASUS files; no UFS, input, modem, GPU or USB drivers')
