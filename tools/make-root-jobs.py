#!/usr/bin/env python3
"""Give the iOS root the stock iOS job set: launchd.plist with every stock job but a few.

  make-root-jobs.py IMAGE HASHFILE PATCHDIR --stock STOCK.plist --flags FEATUREFLAGS_DIR
                    [--hfsplus HFSPLUS] [--chown CHOWN] [--exclude LABEL ...] [--dry-run]

IMAGE is a fresh pull of the phone's iosroot (the whole HFS+ volume), HASHFILE its per-block
hashes made before any edit (tools/asus-iosroot-blockdiff.py hash), STOCK the launchd.plist
of the same iOS build (System/Library/xpc/launchd.plist of the OS image), FEATUREFLAGS_DIR
its System/Library/FeatureFlags/Domain. Run in WSL (the libdmg-hfsplus tool, fsck.hfsplus).

The root's launchd.plist has grown job by job (13 jobs in 2026-11, each missing service
found the hard way: powerd for the battery, kbd, then the dictation loop that wanted
assistantd's preference, then tzlinkd for the time zone). This adds every stock job the
root does not have yet, so that a service is there when something asks for it:

- the root's own jobs stay exactly as they are (their earlier edits, e.g. backboardd under
  the key dev.asus.launchd-probe running as mobile); a stock job whose key or Label the
  root already has is skipped;
- stock jobs are taken unchanged except: `_PanicOnCrash` dropped (a crash must not panic
  the kernel: watchdogd did, image 0041u), `EnablePressuredExit`/`EnablePressureExit`
  dropped (as in the root's own jobs), and the feature-flag branches (`#IfFeatureFlagEnabled`
  / `#IfFeatureFlagDisabled` with `#Then` / `#Else`) resolved with this build's FeatureFlags
  (a flag without a domain entry is off), as they were for the root's own jobs;
- EXCLUDE (below, plus --exclude) are left out, each for a reason given there.

The file is replaced as tools/hfs-add-contiguous.py does it (one extent), read back, fsck -n
must report nothing but the usual minor volume header repair, and PATCHDIR gets the input of
tools/asus-iosroot-blockpatch.py (which checks the phone again and writes only with --write).
"""
import argparse
import hashlib
import plistlib
import subprocess
import sys
from pathlib import Path

XPC = '/System/Library/xpc/launchd.plist'

EXCLUDE = {
    'com.apple.watchdogd': 'crashes on purpose without the watchdog service (panic, image 0041u)',
    'com.apple.ClarityBoard': 'a second system shell (Assistive Access) next to SpringBoard',
    'com.apple.checkerboard': 'a second system shell (repair/diagnostics) next to SpringBoard',
    'com.apple.checkerboardd': 'its daemon',
    'com.apple.mobilestoredemod': 'store demo mode (demo units reset themselves)',
    'com.apple.mobilestoredemodhelper': 'its helper',
    'com.apple.factory.NFQRCoded': 'factory only',
    'com.apple.mobile.obliteration': 'erases the device on request',
    'com.apple.RestoreRemoteServices.restoreserviced': 'restore over USB/network, can reboot into recovery',
    'com.apple.nfrestore': 'NFC controller restore',
    'com.apple.SepUpdateTimer': 'SEP firmware updates (there is no SEP)',
    'com.apple.fdrhelper': 'factory data restore',
    'com.apple.iomfb_bics_daemon': 'calibrates Apple panels through IOMobileFramebuffer (ours is SweetFramebuffer)',
    'com.apple.iomfb_fdr_loader': 'loads Apple panel calibration into IOMobileFramebuffer',
    'com.apple.nand_task_scheduler': "Apple NAND (ASP) maintenance; the root sits on the phone's UFS",
    'com.apple.nand.aspcarry': 'Apple NAND (ASP) logs',
    'com.apple.security.cryptexd': 'manages cryptex mounts; the OS cryptex is laid out by hand here',
    'com.apple.preboardservice': 'pre-unlock system UI (data protection, updates) that takes the screen',
    'com.apple.preboardservice_v2': 'the same',
}

DROP = ('_PanicOnCrash', 'EnablePressuredExit', 'EnablePressureExit')
GONE = object()


def flag_on(flags_dir, name, cache={}):
    domain, key = name.split('/', 1)
    if domain not in cache:
        p = flags_dir / (domain + '.plist')
        cache[domain] = plistlib.loads(p.read_bytes()) if p.exists() else {}
    entry = cache[domain].get(key)
    return bool(isinstance(entry, dict) and entry.get('Enabled'))


def resolve(x, flags_dir):
    """Resolve #If... branches; GONE where a branch leaves nothing."""
    if isinstance(x, dict):
        if '#IfFeatureFlagEnabled' in x or '#IfFeatureFlagDisabled' in x:
            if '#IfFeatureFlagEnabled' in x:
                cond = flag_on(flags_dir, x['#IfFeatureFlagEnabled'])
            else:
                cond = not flag_on(flags_dir, x['#IfFeatureFlagDisabled'])
            pick = x.get('#Then', GONE) if cond else x.get('#Else', GONE)
            return GONE if pick is GONE else resolve(pick, flags_dir)
        if any(k.startswith('#') for k in x):
            sys.exit(f'unknown directive in {x}')
        out = {}
        for k, v in x.items():
            r = resolve(v, flags_dir)
            if r is not GONE:
                out[k] = r
        return out
    if isinstance(x, list):
        return [r for r in (resolve(v, flags_dir) for v in x) if r is not GONE]
    return x


