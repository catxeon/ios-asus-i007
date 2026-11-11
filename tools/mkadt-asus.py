#!/usr/bin/env python3
"""Minimal ASUS I007 Apple DT for a single A55 + RAM-only XNU bring-up."""
import argparse
import hashlib
import os
import struct
from pathlib import Path
import adt
from mkadt import node, u32, u64, cstr

PHYS_BASE = 0x140000000
MEM_SIZE = 0x20000000
FB = 0xe5000000
FB_WIDTH, FB_HEIGHT, FB_STRIDE = 1080, 2448, 4320
FB_SIZE = (FB_STRIDE * FB_HEIGHT + 0x3fff) & ~0x3fff
CANVAS_WIDTH, CANVAS_HEIGHT = 1125, 2436   # iPhone X screen presented to iOS (375x812 pt @3x)
SOC_BASE = 0x01000000
UFS, UFS_SIZE = 0x01d84000, 0x3000   # ufshc@1d84000 ufs_mem (live DT)



# /sbin/mount (launchd mount-phase-1 on a disk root) exits 66 without /chosen
# boot-manifest-hash; it bind-mounts /private/preboot/<hex>/usr/standalone/firmware and
# tolerates a missing directory. Fixed stand-in value, as tools/mkadt.py (sweet).
BOOT_MANIFEST_HASH = hashlib.sha384(b'asus i007 boot manifest').digest()
ASUS_ECID = 0x4153555349000001  # 'ASUSI' + 1, synthetic (not the phone's ECID)
GUI_DISP0 = ('dot-pitch', 'display-default-color', 'max-avg-bpp', 'max-peak-bpp', 'max-scaling-ratio')


def gui_nodes(source):
    """Product description nodes for iOS userland, from a local iPhone X (D22AP) ADT.

    backboardd/MobileGestalt read /product, /buttons (home-button-type 2 = no home
    button), /backlight, /chosen display-* and /arm-io/disp0. Placeholder (syscfg)
    properties and anything wiring Apple hardware (function-*, interrupts, reg,
    clocks, phandle parents) are dropped; no driver matches these nodes.
    """
    root, _ = adt.parse(Path(source).read_bytes())

    def find(path):
        n = root
        for part in path.split('/'):
            n = next(c for c in n.children if c.name == part)
        return n

    def clean(n, keep=None):
        props = [(k, f, v) for k, f, v in n.props
                 if not f and (keep is None or k in keep or k == 'name')
                 and not k.startswith(('function-', 'interrupt', 'AAPL,phandle', 'reg', 'clock',
                                       'power-gates', 'iommu-parent'))]
        out = adt.Node()
        out.props = props
        out.children = [clean(c) for c in n.children] if keep is None else []
        return out

    # iBoot fills the D22AP /chosen display-* templates at boot (they are empty in the
    # IPSW), so set them here: 1080x2448 at 3x = 360x816 points, portrait, as on sweet.
    chosen = [('display-scale', 0, u32(3)), ('display-rotation', 0, u32(0)),
              ('display-boot-rotation', 0, u32(0)),
              # As tools/mkadt.py (sweet): libimg4 wants a crypto hash method; XNU derives
              # IOPlatformUUID (CFPreferences CurrentHost) from a synthetic ECID and the A11
              # chip-id; the dev-fused security tuple (0,0,1,1) makes mobileactivationd take
              # its native "Hactivation" branch (Activated without an activation record).
              ('crypto-hash-method', 0, cstr('sha2-384')),
              ('boot-manifest-hash', 0, BOOT_MANIFEST_HASH),
              ('unique-chip-id', 0, struct.pack('<Q', ASUS_ECID)),
              ('chip-id', 0, u32(0x8015)),
              # iBoot's job too: the radios' addresses. MobileGestalt's UniqueDeviceID wants
              # them; without mac-address-wifi0 it waits for a Wi-Fi IONetworkController with
              # a local-mac-address until a timeout, and lsd asks once for every bundle it
              # scans while seeding the LaunchServices database (MIInstallationIdentity):
              # SpringBoard waited 14 min for that on every boot (images 0052-0087).
              # Synthetic, locally administered addresses (not the phone's).
              ('mac-address-wifi0', 0, bytes.fromhex('024153555301')),
              ('mac-address-bluetooth0', 0, bytes.fromhex('024153555302')),
              ('certificate-production-status', 0, u32(0)),
              ('effective-production-status-ap', 0, u32(0)),
              ('certificate-security-mode', 0, u32(1)),
              ('effective-security-mode-sep', 0, u32(1))]
    disp0 = clean(find('arm-io/disp0'), GUI_DISP0)
    disp0.props = [(k, f, v) for k, f, v in disp0.props if k == 'name' or v.strip(b'\0')]
    return chosen, [clean(find('product')), clean(find('buttons')), clean(find('backlight'))], disp0


