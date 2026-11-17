#!/usr/bin/env python3
"""Put the deletable system apps (Calendar, Weather, Mail, Notes, ...) into the iOS root's /Applications.

  make-root-systemapps.py IMAGE HASHFILE PATCHDIR --source SRC.img --at START --lsdir DIR
                          [--hfsplus HFSPLUS] [--copyvol COPYVOL]

IMAGE is a sparse pull of the phone's iosroot (tools/asus-iosroot-sparsepull.py) with
--free-range START COUNT (COUNT >= the apps' blocks plus room for catalog growth) and
--free-range at the end of the catalog file, HASHFILE its per-block hashes made before any
edit (tools/asus-iosroot-blockdiff.py hash), SRC an older full copy of the root that still
has /private/var/staged_system_apps. Run in WSL (the libdmg-hfsplus tool, fsck.hfsplus).

Why: on stock iOS these apps ship as /private/var/staged_system_apps and DataMigration's
SystemAppMigrator has installd install them into /private/var/containers/Bundle on the first
boot. Here (image 0123, installd's mobile_installation.log) every install failed: installd
copies with clonefile(), which HFS+ does not have (NSPOSIXErrorDomain 45, ENOTSUP), and the
others failed its code signing check (0xe8008001); the migrator then deleted the staged apps
and marked itself done. SpringBoard so shows only Photos, App Store and Settings, and the
Today/home-screen widgets stay "Waiting..." (their extensions live inside Weather, Calendar,
...). Their binaries are in the OS trust cache, like everything in /Applications, so the
kernel runs them from there without installd.

The edit:
- the volume header's nextAllocation is set to START, so that libdmg-hfsplus' allocate()
  puts every new block into the pulled free range;
- the apps are copied from SRC's /private/var/staged_system_apps into /Applications with
  tools/asus-hfs-copyvol.c --tree, owner root:admin like the apps already there (staged ones
  belong to mobile), modes and flags as they are;
- the matching placeholders (/System/Library/AppPlaceholders/*.app, the same 32 names) and
  the LaunchServices databases (*.csstore in DIR, the lsd container's Library/Caches) are
  moved to /private/var/asus-off (libdmg-hfsplus `rm` is unreliable on this root), an empty
  /System/Library/AppPlaceholders is left, so lsd rebuilds its database from the disk on boot
  and finds one app per bundle id;
- fsck -n must report nothing but the usual minor volume header repair, and PATCHDIR gets
  the input of tools/asus-iosroot-blockpatch.py (which checks the phone and writes only with
  --write).
"""
import argparse
import re
import struct
import subprocess
import sys
from pathlib import Path

STAGED = '/private/var/staged_system_apps'
APPS = '/Applications'
PLACEHOLDERS = '/System/Library/AppPlaceholders'
OFF = '/private/var/asus-off'
BS = 4096


def run(cmd, check=True):
    r = subprocess.run([str(c) for c in cmd], text=True, capture_output=True, errors='replace')
    if check and r.returncode:
        sys.exit(f'{" ".join(map(str, cmd))}: {r.stdout}{r.stderr}')
    return r.stdout


def names(hfs, image, path):
    """Entries of a folder (hfsplus ls)."""
    out = run([hfs, image, 'ls', path])          # 040775   0  80   7 10/ 4/2026 22:52 Apple TV Remote.app
    entry = re.compile(r'^[0-7]{6}\s+\d+\s+\d+\s+\d+\s+\d+/\s*\d+/\d+\s+\d+:\d+\s(.+)$')
    return [m.group(1) for m in map(entry.match, out.splitlines()) if m]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('hashfile')
    ap.add_argument('patchdir')
    ap.add_argument('--source', required=True)
    ap.add_argument('--at', type=int, required=True)
    ap.add_argument('--lsdir', required=True)
    ap.add_argument('--hfsplus', default=str(Path.home() / 'asus-roots/hfsplus'))
    ap.add_argument('--copyvol', default=str(Path.home() / 'asus-roots/hfs-copyvol'))
    a = ap.parse_args()
    tools = Path(__file__).resolve().parent
    hfs = a.hfsplus

    apps = names(hfs, a.source, STAGED)
    holders = names(hfs, a.image, PLACEHOLDERS)
    have = set(names(hfs, a.image, APPS))
    if not apps or sorted(apps) != sorted(holders):
        sys.exit(f'staged apps {apps} and placeholders {holders} differ')
    if have & set(apps):
        sys.exit(f'already in {APPS}: {sorted(have & set(apps))}')
    stores = [n for n in names(hfs, a.image, a.lsdir) if n.endswith('.csstore')]
    print(f'{len(apps)} apps; LaunchServices databases: {stores}')

    with open(a.image, 'r+b') as f:            # nextAllocation (volume header + 0x34)
        f.seek(1024)
        vh = bytearray(f.read(512))
        old = struct.unpack_from('>I', vh, 0x34)[0]
        struct.pack_into('>I', vh, 0x34, a.at)
        f.seek(1024)
        f.write(vh)
    print(f'nextAllocation {old} -> {a.at}')

    out = run([a.copyvol, a.source, a.image, '--tree', STAGED, APPS, '--owner', '0:80'])
    print('copyvol:', out.strip())
    m = re.search(r'copied (\d+) dirs, (\d+) files, (\d+) links; kept (\d+) existing; failed (\d+)', out)
    if not m or int(m.group(4)) or int(m.group(5)):
        sys.exit('copy incomplete')

    run([hfs, a.image, 'mkdir', OFF])
    run([hfs, a.image, 'mv', PLACEHOLDERS, OFF + '/AppPlaceholders'])
    run([hfs, a.image, 'mkdir', PLACEHOLDERS])
    for n in stores:
        run([hfs, a.image, 'mv', f'{a.lsdir}/{n}', f'{OFF}/{n}'])

    now = set(names(hfs, a.image, APPS))
    missing = [n for n in apps if n not in now]
    if missing or names(hfs, a.image, PLACEHOLDERS) or \
            sorted(names(hfs, a.image, OFF + '/AppPlaceholders')) != sorted(holders) or \
            [n for n in names(hfs, a.image, a.lsdir) if n.endswith('.csstore')]:
        sys.exit(f'result not as expected (missing in {APPS}: {missing})')
    print(run([hfs, a.image, 'ls', APPS + '/Weather.app']).strip().splitlines()[:6])

    r = subprocess.run(['/usr/sbin/fsck.hfsplus', '-f', '-n', a.image], capture_output=True, text=True)
    out = r.stdout + r.stderr
    print(out.strip())
    bad = [l for l in out.splitlines() if any(w in l.lower() for w in ('repair', 'invalid', 'error', 'orphan', 'incorrect'))
           and 'Volume header needs minor repair' not in l and 'found corrupt and needs to be repaired' not in l]
    if bad:
        sys.exit('fsck found more than the usual: ' + '; '.join(bad))
    subprocess.run([sys.executable, str(tools / 'asus-iosroot-blockdiff.py'), 'diff', a.image, a.hashfile, a.patchdir],
                   check=True)
    print('patch', a.patchdir)


if __name__ == '__main__':
    main()
