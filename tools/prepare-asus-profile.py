#!/usr/bin/env python3
"""Stage the minimal ASUS profile into a fresh pinned darwin-xnu-build checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

repo = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('dxb', type=Path)
parser.add_argument('--hfs', action='store_true', help='Include built-in HFS+ and MD5 for a filesystem RAM root')
parser.add_argument('--display', action='store_true', help='Include ASUS boot framebuffer presentation')
parser.add_argument('--compositor', action='store_true', help='Include the QEMU compositor signature policy')
parser.add_argument('--userland', action='store_true',
                    help='Include iOS daemon support: permissive Sandbox policy, rootrw=1, '
                         'HFS rename swap, no external ACL resolver, pid_hibernate syscall')
parser.add_argument('--input', action='store_true',
                    help='Include HID event services (touch, buttons) and the QEMU virtio input '
                         'source (boot-arg asus_virtio_input=1)')
parser.add_argument('--ufs', action='store_true',
                    help='Include the UFS driver (sweet 0028/0030/0034/0069; writes only to the '
                         'iosroot partition with ufsrw=1; ufsprobe=1, ufslist=1, rd=ufsroot0)')
parser.add_argument('--smp', action='store_true',
                    help='Include sweet SMP via PSCI CPU_ON (0065 + 0078, regenerated on top of '
                         'the ASUS patches): opt-in with boot-arg cpus=N, N <= 8')
parser.add_argument('--amp', action='store_true',
                    help='With --smp: A55 E cluster + A78/X1 P cluster (__ARM_AMP__, 2 clusters); '
                         'images need the ADT from mkadt-asus.py --amp')
args = parser.parse_args()
if args.ufs and not args.hfs:
    parser.error('--ufs requires --hfs')
if args.input and not args.hfs:
    parser.error('--input requires --hfs')
if args.userland and not args.hfs:
    parser.error('--userland requires --hfs')
if args.display and not args.hfs:
    parser.error('--display requires --hfs')
if args.compositor and not args.display:
    parser.error('--compositor requires --display')
dxb = args.dxb.resolve()
if not (dxb / 'build.sh').is_file() or not (dxb / 'patches').is_dir():
    raise SystemExit('Expected a fresh darwin-xnu-build checkout')
if any(p.name[:4].isdigit() for p in (dxb / 'patches').glob('*')):
    raise SystemExit('Refusing to mix ASUS and an existing numbered patch profile')
last = 27 if args.hfs else 25
selected = sorted(p for p in (repo / 'xnu/patches').glob('*.patch')
                  if p.name[:4].isdigit() and 1 <= int(p.name[:4]) <= last)
if [int(p.name[:4]) for p in selected] != list(range(1, last + 1)):
    raise SystemExit('Incomplete minimal kernel patch series')
selected += [repo / 'xnu/patches/0019-stage-libpthread.sh',
             repo / 'xnu/patches/0020-stage-bearssl.sh',
             repo / 'xnu/asus/9999-asus-i007-bringup.patch']
if args.hfs:
    selected.append(repo / 'xnu/patches/0026-stage-hfs.sh')
    selected += [repo / 'xnu/patches/0032-sweet-bearssl-sha1.patch',
                 repo / 'xnu/patches/0033-sweet-bearssl-sha384-sha512.patch']
    selected.append(repo / 'xnu/asus/9998-asus-exec-diagnostics.patch')
if args.display:
    selected.append(repo / 'xnu/asus/9999-asus-i007-display.patch')
if args.compositor:
    selected.append(repo / 'xnu/asus/9999-asus-i007-qemu-compositor.patch')
if args.input:
    selected.append(repo / 'xnu/asus/9999-asus-i007-input.patch')
if args.ufs:
    selected.append(repo / 'xnu/asus/9999-asus-i007-ufs.patch')
    # A root on UFS is not a RAM disk, so bsd_init demands a sealed (authenticated)
    # root and panicked on the phone ("rootvp not authenticated after mounting",
    # image 0011); an HFS root cannot be sealed. As sweet: report and continue.
    selected.append(repo / 'xnu/patches/0029-sweet-unsealed-root-volume.patch')
    # Core dumps off unless sysctl kern.coredump=1: on the phone a failing boot task spent
    # 23 s trying to dump ~1 GB onto the nearly full HFS root (image 0012).
    selected.append(repo / 'xnu/patches/0039-sweet-coredump-default-off.patch')
if args.userland:
    selected.append(repo / 'xnu/asus/9999-asus-i007-userland.patch')
    # SpringBoard calls pid_hibernate (syscall 435, nosys on the macOS config) when an
    # app leaves the foreground: SIGSYS (QEMU: EXC_CRASH code 0xc000000 after a swipe).
    selected.append(repo / 'xnu/patches/0070-sweet-pid-hibernate.patch')
    # Sweet 0043 + 0044 crash reports on the console (CRASH name[pid] sig, registers,
    # frame-pointer backtrace, shared cache slide, exit reason): resprings and app
    # crashes on the phone (image 0033) gave only "EXC_CORPSE_NOTIFY ... for pid N".
    selected.append(repo / 'xnu/asus/9999-asus-i007-zcrash.patch')
    # Sweet 0048: what the processes in boot-arg sweetnull write to /dev/null (their
    # stderr under launchd) reaches the console as "|name| text": the uncaught
    # NSException reason behind SpringBoard's SIGABRT respawns (image 0034).
    selected.append(repo / 'xnu/patches/0048-sweet-devnull-tee.patch')
if args.smp:
    # git apply goes in name order: "zsmp" sorts after every other 9999-asus-i007 patch,
    # whose SweetCPU/SWEET.h edits it was generated against.
    selected.append(repo / 'xnu/asus/9999-asus-i007-zsmp.patch')
if args.amp:
    if not args.smp:
        parser.error('--amp requires --smp')
    selected.append(repo / 'xnu/asus/9999-asus-i007-zzamp.patch')
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
    'cpu': 'cortex-a55', 'cpus': 8 if args.smp else 1, 'amp': args.amp, 'page_size': 16384,
    'root': 'HFS+ or mockfs RAM disk' if args.hfs else 'mockfs RAM disk',
    'hfs': args.hfs, 'display': args.display, 'qemu_compositor': args.compositor,
    'userland': args.userland, 'input': args.input, 'ufs_read_only': args.ufs,
    'storage_driver': args.ufs, 'patches': manifest,
}, indent=2) + '\n', newline='\n')
print(f'Staged {len(selected)} ASUS files; ' + ('UFS (iosroot writes only), ' if args.ufs else 'no UFS, ')
      + 'no modem, GPU or USB drivers'
      + ('' if args.input else ', no input'))
