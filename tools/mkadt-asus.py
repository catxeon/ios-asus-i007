#!/usr/bin/env python3
"""Minimal ASUS I007 Apple DT for a single A55 + RAM-only XNU bring-up."""
import argparse
import os
from pathlib import Path
import adt
from mkadt import node, u32, u64, cstr

PHYS_BASE = 0x140000000
MEM_SIZE = 0x20000000
FB = 0xe5000000
FB_WIDTH, FB_HEIGHT, FB_STRIDE = 1080, 2448, 4320
FB_SIZE = (FB_STRIDE * FB_HEIGHT + 0x3fff) & ~0x3fff
SOC_BASE = 0x01000000


def build(display=False):
    chosen = node('chosen', [
        ('dram-base', u64(PHYS_BASE)), ('dram-size', u64(MEM_SIZE)),
        ('random-seed', os.urandom(64)), ('debug-enabled', u32(1)),
        ('firmware-version', cstr('asus-i007-shim')),
    ], [node('memory-map')])
    cpu = node('cpu0', [
        ('device_type', cstr('cpu')), ('compatible', cstr('arm,cortex-a55') + cstr('ARM,v8')),
        ('reg', u32(0)), ('cpu-id', u32(0)), ('cluster-id', u32(0)),
        ('cluster-type', u32(0)), ('die-cluster-id', u32(0)),
        ('cluster-core-id', u32(0)), ('l2-cache-id', u32(0)), ('l3-cache-id', u32(0)),
        ('state', cstr('running')), ('timebase-frequency', u32(19200000)),
        ('clock-frequency', u32(1804800000)),
        ('peripheral-frequency', u32(19200000)),
        ('bus-frequency', u32(100000000)), ('fixed-frequency', u32(19200000)),
    ])
    cpus = node('cpus', [('#address-cells', u32(1)), ('#size-cells', u32(0))], [cpu])
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
            ('canvas-width', u32(FB_WIDTH)), ('canvas-height', u32(FB_HEIGHT)),
        ]))
    arm_io = node('arm-io', [
        ('device_type', cstr('sweet-io')), ('compatible', cstr('sweet-io') + cstr('arm-io')),
        ('#address-cells', u32(2)), ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], devices)
    fbcon = node('sweet-fbcon', [
        ('reg', u64(FB, FB_SIZE)), ('width', u32(FB_WIDTH)),
        ('height', u32(FB_HEIGHT)), ('stride', u32(FB_STRIDE)),
    ])
    return node('device-tree', [
        ('compatible', cstr('asus,picasso') + cstr('qcom,lahaina') + cstr('AppleARM')),
        ('model', cstr('ASUS_I007D')), ('target-type', cstr('PICASSO')),
        ('manufacturer', cstr('ASUS')), ('#address-cells', u32(2)), ('#size-cells', u32(2)),
    ], [chosen, node('defaults'), cpus, arm_io, fbcon])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--display', action='store_true')
    parser.add_argument('--dump', action='store_true')
    args = parser.parse_args()
    root = build(args.display)
    blob = adt.serialize(root)
    args.output.write_bytes(blob)
    if args.dump:
        adt.dump(adt.parse(blob)[0])
    print(f'ASUS ADT: {len(blob)} bytes; one CPU, 512 MiB at 0x{PHYS_BASE:x}; no UFS node')
