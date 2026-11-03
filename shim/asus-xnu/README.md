# ASUS I007: first XNU RAM-root boot

Diagnostic image 0002 reached XNU and our own PID 1. The user observed the
`ASUS init: still alive` heartbeat approximately every five seconds. The
matching source fix built successfully in CI 41716816697; the user confirmed
the same PID 1 and five-second heartbeat in source-built image 0003 on
2026-11-04. Hardware probe 0007 also
verified the boot A55, 16K MMU, virtual timer INTID 27, and self-SGI.

The separate `asus-xnu` GitHub workflow builds xnu-8796.141.3 with base patches
0001 through 0025 and `xnu/asus/9999-asus-i007-bringup.patch`. No patch 0028
(UFS) or later board drivers is included. Existing topaz builds are unchanged.
The numbered patch series fails the build if any required patch is skipped.

The ASUS patch selects Cortex-A55 code generation, the verified timer IRQ,
ASUS platform/CPU matching, and the 1080x2448 framebuffer. Bootstrap and final
early-console mappings use the 32 MiB-aligned physical block at 0xe4000000;
the actual buffer starts 16 MiB into it at 0xe5000000. Display memory remains
Device-nGnRnE, as in the working hardware probe. There are no CTL_START writes.

`tools/mkadt-asus.py` describes just boot CPU 0, GICv3 and the framebuffer.
Kernel RAM is 512 MiB at 0x140000000, inside a captured RAM bank and outside
every fixed reserved-memory region in the phone's live DT. It has no UFS,
Wi-Fi, touchscreen, audio or other board peripheral nodes.

Build the workflow on a macOS runner with Xcode 15. Download its
`xnu-asus-kernel` artifact, then on Linux/WSL:

```sh
# Needs aarch64-linux-gnu-gcc/binutils, clang and Python 3.
# Keep AOSP system/tools/mkbootimg checked out next to this repo,
# or export MKBOOTIMG=/absolute/path/to/mkbootimg.py.
ASUS_XNU_VERSION=0003 bash tools/build-asus-image.sh /absolute/path/to/kernel.development.sweet
```

The builder rejects kernels without the ASUS build markers and kernels that
still access CNTP_CTL_EL0 in fiq_context_init (which trapped on this phone).
The ASUS source guard retains the virtual timer and omits that physical-timer
access. The builder generates an
ad-hoc signed static Mach-O init from our assembly, embeds that tiny binary
as the mockfs RAM root, packs the kernel/ADT/root and creates boot image v3:
`shim/asus-xnu/out/asus-i007-xnu-0003.img`. No Apple userland is needed here.

Only on the verified, unlocked ASUS I007, the hardware test will use:

```sh
fastboot boot shim/asus-xnu/out/asus-i007-xnu-0003.img
```

Expected evidence is XNU boot output, followed by `ASUS XNU userspace: init
running as PID 1` and repeated `ASUS init: still alive` messages. These messages
were observed on this phone in diagnostic image 0002. Reconfirm them for
each changed kernel; a successful transfer alone does not prove execution.

The successful init intentionally stays alive. There is no ADB/USB driver in
this XNU profile and no automatic timed return once XNU is entered. Return
to Android with the previously verified Power + Volume Down force restart.
No phone partition is flashed by any build/test command in this document.

## HFS+ RAM root

Image 0004 mounted `ASUSRamRoot` and ran our own PID 1 with the five-second
heartbeat, confirmed on the phone. Dispatch `asus-xnu.yml` with `hfs=true`
to include patches 0026/0027 (Apple OSS HFS and MD5), still excluding UFS.
The `asus-ramroot.yml` workflow builds an 8 MiB HFS+ root containing only
our static init and `/dev`, and checks it with Apple's fsck on a read-only
attached device.

```sh
ASUS_XNU_VERSION=0004 bash tools/build-asus-image.sh KERNEL asus-own-init.hfs
```

An explicit root must be a raw HFS+/HFSX volume no larger than 96 MiB and
the kernel must pass the HFS symbol checks. APFS/DMG containers are rejected.
For a separate Apple dyld/libSystem test, `tools/asus-dyld-init.c` builds our
dynamic PID 1 with `tools/build-asus-dyld-init.sh`. Apple's binaries are
private inputs, never part of the public source/archive/CI artifacts. Never
boot the unmodified Apple restore root or its restore jobs on ASUS.

`tools/mkasus-dyld-root.py` packs only our init and its dependency closure
from a locally extracted restore root. It requires the HFS base artifact,
libdmg-hfsplus's `hfsplus` utility and Linux `fsck.hfsplus`. It explicitly
sets and checks mode 0755 on both init and `/usr/lib/dyld`, round-trips every
file to verify its hash, and checks HFS consistency. `hfsplus addall` does
not preserve input modes: omitting the explicit dyld chmod caused image
0005's `LOAD_PROTECT` failure. No kernel trust checks were removed to fix it.