def run(hfs, image, *args, check=True):
    r = subprocess.run([str(hfs), str(image), *args], text=True, capture_output=True, errors='replace')
    if check and r.returncode:
        sys.exit(f'hfsplus {" ".join(args)}: {r.stdout}{r.stderr}')
    return r.stdout


def main():
    work = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('hashfile')
    ap.add_argument('patchdir')
    ap.add_argument('--stock', required=True)
    ap.add_argument('--flags', required=True)
    ap.add_argument('--hfsplus', default=str(Path.home() / 'asus-roots/hfsplus'))
    ap.add_argument('--chown', default=str(work / 'asus-launchd-root/hfs-chown-compositor'))
    ap.add_argument('--exclude', action='append', default=[])
    ap.add_argument('--dry-run', action='store_true', help='build and report the plist, do not touch the image')
    a = ap.parse_args()
    tools = Path(__file__).resolve().parent
    flags_dir = Path(a.flags)
    exclude = dict(EXCLUDE)
    exclude.update({l: 'excluded with --exclude' for l in a.exclude})

    staging = Path.home() / 'zc/jobs-full'
    staging.mkdir(parents=True, exist_ok=True)
    cur = staging / 'launchd-current.plist'
    cur.unlink(missing_ok=True)
    run(a.hfsplus, a.image, 'extract', XPC, str(cur))
    have = plistlib.loads(cur.read_bytes())
    jobs = have['LaunchDaemons']
    labels = {v.get('Label') for v in jobs.values()}
    stock = plistlib.loads(Path(a.stock).read_bytes())['LaunchDaemons']
    added, skipped, left_out = [], [], []
    for key in sorted(stock):
        job = stock[key]
        label = job.get('Label')
        if key in jobs or label in labels:
            skipped.append(label)
            continue
        if label in exclude:
            left_out.append((label, exclude[label]))
            continue
        job = resolve({k: v for k, v in job.items() if k not in DROP}, flags_dir)
        jobs[key] = job
        added.append(label)
    blob = plistlib.dumps(have, fmt=plistlib.FMT_BINARY)
    check = plistlib.loads(blob)
    assert {k: v for k, v in check.items() if k != 'LaunchDaemons'} == \
           {k: v for k, v in plistlib.loads(cur.read_bytes()).items() if k != 'LaunchDaemons'}
    new = staging / 'launchd.plist'
    new.write_bytes(blob)
    at_boot = sorted(j['Label'] for k, j in jobs.items() if (j.get('RunAtLoad') or j.get('KeepAlive')) and not j.get('Disabled'))
    print(f'launchd.plist: {cur.stat().st_size} -> {len(blob)} bytes; jobs {len(jobs) - len(added)} -> {len(jobs)} '
          f'({len(added)} added, {len(skipped)} already there, {len(left_out)} left out); '
          f'{len(at_boot)} start at boot (RunAtLoad/KeepAlive, not Disabled)')
    for label, why in left_out:
        print(f'  left out {label}: {why}')
    print('  already there:', ', '.join(sorted(set(skipped))))
    (staging / 'report.txt').write_text('added:\n' + '\n'.join(added) + '\n\nat boot:\n' + '\n'.join(at_boot) + '\n')
    if a.dry_run:
        print('dry run: not written;', staging / 'launchd.plist', staging / 'report.txt')
        return

    run(a.hfsplus, a.image, 'rm', XPC)
    subprocess.run([sys.executable, str(tools / 'hfs-add-contiguous.py'), a.image, a.hfsplus, a.chown,
                    '/System/Library/xpc', str(new), '--mode', '0644', '--uid', '0', '--gid', '0'], check=True)
    back = staging / 'launchd-readback.plist'
    back.unlink(missing_ok=True)
    run(a.hfsplus, a.image, 'extract', XPC, str(back))
    if back.read_bytes() != blob:
        sys.exit('read back differs')
    r = subprocess.run(['/usr/sbin/fsck.hfsplus', '-f', '-n', a.image], capture_output=True, text=True)
    out = r.stdout + r.stderr
    print(out.strip())
    bad = [l for l in out.splitlines() if any(w in l.lower() for w in ('repair', 'invalid', 'error', 'orphan', 'incorrect'))
           and 'Volume header needs minor repair' not in l and 'found corrupt and needs to be repaired' not in l]
    if bad:
        sys.exit('fsck found more than the usual: ' + '; '.join(bad))
    subprocess.run([sys.executable, str(tools / 'asus-iosroot-blockdiff.py'), 'diff', a.image, a.hashfile, a.patchdir],
                   check=True)
    print('patch', a.patchdir, 'new launchd.plist sha256', hashlib.sha256(blob).hexdigest())


if __name__ == '__main__':
    main()