def trust_cache_prop(path):
    """The OS image's static trust cache (IM4P or raw v2 payload) for the kernel's
    --userland ASUS TRUST policy, like iBoot passes it on iOS."""
    data = Path(path).read_bytes()
    if data[:1] == b'\x30' and b'IM4P' in data[:16]:
        data, info = adt.im4p_payload(data)
        if info['type'] != 'trst' or info['encrypted']:
            raise ValueError('not a plain trust cache IM4P')
    version, count = struct.unpack_from('<I16xI', data)
    if version != 2 or len(data) != 24 + 24 * count:
        raise ValueError(f'unexpected trust cache (version {version}, {count} entries, {len(data)} bytes)')
    hashes = [data[24 + 24 * i:44 + 24 * i] for i in range(count)]
    if hashes != sorted(hashes):
        raise ValueError('trust cache entries are not sorted')
    return ('asus-static-trust-cache', 0, data)


def build(display=False, ios_gui=None, trust_cache=None, ufs=False, mem_size=MEM_SIZE, ncpus=1,
          cores=None, amp=False):
    gui_chosen, gui_top, gui_disp0 = gui_nodes(ios_gui) if ios_gui else ([], [], None)
    chosen = node('chosen', [
        ('dram-base', u64(PHYS_BASE)), ('dram-size', u64(mem_size)),
        ('random-seed', os.urandom(64)), ('debug-enabled', u32(1)),
        ('firmware-version', cstr('asus-i007-shim')),
    ], [node('memory-map')])
    chosen.props += gui_chosen
    if trust_cache:
        chosen.props.append(trust_cache_prop(trust_cache))
    # SM8350: cores 0-3 Cortex-A55, 4-6 Cortex-A78, 7 Cortex-X1; MPIDR Aff1 = core (DynamIQ),
    # all ARMv8.2 with the 16K granule. ncpus > 1 lists them for the SMP patch (cpus=N boot-arg
    # starts the first N via PSCI CPU_ON); ncpus=1 keeps the single boot CPU node as before.
    # Every node matches SweetCPU ('arm,cortex-a55'); the A78/X1 are scheduled as equals.
    # cores: physical cores to list, in logical order (default the first ncpus; core 0, the
    # boot A55, first). amp: cluster-type 'E' for the A55s and 'P' for the A78/X1 (XNU without
    # HAS_CLUSTER makes E cluster 0 and P cluster 1; needs the --amp kernel).
    clocks = [1804800000] * 4 + [2419200000] * 3 + [2841600000]
    cores = list(cores) if cores else list(range(ncpus))
    assert cores[0] == 0 and len(set(cores)) == len(cores) and all(0 <= c < 8 for c in cores)
    cpu_nodes = []
    for index, core in enumerate(cores):
        big = core >= 4
        ctype = (ord('P') if big else ord('E')) if amp else 0
        cpu_nodes.append(node(f'cpu{index}', [
            ('device_type', cstr('cpu')), ('compatible', cstr('arm,cortex-a55') + cstr('ARM,v8')),
            ('reg', u32(core << 8)), ('cpu-id', u32(index)), ('cluster-id', u32(int(big) if amp else 0)),
            ('cluster-type', u32(ctype)), ('die-cluster-id', u32(0)),
            ('cluster-core-id', u32(core - 4 if amp and big else core)), ('l2-cache-id', u32(0)),
            ('l3-cache-id', u32(0)),
            ('state', cstr('running' if index == 0 else 'stopped')),
            ('timebase-frequency', u32(19200000)),
            ('clock-frequency', u32(clocks[core])),
            ('peripheral-frequency', u32(19200000)),
            ('bus-frequency', u32(100000000)), ('fixed-frequency', u32(19200000)),
        ]))
    cpus = node('cpus', [('#address-cells', u32(1)), ('#size-cells', u32(0))], cpu_nodes)
    gic = node('gic', [
        ('compatible', cstr('arm,gic-v3')), ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(27)),
        ('reg', u64(0x17a00000 - SOC_BASE, 0x10000, 0x17a60000 - SOC_BASE, 0x100000)),
    ])
    devices = [gic]
    if display:
        # The platform expert selects this entry by its DT name before
        # IOPlatformDevice matching can examine the compatible property.
        devices.append(node('asus,display', [
            ('compatible', cstr('asus,display')),
            ('device_type', cstr('display-subsystem')),
            # AppleCLCD presents the iPhone X screen (xnu/asus display patch), as sweet 0061.
            ('canvas-width', u32(CANVAS_WIDTH)), ('canvas-height', u32(CANVAS_HEIGHT)),
        ]))
    if gui_disp0 is not None:
        devices.append(gui_disp0)
    arm_io = node('arm-io', [
        ('device_type', cstr('sweet-io')), ('compatible', cstr('sweet-io') + cstr('arm-io')),
        ('#address-cells', u32(2)), ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], devices)
    fbcon = node('sweet-fbcon', [
        ('reg', u64(FB, FB_SIZE)), ('width', u32(FB_WIDTH)),
        ('height', u32(FB_HEIGHT)), ('stride', u32(FB_STRIDE)),
    ])
    # Phone (disk root): keybagd's boot task reads /private/var/keybags/systembag.kb as a
    # plain plist only with /defaults/no-effaceable-storage (as tools/mkadt.py, sweet).
    defaults = node('defaults', [('no-effaceable-storage', u32(1))] if ufs else [])
    return node('device-tree', [
        ('compatible', cstr('asus,picasso') + cstr('qcom,lahaina') + cstr('AppleARM')),
        ('model', cstr('ASUS_I007D')), ('target-type', cstr('PICASSO')),
        ('manufacturer', cstr('ASUS')), ('#address-cells', u32(2)), ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, fbcon, *gui_top,
        # xnu/asus/9999-asus-i007-ufs.patch (sweet_ufs.c): the UFS host ABL leaves running,
        # absolute physical reg. Phone only; QEMU has no UFS.
        *([node('sweet-ufs', [('compatible', cstr('qcom,ufshc')), ('reg', u64(UFS, UFS_SIZE))])]
          if ufs else [])])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--display', action='store_true')
    parser.add_argument('--ios-gui', type=Path, metavar='D22AP_ADT',
                        help='add iPhone X product/buttons/backlight/display-scale nodes from a local decoded ADT')
    parser.add_argument('--trust-cache', type=Path, metavar='TRUSTCACHE',
                        help='pass the OS static trust cache (IM4P or raw v2) in /chosen')
    parser.add_argument('--ufs', action='store_true', help='add /sweet-ufs for the read-only UFS driver')
    parser.add_argument('--mem-mib', type=int, default=MEM_SIZE >> 20,
                        help='kernel RAM from 0x140000000 (phone: up to 5120, free until 0x280000000)')
    parser.add_argument('--cpus', type=int, default=1, help='CPU nodes (1-8; >1 needs the --smp kernel)')
    parser.add_argument('--cpu-list', help='physical cores in logical order, e.g. 0,4,5,6,7 (overrides --cpus)')
    parser.add_argument('--amp', action='store_true', help="cluster-type 'E' (A55) / 'P' (A78, X1); --amp kernel")
    parser.add_argument('--dump', action='store_true')
    args = parser.parse_args()
    if not 512 <= args.mem_mib <= 5120 or args.mem_mib % 16:
        parser.error('--mem-mib must be 512..5120 and a multiple of 16')
    cores = [int(c) for c in args.cpu_list.split(',')] if args.cpu_list else None
    if cores:
        args.cpus = len(cores)
        if cores[0] != 0 or len(set(cores)) != len(cores) or not all(0 <= c < 8 for c in cores):
            parser.error('--cpu-list: distinct cores 0-7, the boot core 0 first')
    if not 1 <= args.cpus <= 8:
        parser.error('--cpus must be 1-8')
    root = build(args.display, args.ios_gui, args.trust_cache, args.ufs, args.mem_mib << 20, args.cpus,
                 cores, args.amp)
    blob = adt.serialize(root)
    args.output.write_bytes(blob)
    if args.dump:
        adt.dump(adt.parse(blob)[0])
    print(f'ASUS ADT: {len(blob)} bytes; {args.cpus} CPU node(s), {args.mem_mib} MiB at 0x{PHYS_BASE:x}; '
          + ('UFS at 0x1d84000' if args.ufs else 'no UFS node'))
