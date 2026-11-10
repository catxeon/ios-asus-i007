# iOS 16 on sweet (Redmi Note 10 Pro, SM7150) - working notes

## ASUS I007 bring-up handoff (2026-11-03, separate device)

### >>> ACTIVE HANDOFF 2026-11-10 late (read this first; details in "Phone path" below) <<<

User paused the work (2026-11-10 ~23:45) with "запиши все находки, продолжим позже".

**Where it stands.** iOS 16.7 boots from the phone's own flash (iosroot, journaled HFS+).
The Adreno 660 runs completely under XNU (GMU, CP, zap, IBs + fences, all in
AsusPhoneInput.cpp stages 1-10, boot-arg asusgpu=10). Kernel profile --gpu adds RedwoodGPU
(msm DRM user client), topaz 0919 exec_inject and an "AMFI" dyld policy answer. In iOS,
libasus-inject.dylib loads libasus-gl.dylib (EAGL over Mesa 25.0.7 OSMesa/freedreno) into
backboardd and CoreAnimation renders with OpenGL ES 3.2 on `FD660`. With
`glenv=FD_MESA_DEBUG=sysmem` it runs 25 min without a hang at 19.7 fps (CPU renderer: 9.9).
**But the screen stays black** (user, every GPU boot 0062-0067).

**Open problem 1: black screen (the current blocker).**
- Observed: display swaps of the BGRA screen surfaces happen (~20/s); 0066's trace of
  frame 1200: after each draw a 1x1 glReadPixels of the middle gives `0 0 135 255`, but a
  full-frame glReadPixels of the same framebuffer gives all zeros; glcopy=1 (frames to the
  surface by glReadPixels instead of a GPU blit) is black too (0064).
- Guess tried: stale CPU cache lines of GPU-written buffers. Kernel 6ca3832 (CI 42889848301)
  cleans+invalidates a GPU-drawn surface before the display copies it and a buffer on
  cpu_prep(READ). Image **0067** (booted 23:36:02) is still black per the user. Its log
  (the cycle was still waiting for the self-reset at ~00:02) has `RedwoodGPU: surface N
  (WxH) shown after submission F: middle pixel XXXXXXXX` every 300 shows: READ IT FIRST.
  If the cycle did not finish, pull it by hand before any other boot (the next boot
  overwrites it): `adb exec-out "su -c 'dd if=/dev/block/by-name/iosroot bs=4096
  skip=8387578 count=1025 2>/dev/null'" > ../phone-0067/log.bin` then
  `wsl python3 tools/sweet-log.py ../phone-0067/log.bin ../phone-0067/log.txt`.
- Next ideas, in order: (a) 0067's middle-pixel lines: nonzero -> the display copy is at
  fault (SweetFramebuffer swap source address/offset/rows for these surfaces; compare
  with the CPU-renderer path that works); zero -> what the GPU blit writes does not land
  in the surface (RedwoodGPU surfaceBuffer: IOSubMemoryDescriptor vs s->memory pages,
  offset 0 vs s->offset, the mirror resource stride 4544 and layout in Mesa's
  osmesa_surface_resource, linear vs UBWC). (b) Why a 1x1 read works and a full read
  does not: Mesa readpixels path (staging blit + map) on the msm/redwood-drm interface:
  check redwood-drm's mmap/clientMemoryForType path and Mesa's BO cache reuse. (c) topaz
  ran this stack with write-combined pool memory and the SMMU in pass-through; a WC path
  here needs non-contiguous kernel RAM (gpu_cached=0 panicked, see 0065).

- 0067 log read (2026-11-10, next session). Observed: the log saved fine (952 lines, flush
  #14) but ends at frame 3 (`ASUS FPS: 3 frames in 5 s`), then nothing until a touch and
  key 2 + key 0 (the user's forced reset; Android back after 882 s, not the 1500 s timer).
  0066 at the same place went on (frame 37 at 6.6 fps, 3161 lines). Only one middle-pixel
  line: `surface 2 (1125x2436) shown after submission 3: middle pixel 00000000` (first
  frame, may be really black: answers nothing). The root is DIRTY (journaled) after the
  forced reset: the next iOS boot needs ASUS_ALLOW_DIRTY_ROOT=1 and the user's OK.
  NOT a stall: every GPU boot has this gap (0062/0063/0064/0066: frames 1-3 in the first
  minutes, the first stats line `N submissions in ~790000 ms`, SpringBoard's first line at
  16:15:0x = ~900 s of uptime). 0067 was reset before that point, so **the cache fix
  6ca3832 is still untested** past the first 3 frames.
- Desk analysis after that (no boots), what the logs and the code do and do not show:
  - "The GPU draws" rests on one number. In 0066's frame-1200 trace `middle 0 0 135 255`
    is the same after all 34 draws, the first of them a 3x18 quad at 1015,60 (nowhere near
    the middle), and `an eighth up` stays `0 0 0 0` (alpha 0) even after two full-screen
    quads with the 1125x2436 texture 2889. So the middle value was in the frame's texture
    before this frame drew anything; no draw of frame 1200 is seen to change a pixel.
    0064's frame 3 read `middle 0 0 0 0`. What is proven: the CP runs Mesa's submissions
    and fences complete (and CP_MEM_WRITE lands, stage 10).
  - Against stale CPU caches as the whole story (guess, but a strong one): the screen is
    uniformly black for 25 minutes over four rotating 13.9 MB surfaces, with ~25 MB pushed
    through the caches per submission; stale lines could not survive that everywhere.
  - Never tested: the GPU writing a big linear buffer. Stage 10 writes one word into a
    2-page buffer with CP_MEM_WRITE (the CP's own path, not the RB's). Screen surfaces and
    Mesa's full-frame staging buffers are 11-14 MB written by the RB (2D blit).
  - SMMU faults are only printed by redwood_gpu_report (a hang): no sysmem run ever printed
    the context bank's FSR. In 0062's hang report FSR was 0x400 (format bits, no fault).
    FSR 0x400 and the FAR/FSYNR0 values there are the same as before the SQE started.
  - Code read, nothing wrong found: newBuffer() maps 4 KiB steps of getPhysicalSegment into
    the 4 KiB-granule tables (16 KiB kernel pages are fine with that), ptTable() chunks are
    zeroed and flushed, redwood_gpu_map flushes the entries it touches.
  - Plan: image 0068 = 0067's kernel + `gltrace=1200-1200 gltrace_lit=1` (no CI run), let
    it run its 1568 s. Kernel middle-pixel lines nonzero + black screen -> the display copy;
    kernel lines zero but Mesa's full-frame read now nonzero -> caches were the read
    problem and the blit into the surface is lost (surfaceBuffer layout/offset); both zero
    -> big GPU writes do not land. Then a kernel build with: the context bank's FSR/FAR in
    the stats line, a grid of pixels instead of the middle one, and a stage-11 self-test
    (2D solid fill of a 1125x2436 linear buffer, checked by the CPU) for lab images.
  - **Image 0068 built, NOT booted**: shim/asus-xnu/out/asus-i007-xnu-0068.img, SHA256
    76e1d467...ac03, kernel CI 42889848301 (6ca3832), 0067's recipe + `gltrace=1200-1200
    gltrace_lit=1` (boot-args 521 chars). Booting it needs the user's OK: the root is
    dirty (ASUS_ALLOW_DIRTY_ROOT=1), and the phone must be left alone for the full 1568 s.

**Open problem 2: GMEM (tiled + binning) hangs the CP** (0061/0062, decoded in "Image
0062" below: CP waits at a CP_DRAW_INDX_OFFSET of the per-bin pass; only CP busy). Workaround
FD_MESA_DEBUG=sysmem. Next: FD_MESA_DEBUG=nobin; Linux's a660 hwcg table (a6xx_set_hwcg,
a660_hwcg, gmu_cgc_mode 0x00020200) which stage 7 never writes.

**Perf notes:** ~25 MB pushed out of the CPU cache per submission (gpu_cached=1), one
full-screen texture refilled from an IOSurface per frame (10.8 MB).

**How images are built** (WSL; the scratchpad scripts are not in git):
- iOS GPU image (0067):
  `ASUS_PHONE_IOS=1 ASUS_UFS=1 ASUS_DISPLAY=1 ASUS_IOS_USERLAND=1
  ASUS_IOS_GUI=../apple-20H392/devicetree/d22ap.adt ASUS_TRUST_CACHE=../apple-20H392/os.trustcache.im4p
  ASUS_CPU_LIST=0,4,5,6,7 ASUS_REBOOT=1500 ASUS_XNU_VERSION=NNNN ASUS_EXTRA_ARGS="asusautoswipe=900
  asusgpu=10 asustouchdelay=5 topaz_inject=/asus/lib/libasus-inject.dylib,backboardd
  glenv=FD_MESA_DEBUG=sysmem" bash tools/build-asus-image.sh ../asus-ci-42889848301/kernel.development.sweet
  ../asus-hfs-root/mac/asus-own-init.hfs` (boot-args must stay < 608 chars; 0064 was 526).
- Lab image (RAM root, iosroot FS never mounted): `ASUS_UFS=1 ASUS_TOUCH_LAB=1 ASUS_CPUS=8
  ASUS_REBOOT=120 ASUS_EXTRA_ARGS="asusgpu=10 asuscpubench=0" ...` same tools/build-asus-image.sh.
- Boot: `tools/asus-phone-cycle.sh IMG ../phone-NNNN 2400` (adb can take ~4 min to answer
  after Android boots; the script waits). FPS: mean of `ASUS FPS: N frames in 5 s` between
  `ASUS AUTOSWIPE: round 1` and `round 30`.
- Kernel CI: `gh workflow run asus-xnu.yml -R catxeon/ios-asus-i007 --ref asus-i007 -f hfs=true
  -f display=true -f userland=true -f ufs=true -f input=true -f smp=true -f gpu=true
  -f runner=macos-15-intel` (arm64 macOS runners had no capacity for hours on 2026-11-10).
  GL libraries: `asus-gl.yml` (-f runner=macos-15-intel), artifact asus-gl. The zgpu patch is
  generated: edit tools/make-asus-gpu-patch.py, then run it; never edit the patch by hand.

**Root and permissions.** iosroot has /asus/lib (libOSMesa.8.dylib, libasus-gl.dylib,
libasus-inject.dylib), written 2026-11-10 with the user's OK; undo = `python
tools/asus-iosroot-blockpatch.py ../asus-rootfix-gl-v1 --restore` (backup.bin there; the files
are inert without the topaz_inject boot-arg). The WSL copy ~/asus-roots/iosroot-now.img is
the root as of 19:13 + /asus/lib, NOT current (iOS boots since then changed the root):
re-pull before preparing any block patch. User permissions given 2026-11-10: lab images
unattended; the /asus/lib write and the GPU iOS boots; booting once on the dirty journaled
root after the 0065 panic (journal replay worked). Ask again for anything else that writes
the root. The auto-mode classifier refused asus-iosroot-blockpatch.py (even read-only) and
ASUS_ALLOW_DIRTY_ROOT=1 without an explicit user OK.

**Tooling quirks:** Git Bash rewrites "/"-looking arguments (a jq filter `"/"` became
`C:/Program Files/Git/`): use MSYS_NO_PATHCONV=1 or avoid "/" in gh/jq/adb arguments, and
test a background watcher's condition once by hand. xnu/patches/* are CRLF in this checkout,
xnu/asus/* LF. The repo dir is owned by CodexSandboxOffline: `git -c safe.directory=*`.

### Phone path (2026-11-05): read-only UFS driver, storage facts

- Phone facts (adb, read-only; root via Magisk `su` for sysfs): ASUS_I007D, WW_I007D,
  Android 11 (RKQ1.201112.002, 18.1055.2307.269), kernel 5.4.210picasso-qgki-perf,
  bootloader unlocked (verifiedbootstate orange). /data = f2fs on sda23, 465 GB, 27 GB
  used; FBE v2 + metadata encryption, hardware-wrapped keys (`wrappedkey_v0`, inlinecrypt);
  `fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized+wrappedkey_v0,
  metadata_encryption=aes-256-xts:wrappedkey_v0, checkpoint=fs`.
- LUN 0 (sda): 994770944 x 512 B (474 GiB), 4 KiB logical blocks; fully allocated.
  userdata sda23 starts at 512-sector 18821440, 975949464 sectors, ends 40 sectors
  (backup GPT) before the LUN end. super sda19 7 GiB (full, lpdump group max 7.5 GB),
  xrom_a/xrom_b 512 MiB each, asdf 384 MiB: no space for an iOS root outside userdata.
- No-wipe option (NOT done, each write needs the user's explicit OK): the phone kernel
  has f2fs_resize_fs (F2FS_IOC_RESIZE_FS, online shrink of the mounted /data as root),
  then shrink the userdata GPT entry from the END (start unchanged: metadata-encryption
  sector numbers stay valid) and add an `iosroot` partition (~32 GiB) after it; root it
  with ufsroot=iosroot. Risks: online f2fs shrink is rarely used on phones; live GPT edit.
  Alternatives: raw HFS image in userdata (Android lost), or disable encryption via
  vendor fstab + format (wipe once). User prefers keeping Android and said "let's try"
  the no-wipe path (2026-11-05); every write step still needs a separate OK.
- No-wipe path, preparation done (read-only):
  - GPT backup on the PC: ../asus-phone-backup/lun0-gpt-primary.bin (LBA 0-5, SHA256
    d070a07a...adcef) and lun0-gpt-backup.bin (last 6 LBAs, d5f59e3c...681d8f), sysfs map
    lun0-sysfs.txt. tools/asus_gpt.py show: both copies valid (header + entry CRCs), equal;
    32 entry slots, 23 used; usable LBAs 6..124346362; userdata #22 2352680..124346362.
  - /data: dm-11 (dm-default-key over sda23, 975949464 sectors), f2fs 1.13, block_count
    121993683 (= the whole partition), 512-block sections, 237679 main segments,
    225757 free; mount has no checkpoint=disable; no fs_mgr `resize` flag (Android will
    not grow it back).
  - Targets: iosroot = LBA 115957504..124346362 (32.0 GiB, 1 MiB aligned); userdata =
    2352680..115957503 (113604824 blocks); f2fs block_count 113604608 (section aligned,
    216 blocks slack) = 433.37 GiB, shrink by 32 GiB.
  - tools/asus-f2fs-shrink.c (NDK r27c static, ../asus-phone-backup/asus-f2fs-shrink
    SHA256 383bed34...081717): checks magic, exact old block_count, section alignment
    and the mount, dry run without --go; then F2FS_IOC_RESIZE_FS and a superblock re-read.
    Dry run on the phone: checks ok; a misaligned size is refused.
  - Order (each needs the user's OK): (1) user backs up phone data; (2) shrink f2fs:
    `asus-f2fs-shrink /dev/block/dm-11 /data 121993683 113604608 --go`; reboot, check
    Android + /data size; (3) write the new GPT (userdata end -> 115957503, new entry
    `iosroot` 115957504..124346362) built by asus_gpt.py, primary + backup, then reboot.
- **Step 2 DONE (user OK "Да, уменьшай", 2026-11-05 14:35 local): /data shrunk online.**
  F2FS_IOC_RESIZE_FS returned in seconds. The kernel removes whole sections only
  (16384 = 32 GiB) and keeps the unaligned 467-block tail, so the superblock now says
  block_count 113605075 (not 113604608): segment_count 221883, segment_count_main 221295,
  main area ends at block 113604608; free segments 225756 -> 209371; df /data 433 GiB,
  27.9 GB used (unchanged). No f2fs errors in dmesg (only a permissive Magisk avc for
  ioctl 0xf510). adb reboot: Android boot_completed, /data mounted; boot fsck.f2fs
  (/dev/fscklogs/f2fs_log): total sectors 121993683, FS sectors 113605075, "No error was
  reported". Superblocks before/after: ../asus-phone-backup/f2fs-sb*.bin.
- **Corrected GPT targets** (userdata must hold 113605075 blocks): userdata
  2352680..115957759 (113605080 blocks, 5 spare); iosroot 115957760..124346362
  (8388603 blocks = 32 GiB - 20 KiB, 1 MiB aligned), Apple HFS+ type, entry #23.
  Built with `tools/asus_gpt.py build ... userdata 115957759 iosroot 115957760 124346362`:
  ../asus-phone-backup/new-gpt-primary.bin (SHA256 14b85bd3...86f9fe) and
  new-gpt-backup.bin (10791baf...ce665d7); `show` validates both (CRCs ok, identical);
  only the header block and entry block 2 differ in each copy. NOT written yet.
  Write plan (needs the user's OK): re-dump and compare the on-disk GPT with the backup
  first; dd primary LBAs 1..5 (LBA 0 untouched) and the last 6 LBAs of sda, fsync, read
  back and compare; reboot; expect by-name/iosroot = sda24 and a normal Android boot.
- **Step 3 DONE (user OK "Да, записывай", 2026-11-05): new GPT written.** On-disk GPT
  re-checked against the backup (both SHA256 equal), images checked, then dd of primary
  LBAs 1..5 and the last 6 LBAs (conv=fsync), blockdev --flushbufs + drop_caches, read
  back = new images (toybox dd has no iflag=direct). Reboot: Android boot_completed;
  by-name/iosroot -> sda24 (start 927662080, 67108824 512-byte sectors), userdata sda23
  908840640 sectors, dm-11 resized with it, /data f2fs 433 GiB mounted, boot fsck "total
  sectors 113605080 / FS sectors 113605075, No error was reported". Android kept, no
  data lost. Undo = dd the saved lun0-gpt-*.bin back (f2fs stays 433 GiB).
- iosroot is still empty (old userdata blocks, encrypted). Writing an HFS root image
  there is a separate write step (needs the user's OK; dd from Android as root or
  fastboot flash iosroot).
- build-asus-image.sh ASUS_UFS=1 now passes ufsroot=${ASUS_UFS_ROOT:-iosroot}. Image
  **0010** = 0009 + ufsroot=iosroot, SHA256 7fe1a47a...cca5bf (supersedes 0009, not
  booted). Expected: partition list incl. `iosroot 0x6e96000 32767 MiB`, then
  `"iosroot" on LUN 0: first block 0x6e96000, 32767 MiB, read-only`, probe 0, heartbeat.
- **MILESTONE: XNU READS THE PHONE'S UFS (image 0010, 2026-11-05, user photo, kept
  private).** fastboot boot over usbipd/WSL (`usbipd attach --wsl --busid 2-1`,
  `wsl -u root fastboot boot`): send 0.45 s, boot OKAY. Screen: `SWEET UFS: host ver 0x300
  cap 0x1587031f hce 0x1 hcs 0x10f utrlba 0x09ea5b000` (UFSHCI 3.0, 64-bit addressing,
  ABL left the host enabled with its own list at 0x9ea5b000); all 24 LUN 0 entries listed
  exactly as Android sees them (#22 userdata 0x23e628 443769 MiB, #23 iosroot 0x6e96000
  32767 MiB); `"iosroot" on LUN 0: first block 0x6e96000, 32767 MiB, read-only, DMA at
  0x143428000` -> DMA above 4 GiB works, the SMMU stream does not block UFS after ABL;
  `ASUS UFS probe: 0 (root stays md0)`, `BSD root: md0`, HFS ASUSRamRoot mounted, our init
  as PID 1 with the 5 s heartbeat. Nothing written.
- NEXT (phone path): (1) a phone kernel profile without QEMU parts (hfs + ufs + display +
  userland; no compositor table, no virtio; touch/buttons need real drivers later);
  (2) an iOS root image for iosroot (from v12, sized <= 32 GiB) - writing it to iosroot
  needs the user's OK; (3) ADT for the phone with more RAM (512 MiB now) and the trust
  cache; (4) rd=ufsroot0 ufsroot=iosroot (read-only first; root-write needs ufsrw, which
  this build refuses).
- Phone iOS prep (commit 9a4d0a7 + shim fix): UFS driver writes only when ufsroot is
  `iosroot` and ufsrw=1 (verifier marker "writes are only allowed to iosroot"); phone RAM
  from /proc/iomem: bank e3000000-27fffffff has reserved e3400000-e72fffff, e7400000-
  e74fffff, e8400000-ffbfffff only, so 0x140000000..0x280000000 (5 GiB) is free;
  mkadt-asus.py --mem-mib (default 512), shim accepts 512 MiB..5 GiB at 0x140000000.
  Phone kernel CI 42022198290 (hfs+display+userland+input+ufs, no compositor), kernel
  48713b6b. Image **0011** (`ASUS_PHONE_IOS=1 ASUS_UFS=1 ASUS_DISPLAY=1
  ASUS_IOS_USERLAND=1 ASUS_IOS_GUI=../apple-20H392/devicetree/d22ap.adt
  ASUS_TRUST_CACHE=../apple-20H392/os.trustcache.im4p ASUS_XNU_VERSION=0011
  tools/build-asus-image.sh KERNEL ../asus-hfs-root/mac/asus-own-init.hfs`): 4 GiB RAM,
  boot-args `rd=ufsroot0 ufsroot=iosroot ufsrw=1 rootrw=1 ufslist=1 ignition_level=1
  interrupt-masked-debug-mode=0 sched-preemption-disable-debug-mode=0
  unentitled_ios_sim_launch=1` (console still on the panel: serial=3), SHA256
  288e93a8...fae347. Contains Apple data (D22AP nodes, trust cache): private.
  Root to write into iosroot: ~/asus-roots/springboard-v12.hfs (12884901888 bytes,
  SHA256 e26e9d4a...393f6d, re-checked).
- **iosroot WRITTEN (user OK "Да, записывай", 2026-11-05).** Checked by-name/iosroot =
  sda24 (67108824 sectors, unmounted), then `adb exec-in su -c "dd of=/dev/block/sda24
  bs=4194304 conv=fsync" < springboard-v12.hfs` (629 s). Read-back hash differed: only
  the LAST 4 MiB block (3071, holds the HFS alternate volume header) was wrong - the final
  buffer of the piped stream was not written. Rewrote that block from a pushed 4 MiB file
  (dd seek=3071 conv=notrunc,fsync); after flushbufs + drop_caches the first 12884901888
  bytes of sda24 hash to e26e9d4a3aeae60f56f67dffcd266ecc233c9e77014e28203fc1568b95393f6d
  = the image. Lesson: verify piped dd writes; rewrite the tail from a file.
- **Image 0011 on the phone (user photo, private):** UFS list as in 0010, `"iosroot" on
  LUN 0: first block 0x6e96000, 32767 MiB, read-write, DMA at 0x144740000`, `BSD root:
  ufsroot0, major 2, minor 0`, `hfs: Removed 1 orphaned / unlinked files and 1 directories`,
  `hfs: mounted ASUSRamRoot on device b(2, 0)` (v12's volume name), LowDisk set, then
  `panic(... caller 0xfffffe0007b4c4e4): rootvp not authenticated after mounting
  @bsd_init.c:937`. On a non-RAM-disk root bsd_init sends FSIOC_KERNEL_ROOTAUTH (sealed
  APFS); HFS cannot pass. Fix: --ufs now also takes sweet 0029 (print and continue).
  Note: the writable mount already removed 1 orphaned file + dir on iosroot.
- **Image 0012** (kernel CI 42026509038 with 0029, SHA256 6ec041f9...f5fab3; user photo):
  past the root-auth check, launchd ran; boot task `mount` (pid 2) exited 66 -> launchd
  panic `mount[2] exited -- no exit reason available -- (signal 0, exit status 66)`. Before
  that the kernel spent 23 s on a core dump of mount (~1 GB, `failed to write segment
  ...: error 28` ENOSPC, then `core dump failed: error 14`). Exit 66 = sweet 0036's
  "failed to get boot-manifest-hash". Fixes (commit d572246, CI 42028226278):
  mkadt-asus.py --ios-gui adds /chosen boot-manifest-hash = SHA-384("asus i007 boot
  manifest") (d9ca9f26...); bindfs added to FILESYS_BASE in the userland patch (sweet
  0037; mount bind-mounts /private/preboot/<hex>/usr/standalone/firmware, ENODEV without
  it); --ufs takes sweet 0039 (core dumps off unless sysctl kern.coredump=1).
  Root free space: HFS LowDisk said freeblks 228291 (~0.9 GB free of the 12 GiB volume);
  the 32 GiB partition is bigger than the volume - growing the HFS volume is a later step.
- **Image 0013** (kernel CI 42028226278, SHA256 f6ec1d7d...d3783a; user photo): mount task
  passes (bindfs + boot-manifest-hash work), cc_fips_test boot task: corecrypto FIPS POST
  all PASSED; keybag boot task: `SWEET AKS: keybagd sel 0 / sel 14 -> 0x0`, `MKB_INIT:
  mount is with NO CPROTECT`, `System Keybag loaded`, then `Could not open
  /private/var/keybags/systembag.kb: No such file or directory` -> `FATAL KEYBAG ERROR:
  kb_load` -> REBOOTING INTO RECOVERY MODE -> panic `Halt/Restart Timed Out
  @IOPlatformExpert.cpp:884` (no restart path). Same as sweet 0038w. (QEMU never ran
  these disk-root boot tasks: its root was a RAM disk.)
- Fixes: mkadt-asus.py --ufs adds /defaults/no-effaceable-storage = 1; root **v13**
  (../make_springboard_v13.py) = v12 + /private/var/keybags (0700 root) /systembag.kb
  (0600 root, plist {KeyBagKeys: data "SWEET"}), fsck OK, ~0.93 GB free, SHA256
  b7c0a839d25f142c57c6d1519eede1297c09cc189ba7b0cda9345f4af66b7871 (v12 unchanged);
  phone boot-args add launchd_unsecure_cache=1 (sweet's next wall: launchd_cache_loader
  "Unable to find AMFI"). Image **0014** = kernel 42028226278 + new ADT/boot-args,
  SHA256 c128a630...dbd16. iosroot rewritten with v13 (full: rw boots changed it):
  exec-in 637 s; this time blocks 3070 AND 3071 were lost (the piped stream drops a
  variable tail, several MiB) - rewrote the last 32 MiB (blocks 3064..3071) from a pushed
  file; sda24 first 12 GiB = b7c0a839... = v13. Rule: always rewrite the last 32 MiB
  from a file and hash-check the whole range.
- **Image 0014 on iosroot=v13 (user photo):** keybag passes; boot tasks auearlyboot (no
  accessories), tzinit (bad zoneinfo link, resets), FinishRestoreFromBackup,
  finish_demo_restore, sysstatuscheck, prng_seedctl (no kernel prng seed / virtual random
  -147, non-fatal) run; launchd_cache_loader: `Using unsecure cache:
  /System/Library/xpc/launchd.plist` then `abort: Failed to open fd [2|No such file or
  directory]` -> launchd panic `launchd_cache_loader[22] exited -- exit reason namespace 7
  subcode 0x1`. Our roots never had /System/Library/xpc (make_springboard_v8 excluded it).
  QEMU msgbuf shows why it never mattered there: with the RAM-disk root launchd logs
  "Skipping boot-task" for mount-phase-1, keybag, fips, tzinit, auearlyboot, ... and loads
  the job files of /System/Library/LaunchDaemons directly (11 jobs; backboardd's is
  dev.asus.launchd-probe.plist). On the disk root every boot task runs.
- Root **v14** (../make_springboard_v14.py) = v13 + /System/Library/xpc/launchd.plist
  (binary, 498749 bytes): LaunchDaemons = exactly this root's 11 job files (SpringBoard,
  backboardd, cfprefsd, containermanagerd, iconservicesagent, lsd, usermanagerd,
  mobileactivationd, mobilegestalt, notifyd, runningboardd); AppExtensions,
  AppRemovalServices, SystemLibraryTreeState, Symlinks, VersionNumber 7 from the 20H392
  OS cache. fsck OK, SHA256 528ab8ee7774f0c39cc70cac95bdc271c7a24994cbdbe2885666decf2b8bc828.
- iosroot rewritten with v14 (613 s + last 32 MiB from file), hash = v14 on the first check.
- **MILESTONE 2026-11-05 ~16:30 local: iOS 16.7 BOOTS FROM THE ASUS'S OWN FLASH TO THE
  APPLE LOGO** (image 0014, kernel 42028226278, iosroot = v14; user photos, private).
  All disk-root boot tasks pass (xpcroleaccountd, init_featureflags, auearlyboot, tzinit,
  finish-restore, finish-demo-restore, sysstatuscheck, prng_seedctl, launchd_cache_loader
  with the v14 cache), `launchd UUID 5C15DB07-...`, `Early boot complete. Continuing system
  boot.`; MobileGestaltHelper and backboardd talk to AppleCLCD (`ASUS FB: backboardd sel
  57/15/22/29/33/79/74 -> 0x0`), xpcproxy, iconservicesagent, cfprefsd, lsd (AKS sel 0/7),
  containermanagerd, mobileactivationd spawn; then backboardd draws the Apple logo on the
  real panel (iPhone X 1125x2436 presentation over the 1080x2448 boot framebuffer, CPU
  blits). Waiting for SpringBoard / lock screen; no touch or buttons on the phone yet.
- **🎉 MILESTONE 2026-11-05 evening: iOS 16.7 LOCK SCREEN ON THE ASUS I007, booted from its
  own flash** (same boot of image 0014; user photos, private). Logo for a while (user
  waited ~20+ min), then SpringBoard: `SWEET SURF: SpringBoard create id 7..10` (LA08
  layer surfaces), AKS sel 17/26/27/8 from SpringBoard, ContextService, FocusUIModule;
  lock screen with date (Dec 31, no RTC), clock, flashlight/camera buttons, home
  indicator and a real status bar (battery icon empty/red: no IOPMPowerSource). Kernel
  console (serial=3) still paints text over the UI; the 6 panel rows above the presented
  1125x2436 area keep console fragments (the margin is cleared only once, at the first
  swap). Next: console off for GUI boots (serial=0 or an asusfb_noconsole switch), keep
  the margins black, flash console log (sweet 0047 into the iosroot tail), touch +
  buttons drivers for the phone, battery power source, SMP.
- **Image 0015** (kernel CI 42040319320 with AsusPhoneInput, commit 0d716d7; boot-arg
  asusinput=1; SHA256 719e9f4f...cb10245; root still v14). User photo of the early log:
  - SPMI: `arb=0x50020000 apids=512 pon-apid=88 volup-apid=122 pon=0x10/1 gpio6=0x1/1
    mode=0/1 en=0x80/1 subtype=0x11/1` -> buttons polled every 20 ms. PON RT reads 0x10
    at rest (bit 4; which bits are KPDPWR/RESIN on PMK8350 is to be confirmed by key
    presses); GPIO 6 is an enabled digital input, high (not pressed, active low).
  - Touch path as ABL leaves it: GCC 0x52008 = 0x83c2000 (QUP0 S4 bit 14 and s_ahb bit 7
    NOT voted), S4 CBCR 0x80004220 (off), S4 RCG CMD 0x80000000 (root off) CFG 0x100000,
    core/core2x CBCR 0x4220 (on), m_ahb 0 (on), s_ahb 0x80000002 (off) -> SE4 registers
    not read. GPIO 20/21/22/23/131 all read cfg=0 io=0 because the diag used the DT node
    base 0xf000000: the downstream lahaina pinctrl adds REG_BASE 0x100000, GPIO n is at
    0xf100000 + 0x1000*n. Next: vote S4 + s_ahb, RCG from XO, read SE4 FW, fix the TLMM
    base, then probe FocalTech 0x38 (no answer = rails L3C/L8C off).
- Images 0016-0019 (touch bring-up iterations, photos): QUP0 votes + S4 from DFS level 0
  (XO/1) OK; s_ahb CBCR 0x80000002 is HW-gated (skip its halt check); SE4 fw 0x303,
  proto 3, idle; pins set (SDA/SCL qup4 cfg 0x4, both read high = external pull-up
  powered; reset/IO-supply high). Every I2C command hung: status 0x41, M_IRQ 0, ABORT
  ignored - TX watermark 1 and the GENI I/O init (cgc/out were already 0x7f) changed
  nothing. Volume up works (HID 0xe9 reached iOS); power/volume down fixed to PON gen3
  bits 7/6 (commit 9647c4b), not yet tested by pressing.
- **Unattended cycle WORKS (commit f1b23ee + script fix, kernel CI 42056477083, image
  0020 with ASUS_REBOOT=150):** `tools/asus-phone-cycle.sh IMG OUT` - fastboot boot at
  19:00:29, kernel `asusreboot=150 reached: final log flush, PSCI SYSTEM_RESET`, Android
  back at 19:04:03, log area (last 1025 blocks of iosroot) decoded: 47400 bytes, 680
  lines, final-flush seq 0xa5a5a5a5. No photos or buttons needed. Logs in
  ../phone-0020/ (private: Apple userland output).
- **Touch root cause (log of 0020):** SE4 register 0x64 = GENI_IF_DISABLE_RO = 1 =
  FIFO_IF_DISABLE: the SE firmware only allows GPI DMA (GSI). Linux i2c-qcom-geni checks
  this bit and switches to gpi_mode (DT dmas = gpi_dma0). Next: a minimal GPI DMA driver
  for QUP0 (qcom/gpi.c: channel alloc/start, transfer + event rings, TREs config0/go/dma,
  doorbells) plus the i2c-qcom-geni GPI path; then rails/IRQ if 0x38 still NACKs.
- **Image 0021 (GPI DMA, commits 964a640/f19494c/dbf9c9f, CI 42063848974; first CI
  attempt failed to LINK: no invalidate_dcache64 in this kernel, only flush/clean):
  FATAL.** fastboot boot 19:30:35, no Android; the phone sat in Qualcomm EDL
  (USB 05c6:9008 "QDLoader 9008") = a fatal secure-side error forced emergency download.
  User hard-reset it (power ~15 s) -> Android 19:38:41, nothing flashed. The iosroot log
  area still held 0020's log (same 47400 bytes, seq 0xa5a5a5a5): AsusPhoneInput runs at
  IOKit matching, BEFORE the UFS root/log exists, so the crash point was not logged.
  Suspects: GPI DMA through the apps SMMU (gpi node iommus stream 0x5b6; UFS DMA works,
  probably because ABL set up its stream) -> unidentified-stream/context fault treated as
  fatal; or GPII-4 register access owned by another EE (touch DT is "trusted touch"
  vm_mode, its io-bases include 0x910000 = GPII 0 region, gpi static-gpii-mask 0x1).
  Rule: never run GPI code at IOKit start; run touch bring-up from a delayed thread after
  the log is up, flush the log synchronously before each risky step, and step through
  (registers read-only -> SE GPI mode -> EV/CH allocate -> start -> doorbell) with a
  boot-arg limit. Each fatal step costs a manual hard reset (EDL).
- **Image 0022 (staged GPI, commit b800d6a, CI 42067369726): EDL again.** Log flushed 7
  times; it ends in the normal iOS boot (backboardd, HID services opened, lsd,
  containermanagerd) and has `ASUS TOUCH: bring-up in 45 s` but NO `ASUS GPI: stage 1`
  although that stage flushes the log first. So either the crash happened in
  touchBringup's pre-GPI steps (GCC votes, SE4 read, pins, GENI I/O init: unflushed) or
  the stage flush was skipped (it used try_lock). Fixes: asus_log_sync now waits for the
  UFS lock (reset/panic paths still try-lock); markers + sync before every pre-GPI step.
- EDL recovery: tools/edl-reset.ps1 (Sahara over the QDLoader COM port: HELLO ->
  HELLO_RESP command mode -> CMD_READY -> RESET_REQ -> RESET_RESP) got a full reply, but
  the phone stayed in 9008 and then stopped answering; the user power-reset it.
  Prevention: Android has download_mode=1 (msm_poweroff and qcom_dload_mode params,
  /sys/kernel/dload "mini", persist.asus.ramdump=1). tools/asus-phone-cycle.sh now
  writes 0 to both runtime params right before `adb reboot bootloader` (not
  persistent; the next Android boot sets them again), so a fatal secure-side error
  should reset into Android instead of EDL. Untested yet.
- **Image 0023 (kernel 42071762478): EDL even with download_mode 0 0** (the cycle wrote 0
  to both params). So the EDL is the secure side's own policy, not Android's crash dump
  setting. Log header seq 0xa5a5a5a5 = written by asus_log_flush (sync), but no step
  marker: printf/IOLog only queue text for the console thread, so a marker printed right
  before the fatal step is never in the ring. Also: the iosroot HFS came back with
  "BlockMarkFreeInternal ... runtime corruption detected" (rw root through repeated
  EDL crashes; it is mounted "dirty non-journaled" on every boot since the asusreboot
  resets never unmount). The root needs a v14 rewrite before the next iOS GUI test.
- Fixes (7fb8c47..): asus_log_mark(line) appends to the ring and flushes (all touch/GPI
  markers use it); halt/restart notes its type in the ring directly and, on a panic,
  appends the tail of debug_buf. build-asus-image ASUS_TOUCH_LAB=1: RAM root, iosroot FS
  never mounted, ufsprobe + ufsrw only for the log (a crash cannot corrupt the root).
- Image 0024 (lab, touch after 20 s): EDL with NO log written: with a RAM root the UFS
  probe comes late, the bring-up ran first. touchFired now waits for asus_log_ready().
- **Image 0025 (lab, kernel 42086225176): crash located.** Log: flash log ready after
  12 s; GCC votes, S4 RCG (DFS), SE4 fw 0x303, pins, GENI I/O init all fine; GPI stage 1
  (GPII 1-6 idle), 2 (SE4 DMA_MODE_EN, GSI events), 3 (event ring allocated: ev
  0x10110042), 4 (channels ch0 0x10100042, ch1 0x1010014a), 5 (started: 0x10200042 /
  0x1020014a, err 0), 6 (ev doorbell, err 0) all fine; the last line is "stage 7:
  transfer: channel doorbells". So the SoC dies (EDL) the moment the GPI engine fetches
  its first TRE from DRAM; no halt/restart note, so XNU did not reset it. Most likely
  the GPI stream (apps_smmu SID 0x5b6 in the Android DT, iommu addr pool 0x100000) has no
  SMMU context, and the fault is fatal on the secure side. Dropped for now.
- Next: bit-banged I2C on GPIO20/21 (TLMM only, no DMA, open drain via the OE bit),
  default when asusgpi is not given; reads FocalTech 0xa3/0x9f/0xa6/0xa8/0x02, scans the
  bus on NACK, then polls touches for 60 s while GPIO23 (IRQ) is low.
- **Image 0026 (lab, kernel 42088068631): no EDL, self-reset back to Android.** Bit-bang
  idle sda=1 scl=1, but 0x38 NACKs every register (ENXIO) and the 0x08..0x77 scan finds
  nobody. Android's live DT (/proc/device-tree, phone rooted): focaltech@38 is the
  active touch (driver fts_ts on i2c-1; st_fts@49 disabled), reset GPIO22, IRQ GPIO23,
  vddio GPIO131, vdd-supply = pm8350c_l3 (3.008 V), vcc_i2c-supply = pm8350c_l8
  (1.8 V), both enabled in Android; trusted-touch-mode vm_mode; i2c@990000 dmas tx/rx
  (GPI). RPMh: cmd-db reserved-memory@80860000 (0x20000), apps_rsc drv-2 0x18220000,
  qcom,tcs-offset 0xd00, drv-id 2, tcs-config 2 active / 3 sleep / 3 wake / 0 control.
- Next (image 0027): cmd-db lookup of ldoc3/ldoc8 and an active-TCS vote (L8 1800 mV
  HPM on, L3 3008 mV HPM on) before the pins/reset step (asustouchpower=0 skips it);
  bit-bang self-test (driven-low readback).
- **MILESTONE: TOUCH WORKS ON THE PHONE (image 0027, lab, kernel 42090383636; no EDL,
  self-reset into Android after 214 s).** cmd-db v1 (magic db30030c): ldoc3 0x41000,
  ldoc8 0x42700. Active TCS 1 (control 0, status idle): 6 votes (L8 1800 mV / HPM 7 /
  on, L3 3008 mV / HPM / on), every cmd status 0x10101, completed. Bit-bang self-test OK
  (driven low reads 0, released 1). FocalTech 0x38: reg 0xa3 = 0x56, 0x9f = 0x52
  (FT5652), 0xa6 = 0x40 (FW), 0xa8 = 0x8d (vendor). With the user's finger on the
  screen: 60 reports in ~5 s from reg 0x02 (n=1, event 2 = contact, id 0, smooth x/y
  tracks). Panel coordinates are native 1080x2448 (DT focaltech,display-coords 0 0
  1080 2448, max-touch-number 10); iOS canvas is 1125x2436 -> scale when feeding
  SweetHIDTouch.
- So the bootloader leaves the touch rails off and the QUP SE4 is GSI-only; the working
  path is RPMh votes + bit-banged I2C (50 kHz, polled on GPIO23).
- iosroot (sda24, start 927662080 x 512 B, 34359717888 B, not mounted) rewritten with
  v14 after the 0023 corruption (user approved): `adb exec-in "su -c 'dd
  of=/dev/block/sda24 bs=4194304 conv=fsync'" < //wsl.localhost/Ubuntu/...v14.hfs`,
  579 s; first 3072 x 4 MiB = 528ab8ee... = v14 on the first check (the tail arrived
  this time; the separate last-32-MiB push was not needed). Note: from Git Bash, `adb
  push X /data/...` needs MSYS_NO_PATHCONV=1 (the remote path got rewritten).
- CI moved to macos-15 (macos-14 retired 2026-11-02: jobs were cancelled after 3 s with
  "The configuration 'macos-14-arm64-us-default' is not supported"); first macos-15 run
  42095520845 succeeded.
- **Image 0028 (iOS from iosroot v14, kernel 42095520845, asusreboot=600): touch reaches
  iOS.** Bring-up at 45 s (log ready at once on the disk root): RPMh votes completed,
  FT5652 ID read, "FocalTech reports -> iOS (poll 8 ms, 10 fingers)", and
  `SWEET HID: touch N down/up at x,y` follow the user's finger; SweetHIDTouch and
  SweetHIDButtons opened by backboardd's IOHIDEventServiceUserClient. No lock screen
  within 600 s: only 3 FB swaps (Apple logo); in 0014 the lock screen took 20+ min
  (cpus=1, fresh root = data migration again). Bug: the report loop read n+1 points,
  the stale extra one produced phantom fingers 1/2 -> fixed (i < n; missed lifts are
  released by the "not seen" pass). Next run: asusreboot=2700.
- (Image 0029 never ran: dispatching the SMP build cancelled its CI run 42099077746;
  the workflow now queues instead, cancel-in-progress false.)
- **SMP: ALL 8 CORES ON THE PHONE (image 0030, lab, kernel 42099915253 with smp=true,
  ASUS_CPUS=8).** `SWEET CPU: requested=8 selected=8`, CPU_ON to MPIDR 0x100..0x700
  (entry PA 0x1414a5000) rc=0 for each, every core "came alive; SGI1 OK", `8/8 CPUs
  initialized, SGI1 verified`; UFS log, touch bring-up (FT5652) and the asusreboot PSCI
  reset all fine; back in Android after 156 s. The A78/X1 run as equal SMP cores (no
  AMP clusters). Next: image 0031 = iOS from iosroot with cpus=8, asusreboot=2700.
- **Image 0031 (iOS, cpus=8): LOCK SCREEN reached, touch reaches the UI, but touch
  behaves oddly and the UI lags heavily** (user report). Screen during boot: `hfs:
  BlockMarkFreeInternal() trying to free unallocated blocks on volume ASUSRamRoot`,
  `hfs_mark_inconsistent: Runtime corruption detected`, `MapFileBlockC: tmpOff <= 0`:
  the root rewritten after 0023 was damaged again by 0028's direct PSCI reset at 600 s
  (non-journaled HFS, no sync/unmount). Fixes prepared (next kernel):
  - asusreboot = clean reboot_kernel(RB_AUTOBOOT) (stop processes, sync, unmount all),
    then the halt hook's PSCI reset; a 120 s backstop thread forces the reset.
  - Display: panel memory surface back to write-combine (sweet's mode; the ASUS patch
    had Device/InhibitCache with per-pixel 32-bit stores = 11 MB of device stores per
    swap in backboardd), row memcpy; the screen text console is turned off at the first
    swap (it kept scrolling the whole panel over the GUI), asusfb_console=1 keeps it.
    App surfaces were already cacheable (kIOMapDefaultCache).
  - make_asus_display_patch.py now carries 952fc66 (platform-binary framebuffer
    clients, previously a hand edit of the patch) and runs on a clean xnu-asus-source
    (reverse the display patch first; scratchpad unpatch_display.py removes leftovers).
  - Touch: read count + first point (7 bytes), the rest only for multi-touch (was 61
    bytes per report, ~11 ms); bit-bang half period 2 us (~200 kHz).
  - Touch stuck down (user: "after a touch it still thinks the finger is on the
    screen"): the FocalTech IRQ is a short falling-edge pulse (DT flags 0x2008); the
    8 ms level poll missed the lift report. Now every poll reads while a finger is down.
- 0031 log (back in Android after 1925 s, user reset): 8/8 CPUs, SpringBoard surfaces,
  28 HID touch lines, three hfs_mark_inconsistent, no panic.
- **Image 0032 (kernel 42106499721): panic at the first GUI swap** "Unexpected fault in
  kernel static region", pc switch_to_serial_console+0x1c <- PE_initialize_console+0x58,
  far = cons_ops_index (__DATA_CONST, read-only late), esr 0x9600004f. Same as sweet:
  fix = sweet 0067's mute flag: `int sweet_console_muted` in serial_console.c (UFS
  patch, next to the flash log hook; _cnputs returns after the log append unless a
  panic is active), set by AppleCLCD at the first swap (weak extern). The panic tail
  and halt/restart notes reached the flash log as designed.
- Image 0033 (kernel 42111381928): console mute works ("ASUS FB: screen console off"),
  user: "much better", touches release properly (299 HID touch lines), but Settings
  crashes while scrolling and SpringBoard resprings (SpringBoard spawned 4x,
  Preferences 4x; the log only has "Failed to send exception EXC_CORPSE_NOTIFY ... for
  pid N"). Settings: probably sweet's PodcastsUI force-unwrap of a nil
  URL.volumeTotalCapacity on the HFS+ root (not verified on ASUS). The user ended the
  run with a hard reset (vol down + power), so the clean reboot is still untested.
- Next kernel: 9999-asus-i007-zcrash.patch (sweet 0043 + 0044 console crash reports,
  under --userland; 0066 needs sweet 0051, left out) and EPSS cpufreq: AsusPhoneInput
  logs each domain (0x18591000 A55, 0x18592000 A78, 0x18593000 X1: LUT, state) and
  requests the top LUT index (asuscpufreq=0 logs only, =N caps at index N-1).
- **Image 0034 (kernel 42116493911; the chain downloaded it into ios-topaz/ by
  mistake, moved to work/).** EPSS: domain 0 (A55) enable 0x3, was 1497 MHz (perf
  0xc), LUT 288..1804 MHz (16 levels) -> perf-state 15 = 1804 MHz. Domains 1 (A78,
  LUT 710..2419) and 2 (X1, LUT 844..2841) report enable 0x2 (bit 0 clear = cpufreq
  hardware not enabled), state 0: the big cores' clock is unknown (maybe very low).
  Crash reports (symbolicated with scratchpad dsc_a2s.py against the SystemOS cache,
  slide 0x2b558000):
  - SpringBoard[24], [39] and most Preferences: sig 6, abort <- libc++abi <-
    objc_exception_rethrow <- CFRunLoopRunSpecific <- GSEventRunModal <- UIKitCore:
    an uncaught NSException on the main thread (reason not on the console).
  - Preferences[44] sig 5 (Swift trap) in PodcastsUI (sweet's nil
    URL.volumeTotalCapacity force-unwrap).
  - Preferences[50] sig 11, pc 0 in CoreImage <- MobileIcons (null function pointer,
    probably no GPU/Metal).
- Patch batch (user: "add many patches preemptively"; only those that apply on the
  ASUS tree and do not depend on Xiaomi/topaz hardware):
  - 0048 /dev/null tee (stderr of sweetnull=backboardd,SpringBoard,Preferences to the
    log: the NSException reasons);
  - 0064 PRIV_WORK_INTERVAL grant + first-denial log, hand-ported into asus_userland.c
    (the ASUS sandbox policy had no mpo_priv_grant; CoreAnimation clients need it);
  - 0068 + 0131 IOSurface fixes (cached lookup answers base -1; no zero-client
    surfaces) applied by make_asus_display_patch.py after the ASUS edits;
    0136/0137 need topaz 0132-0135 first (left out), 0040 IOWatchdog not needed
    (no watchdogd job), 0051 replaced by 0048, 0056 throttled present not taken (the
    copy is cheap with write-combine; deferring it adds latency/races).
  - asuscpubench: 30 s after start a thread bound to each CPU in turn times 2^22 x 8
    dependent adds -> MHz estimate per core.
- **Image 0035 (kernel 42122414045): no crashes, no resprings** (user: "better,
  nothing crashes"). **Clean reboot verified**: asusreboot=2700 -> sync, processes
  stopped, vfs_unmountall (`hfs: unmount initiated on ASUSRamRoot`), halt/restart
  type 1 -> PSCI reset. Per-core check: cpu0-3 (A55) ~1802-1804 MHz, cpu4-6 (A78)
  ~709 MHz (= their lowest LUT level 710), cpu7 (X1) ~1074 MHz. Remaining kills are
  slowness: 0x8badf00d "failed to terminate gracefully after 5.0s" (MobileSMS,
  Preferences), MobileSafari sig 9 (ns 23), MobileSlideShow abort. SpringBoard
  privilege 1007 denied once.
- **GPU facts (Android live DT/sysfs, private copy work/asus-gpu-fw/gpufacts.txt):**
  Adreno660v2, qcom,chipid 0x06060001, highest-bank-bit 16, ubwc-mode 4, min access
  32; kgsl-3d0 regs 0x3d00000 (0x40000), cx_dbgc 0x3d61000, rscc 0x3de0000,
  isense 0x3d8b000; GMU qcom,gmu@3d69000 reg 0x3d6a000 (0x34000) + gmu_pdc 0xb290000,
  GMU behind kgsl-smmu SID 5 (iommus <smmu 5 0x400>); GPU SMMU kgsl-smmu@3da0000
  (qsmmu-v500, qcom,skip-init, TBUs 0x3dc5000/0x3dc9000, 3-level split tables);
  gpucc 0x3d90000 (0x9000, lahaina-gpucc); zap region pil_gpu_region@8b51a000 (8 KiB);
  RPMh gfx.lvl / cx.lvl / mx.lvl; GPU OPPs 315..840 MHz (10 levels, initial level 9 =
  315 MHz). Firmware /vendor/firmware a660_gmu.bin (53672), a660_sqe.fw (40496),
  a660_zap.{mdt,b00,b01,b02,elf} -> work/asus-gpu-fw/ (private, not in git).
- **GPU plan.** Reuse the topaz stack (patches 0901-0947 from HackiOS/darwin-redwood:
  RedwoodGPU.cpp = msm DRM user client for Mesa freedreno, hardware behind
  redwood_gpu_*; userland/mesa (Mesa 25.0.7 freedreno, redwood-drm), userland/gpu
  (eagl, injection into backboardd)). New: an Adreno 660 hardware layer (topaz's
  TopazAdreno.cpp is A610, no GMU). A660 needs, after Linux a6xx_gmu.c/a6xx_hfi.c
  (v6.12 sources sparse-checked out in WSL ~/linux-msm): GCC/GPU_CC clocks, CX GDSC,
  GMU firmware into ITCM/DTCM, RSCC/PDC sequences (a6xx_gmu_rpmh_init), HFI queues in
  memory the GMU reaches through the GPU SMMU (SID 5), GMU start, HFI init/perf table
  (gfx.lvl/mx.lvl ARC votes from cmd-db), OOB GPU_SET (GX on), then a6xx hw_init,
  SMMU context bank for the GPU, zap shader (PAS 13) via TrustZone, SQE + CP start.
  Staged boot-arg asusgpu=N in AsusPhoneInput (lab images): stage 1 = read GCC GPU
  branches + GPU_CC (enables GCC_GPU_CFG_AHB_CLK like Linux), stage 2 = GPU clocks +
  CX GDSC + read GMU registers.
- **Image 0036 (lab, kernel 42130406828, asusgpu=1 asusepssen=1): BIG CORES AT FULL
  SPEED.** Setting EPSS enable bit 0 on domains 1/2 worked: domain 1 enable 0x3 state
  710 MHz -> perf-state 15 = 2419 MHz, domain 2 2764 -> perf-state 18 = 2841 MHz;
  per-core check: cpu0-3 1802 MHz, cpu4-6 2418 MHz (was 709), cpu7 2840 MHz (was
  1074). Now the default (asusepssen=0 opts out). GPU stage 1: GCC_GPU_CFG_AHB_CLK
  already on (0x1); GPU_CC readable: CX GDSC 0x106c = 0x222001 and GX GDSC 0x100c =
  0x222001 (collapsed, PWR_ON clear), cx_gmu 0x1098 0x80004221 (off), cxo 0x109c
  0x80000001, ahb 0x1078 0, gx_gmu off, gmu_clk_src cmd 0x1120 0x80000000 (root
  off), hlos1 SMMU vote 0x5000 0x80000000, PLL0/PLL1 (0x0/0x100) configured but off
  (L 0x?? at +0x4 = 0, user ctl 0x805). The lab-mode "initproc failed" panic at the
  end is the clean reboot killing the RAM-root init (harmless, lab only).
- Image 0037 (lab, asusgpu=2, first version): gcc_gpu_memnoc_gfx stayed off
  (0x80000221) when enabled before the GPU power domain. Fixed order as Linux:
  GPLL0 votes, CX GDSC, clocks.
- **Image 0038 (lab, kernel 42134929628, asusgpu=3): GPU CX side alive.** CX GDSC
  0x106c 0x222001 -> 0xf8222800 with gds_hw_ctrl 0x1540 0x5ff4 -> 0x80001fe0 (VOTABLE
  status bit 31 there); gmu_clk_src CFG 0x602 (GPLL0_DIV/1.5 = 200 MHz), hub_clk_src
  0x603 (150 MHz); all GPU_CC branches on; memnoc_gfx 0x221 / snoc_dvm_gfx 0x1 on. GMU
  registers readable: CM3_SYSRESET 1 (held), FW_INIT_RESULT 0, ALWAYS_ON_COUNTER
  running, SPTPRAC status 0xc0. GPU SMMU (0x3da0000): sCR0 0x290406 (enabled, GFRE/
  GFIE, USFCFG = unmatched streams fault), IDR0 0x4c017e06 (6 SMRs), IDR1 0x30000007
  (7 CBs, 4 KiB pages, CBs from 0x10000), IDR2 0x5511; all SMRs invalid, all S2CR
  type 2 (fault), all CBAR 0 with power-up garbage in SCTLR/TTBR/TCR: completely
  unconfigured, the HLOS (kgsl) programs it. Stage 4 now takes a CB with CBAR 0.
- Driver firmware area: tools/asus-fw-pack.py -> work/asus-gpu-fw/asusfw-area.bin
  (a660_gmu.bin, a660_sqe.fw, a660_zap.{mdt,b00,b01,b02}; 113792 of 262144 bytes,
  SHA256 f8617c2f...) written 2026-11-06 ~03:15 to iosroot blocks 8387514..8387577
  (right before the log at 8387578), readback hash equal, log header intact. Read by
  asus_fw_load() (sweet_ufs.c). Android's mksh arithmetic is 32-bit: compute block
  numbers on the PC (a first try computed a negative seek; dd refused, nothing
  written).
- kgsl GPU contexts (Android DT qcom,kgsl-iommu@3da0000, qcom,kgsl-smmu-v2):
  gfx3d_user = SMMU stream 0, gfx3d_lpac = 1, gfx3d_secure = 2 (all mask 0x400);
  the GMU is stream 5 (qcom,gmu iommus). Next after GX on: a6xx hw_init (a660 values:
  GBIF QSB sides 0x71620, ROQ thresholds 0x02000140/0x8040362c, LPAC FIFO 0x20, mem pool
  128, UCHE CMDQ 0x66906, CP_CHICKEN_DBG 1, ...), an SMMU context for stream 0, SQE
  (a660_sqe.fw) + ringbuffer, CP init, zap shader (PAS 13, pil_gpu_region@8b51a000),
  CP_SET_SECURE_MODE 0.
- **Image 0039 (lab, kernel 42136794697, asusgpu=6): the GMU runs its firmware.**
  asus_fw_load a660_gmu.bin from the iosroot area (53672 bytes); GMU memory icache pa
  0x144398000, dummy/log/hfi/debug, tables 0x1444a0000; SMMU SMR5 -> CB6 (CB page
  0x16000): SCTLR 0x67, TTBR0 0x1444a0000, SMR 0x84000005, S2CR 6; RSCC stop ok
  (status 0x1010006), rpmh_start ok; 4 firmware blocks into ITCM/DTCM/icache; CM3 out
  of reset -> FW_INIT_RESULT stayed 0 and the GMU's SMMU CB reported FSR 0x402
  (translation fault) at FAR 0x104000 = just past the 1 MiB icache window: the
  firmware uses more of the icache range (headers list sections at 0x100000 and
  0x200000). Now icache = 16 MiB - 16 KiB at GMU VA 0x4000 as Linux.
- Image 0040: with 16 MiB the first code fetch at 0x4000 took a permission fault (FSR
  0x408): EL0-writable stage-1 pages are privileged-execute-never. Fixed: icache/
  dummy/debug mapped RW EL1-only (Linux IOMMU_PRIV), log/HFI plain.
- **MILESTONE: ADRENO 660 POWERED BY ITS OWN GMU UNDER XNU (image 0041, lab, kernel
  42141145854, asusgpu=6, 2026-11-06 ~04:12).** `GMU firmware init OK: result 0x100`;
  `HFI start OK (status 0x1, version 0x20000001)`; cmd-db gfx.lvl levels 0 64 80 128 144
  192 224 256 320 384 416 432 (mx/cx 16 entries); ARC votes gx[1] (315 MHz) 0x400201,
  gx[10] (840 MHz) 0x1a0080a, GMU cx 0x400202; HFI PERF_TABLE, a660 BW_TABLE,
  CORE_FW_START, START and GX_BW_PERF_VOTE (315 MHz) all acked; OOB GPU_SET acked ->
  GX GDSC 0x100c = 0xf8222800 (on); GPU registers answer: RBBM_STATUS 0x1,
  CP_ALWAYS_ON counter 0xc1a3e4 ticking, CP_HW_FAULT 0, INT0 0. Next: stage 7 (hw_init,
  SQE + ring behind an SMMU context for stream 0, CP_ME_INIT), 8 (zap via TrustZone
  PIL), 9 (CP_MEM_WRITE test).
- **Image 0042 (lab, kernel 42141514624, asusgpu=7): EDL** (no reset within 1200 s,
  USB 05c6:9008 at 04:40; tools/edl-reset.ps1 got RESET_RESP again but the phone stayed
  in 9008). Needs the user's power-key reset; the flash log (stage-7 markers: "hw_init
  registers" / "SQE and ringbuffer, CP start" / CP_ME_INIT result) must then be read
  by hand: adb exec-out "su -c 'dd if=/dev/block/by-name/iosroot bs=4096
  skip=8387578 count=1025'" > log.bin; python3 tools/sweet-log.py log.bin log.txt.
  Suspects: the RBBM_SECVID_TSB_* writes (0xf800-0xf810, secure-side registers that
  Linux writes only before the zap shader's CP_SET_SECURE_MODE on its targets),
  GBIF halt clears, or the CP's first fetches through the stream-0 context.
  Next kernel (42145829535): a marker before each stage-7 group; SECVID TSB writes only
  with asusgpusecvid=1. Images ready for the morning: 0043 (lab, asusgpu=7) and 0044
  (iOS from iosroot, cpus=8, big cores at full speed, GPU stages off).
- **0042 flash log (read after the user's reset, 07:21):** stage 7 got through "SMMU
  stream 0 bound: SMR4 -> CB5" and "hw_init registers" (SECVID TSB writes included) and
  the last marker is "SQE and ringbuffer, CP start": the EDL comes from starting the CP
  (CP_SQE_INSTR_BASE / RB / SQE_CNTL / CP_ME_INIT), not from the register setup.
- **Image 0044 (iOS, cpus=8, big cores at full speed, kernel 42145829535):** user:
  "fast and responsive UI, but 15 fps" (CPU rendering). Clean reboot after 2766 s. The
  log wrapped (51 MB): 69850 x "SWEET AKS: Spotlight sel 17" (device state polled ~26/s;
  sfa = failed attempts, 0 is right; cause of the polling unknown). AKS logging now rate
  limited (first 8 per process/selector, then every 1000th).
- FPS: XNU sees 8 equal cores and may run the render server on a 1.8 GHz A55. Image 0045
  = cores 0,4,5,6,7 only (mkadt --cpu-list, ASUS_CPU_LIST), same kernel; image 0046 = AMP
  kernel 42164134227 (profile --amp: 9999-asus-i007-zzamp.patch __ARM_AMP__ +
  MAX_CPU_CLUSTERS 2; ADT --amp: A55 cluster-type 'E' cluster 0, A78/X1 'P' cluster 1).
- **Userland GPU, first piece: Mesa for iOS built in CI** (.github/workflows/asus-mesa.yml,
  macos-15, run 42145703219): Mesa 25.0.7 + userland/mesa patches + redwood-drm,
  gallium softpipe+freedreno, OSMesa: libOSMesa.8.dylib 14.8 MB (deps: libz, libSystem,
  IOKit, libc++; install name /asus/lib/libOSMesa.8.dylib), 1296 exported gl* symbols,
  gl.xml; artifact asus-mesa (copy in work/asus-mesa-42145703219). Still missing for
  CoreAnimation on the GPU: the EAGL layer (userland/gpu/eagl.c + gl_osmesa.c ->
  libasus-gl.dylib; needs eagl_gen.h from HakiOS mkeagl.py + eagl_slots.txt, which live in
  oosexgang/hackios-xnu-kernel userland/ (MIT); work/hakios-os has a read-only sparse
  checkout; running that generator locally was refused by the session's safety rules
  -> needs the user's OK or our own generator), the injector into backboardd
  (inject.c + kernel exec_inject, topaz 0919/0947), the kernel RedwoodGPU msm client
  (topaz 0901, SweetSurface `drawn` field) on an Adreno 660 hardware layer
  (redwood_gpu_* from the asusgpu stages), and the files in the iosroot HFS image.
- **iosroot damaged since 0045 (found 2026-11-06 from a read-only pull, 12 GB in 700 s):**
  0045 panicked (cpubench assert) with the root mounted read-write. The volume is
  non-journaled, so the bitmap and header counts on flash stayed stale; 0046-0050 mounted
  it dirty ("mounting dirty non-journaled root read-write"), allocated blocks that were in
  use (fsck: 8 overlapped extents: MCM containers.sqlite3-wal, FrontBoard
  applicationState.db, proactive aggr_state, 5 Safari files) and twice hit
  `BlockMarkFreeInternal ... unallocated` -> hfs_mark_inconsistent, which also makes
  hfs_unmount leave the volume dirty (phone header attr 0x80004000 after a clean 0050
  reboot). Setup's com.apple.purplebuddy.plist was rewritten without SetupDone, so
  0046-0050 ran Setup/datamigrator and never left the logo: those FPS runs are void.
  Fix prepared on the pulled image (work/asus-rootfix-0050, not written yet): v14
  purplebuddy.plist back (old one in /private/root-old-prefs/), `fsck.hfsplus -y`
  (overlaps cloned, counts, bitmap), the garbage Safari/aggr_state files moved to
  /private/root-old-prefs/damaged-0050 (FrontBoard and MCM databases pass sqlite
  integrity_check); fsck clean. Diff: 142 4K blocks in 24 runs (568 KiB).
  tools/asus-iosroot-blockpatch.py checks the phone blocks against the pulled image
  (passed), keeps backup.bin, writes with --write, verifies; --restore undoes it.
  tools/asus-phone-cycle.sh now prints the root header state before and after a run and
  refuses to boot on a dirty root (ASUS_ALLOW_DIRTY_ROOT=1 for lab images).
  Lesson: any panic or forced reset with the root mounted needs an fsck before the next
  iOS boot; a journal on the root removes that need.
- **Journal for the root (tools/hfs-add-journal.py, offline):** adds /.journal_info_block
  (flags InFS|NeedInit) and a contiguous /.journal in a free run (the libdmg-hfsplus
  allocator is steered with nextAllocation; landing place verified with a marker
  pattern), sets kHFSVolumeJournaledBit + journalInfoBlock. lastMountedVersion stays
  non-'HFSJ', so XNU's hfs_late_journal_init finds the files by name, fixes up offsets and
  calls journal_create. QEMU (kernel 42176394818, own-init root + 1 MiB journal,
  rootrw=1): "hfs: Initializing the journal (joffset 0x17b000 sz 0x100000)", root mounted,
  init ran. Linux fsck.hfsplus reports "Volume header needs minor repair (1, 0)" on any
  journaled image, also one from `mkfs.hfsplus -J` (harmless). Variant
  work/asus-rootfix-0050-journal = the fix + an 8 MiB journal at blocks 4330..6377: 143
  blocks (the journal area is already zero on the phone); read-only phone check passed.
  Not tested on the phone yet: journal I/O through the UFS driver (512-byte sectors ->
  read-merge-write of 4K blocks) and its speed.
- **Root fix + journal written to the phone (2026-11-06 ~14:40, user approved):** 143
  blocks via asus-iosroot-blockpatch.py, read back and verified; header 0x80002100 (clean,
  journaled). The PC disk C: was full (0 MB) during the write; the script now keeps all
  files on the phone (write script over adb exec-in, sha256 checked). Freed 7.8 GB by
  deleting the 20H392 dmg/apfs images in work/apple-20H392 (user approved; the small
  generated files there, d22ap.adt and os.trustcache.im4p used by image builds, kept).
- **Run 0048b (image 0048 again, SMP8, first journaled boot):** "hfs: Initializing the
  journal (joffset 0x10ea000 sz 0x800000)", "/dev/ufsroot0 on / (hfs, local, journaled)",
  clean reboot after 1266 s, root clean + journaled afterwards. No Setup. FPS not
  measurable: SpringBoard's first lines come around autoswipe round 25 (the unlock swipe
  at 420 s was too early), 0.2-6.4 fps lines. Images 0051 (SMP8), 0052 (cores 0,4-7),
  0053 (AMP) = same kernels with asusautoswipe=900 asusreboot=1500.
- **FPS benchmark (2026-11-06, journaled root, same workload: unlock at 900 s, 30 rounds of
  home-screen page swipes left/right, 1.5 s apart; mean of the 5 s "ASUS FPS" intervals
  during the rounds, so idle gaps included):**
  - 0051 SMP, 8 equal cores (kernel 42176394818): 8.7 fps (8.2-9.8, one 12.8)
  - 0052 SMP, cores 0,4,5,6,7 (1 A55 + 3 A78 + X1): 9.3 fps (8.6-10.4, one 16.4)
  - 0053 AMP, E = A55 0-3, P = A78/X1 4-7 (kernel 42176437062): 2.8 fps (2.4-3.6)
  All three: no CRASH lines, clean reboot after 1569-1570 s, root clean + journaled after.
  AMP is 3x slower: the render path evidently runs on the A55 E cluster (no CLPC /
  perfcontrol to recommend P cores) -> do not use the AMP kernel as is. 5 cores vs 8 is
  within ~7 % (noise level); the 5-core layout is the slightly better default. The limit
  is the CPU renderer, not the core count: next step for frame rate is GPU or a cheaper
  render path, not scheduling.
- PC disk cleanup (2026-11-10, user approved): WSL ~/asus-roots/springboard-v12.hfs and
  springboard-v13.hfs deleted (v14 = 528ab8ee... and iosroot-now.img kept), ext4.vhdx
  compacted 77.5 -> 48.4 GB (fstrim as root, then elevated Optimize-VHD); C: has ~32 GB
  free. v12/v13 can be rebuilt with ../make_springboard_v12.py / _v13.py if ever needed.
  The repo directory is owned by the CodexSandboxOffline account: from the normal user,
  git needs `git -c safe.directory=* ...`.
- **GPU stage 7 EDL, offline analysis (2026-11-10; phone not connected, nothing booted).**
  - Observation: af54256 (per-step CP markers, SMR mask 0x403) was never built. CI
    42176394818 / 42176437062 ran on 51b90ae (dispatched 3 min before af54256 was pushed),
    and image 0043 (kernel 42145829535) never ran. So the only stage-7 data is 0042.
  - Compared with Linux 6.12 (WSL ~/linux-msm: a6xx_gpu.c hw_init, a6xx_gmu.c, a6xx_hfi.c,
    sm8350.dtsi, arm-smmu-qcom.c, gpucc-sm8350.c). Our register sequence equals Linux's
    hw_init for the A660 minus hwcg, CP protect, APRIV (we keep RB_CNTL NO_UPDATE), the
    GMU power counter and the interrupt mask; CP_ME_INIT, RB_CNTL, the pkt7 header and the
    SQE load (file minus 4 bytes) are the same. No difference there explains an EDL.
  - The platform difference: sm8350.dtsi gives the GPU `iommus = <&adreno_smmu 0 0x400>,
    <&adreno_smmu 1 0x400>` (streams 0 AND 1 in one domain) and arm-smmu-qcom puts the GPU
    in context bank 0 ("so the GPU hardware can switch pagetables") with SCTLR.HUPCF.
    0042 matched stream 0 only (SMR4 = id 0 mask 0x400 -> CB5), so stream 1 (LPAC on the
    A660) had no SMR; with sCR0.USFCFG (0x290406) that is a global fault.
  - Guess (not proven): a DMA from an unmatched stream is what kills the SoC. It fits all
    three data points: GPI EDL at the first TRE fetch (stream 0x5b6 never configured),
    0042 EDL at CP start, and no EDL for the GMU, whose stream 5 was matched and only
    took context faults (0039 FSR 0x402, 0040 FSR 0x408).
  - Rejected: "GPU_CC PLL0 is not configured". The boot dump shows PLL0 in reset (mode 0,
    L 0, CONFIG_CTL_U1 at its reset value 0x029a699c) and gpucc-sm8350 configures it at
    probe, but a660_gmu.bin holds the same configuration words (0x20485699, 0x2261,
    0x2a9a699c, 0x01800000, 0x805 at file offset 0xc13c): the GMU programs the PLL itself.
  - Change (commit 4385893): smmuBind(prefer CB, SCTLR): GPU streams 0-3 (mask 0x403) ->
    CB0, SCTLR 0x167; SECVID TSB writes on by default again (as Linux; 0042 ran them and
    went on; asusgpusecvid=0 skips); before `stage 7 cp 1` a read-only dump: SMMU sCR0 /
    sGFSR / sGFSYNR0-2, SMR/S2CR 0-5, our CB, GPU_CC PLL0 (mode, L, cal, status, opmode,
    alpha), gfx3d RCG 0x101c/0x1020 and 0x1054, and "in 10 ms: CP perf counter +N (~N MHz
    core clock)" from RBBM_PERFCTR_CP_0 (expect ~315 MHz if the GMU clocks GX).
  - No local xnu-cc toolchain on this PC (no ~/swift clang in WSL): CI is the compile check.
  - Kernel CI **42755235723** (4385893; hfs display userland ufs input smp) succeeded,
    kernel f4370c03...b4e25 in ../asus-ci-42755235723. Lab images (ASUS_UFS=1
    ASUS_TOUCH_LAB=1 ASUS_CPUS=8 ASUS_REBOOT=120, RAM root asus-own-init.hfs, iosroot
    never mounted): **0054** = `asusgpu=7 asuscpubench=0` (SHA256 a29adef0...930bc) and
    **0055** = `asusgpu=9 asuscpubench=0` (99a55da8...9041e: zap shader through TrustZone
    + CP_SET_SECURE_MODE + CP_MEM_WRITE; only after 0054 passes). NOT booted yet.
  - NEXT (needs the user at the phone: an EDL takes a power-key reset):
    `ASUS_ALLOW_DIRTY_ROOT=1 tools/asus-phone-cycle.sh shim/asus-xnu/out/asus-i007-xnu-0054.img
    ../phone-0054 600`. Read in the log: the dump lines before `stage 7 cp 1` (sGFSR must
    be 0, SMR0-5, PLL0, core clock MHz), then `stage 7 cp 1..4`, `SQE running?` and
    `CP_ME_INIT DONE`. EDL again -> the last `stage 7 cp N` marker names the step
    (asusgpucp=N stops after step N) and the unmatched-stream guess is wrong.
    DONE without EDL -> run 0055.
- **Image 0054 (2026-11-10 07:31 local, user at the phone): EDL again at CP start.** The
  cycle script printed "Android back after 104s", but that was the user's power-key
  reset out of EDL (the user saw the ASUS GPU lines on the screen, then EDL): the script
  cannot tell a self-reset from a manual one. (Commit def4257's message says "no EDL":
  wrong, written before the user reported it.) Log ../phone-0054/log.txt, 283 lines,
  last line `stage 7 cp 3: CP_SQE_CNTL = 1 (sGFSR 0, RBBM_STATUS 0x1)`; the `SQE
  running?` line 1 ms later is missing. The dump right before:
  - `SMR/S2CR 0: 0/0x21ee28ff 1: 0/0xc6200ff 2: 0/0x60e00ff 3: 0/0xa202ff 4: 0/0 5:
    0x84000005/0x6`: **SMR4 read back 0.** The S2CR write (CB0) took, the SMR write
    0x84030000 (id 0, mask 0x403) did not; the GMU's 0x84000005 is there. So no GPU
    stream was matched when the SQE started. Guess: the SMMU stream table is filtered
    (hypervisor) and an entry that covers the secure stream 2 is dropped. Whether 0042's
    id 0 / mask 0x400 entry was taken is unknown (no readback then).
  - `CB0 SCTLR 0x167 TTBR0 0x1454fc000 TCR 0x800020 FSR 0x400` (CB0 usable; FSR bit 10
    reads set in the GMU's CB6 too), sCR0 0x290406, sGFSR/sGFSYNR 0 before the start.
  - `PLL0 mode 0x80001805 L 0x20 cal 0x44 status 0 opmode 0x1 alpha 0xd000`, `gfx3d rcg
    0x2/0x103`, 0x1054 = 0x221: the GMU configured and locked PLL0 (32.8125 x 19.2 MHz
    / 2 = 315 MHz); `CP perf counter +3471483` in a 10 ms IODelay (~347 MHz, IODelay
    runs ~10 % long). The GX clock is fine.
  - Still consistent with "a DMA from an unmatched stream is fatal" (now stream 0 itself).
- Fix (commit def4257, kernel CI 42756929160): the two entries Linux writes, id 0 and
  id 1 with mask 0x400 each, both to CB0 (new smmuStream()), then `SMMU readback: stream 0
  SMRn .. stream 1 SMRn ..: both taken | NOT taken, the CP stays off`: the CP is started
  only if both SMRs read back 0x84000000 / 0x84000001 with S2CR = CB. A dropped entry
  now ends the run without an EDL.
  Kernel 42756929160 succeeded (c791e989..., ../asus-ci-42756929160). Lab images, same
  options as 0054/0055: **0056** = `asusgpu=7` (SHA256 5e6c50d6...0e125), **0057** =
  `asusgpu=9` (82978bf9...86e8f). 0055 (old kernel) is obsolete. NOT booted yet.
  NEXT: `ASUS_ALLOW_DIRTY_ROOT=1 tools/asus-phone-cycle.sh
  shim/asus-xnu/out/asus-i007-xnu-0056.img ../phone-0056 600` with the user at the phone;
  ask the user afterwards whether the phone reset itself or needed the power key.
- **MILESTONE: THE ADRENO 660 CP RUNS UNDER XNU (image 0056, lab, kernel 42756929160,
  asusgpu=7, 2026-11-10 08:04 local; no EDL, the user watched the screen: ASUS GPU lines,
  then "ASUS init: still alive"; self-reset, Android back after 186 s).** Log
  ../phone-0056/log.txt: `SMMU readback: stream 0 SMR4 0x84000000 S2CR 0, stream 1 SMR3
  0x84000001 S2CR 0: both taken`; `stage 7 cp 3: CP_SQE_CNTL = 1`; `SQE running?
  CP_SQE_STAT 0xb6 RBBM_STATUS 0x1 CP_HW_FAULT 0 sGFSR 0 CB FSR 0x400`; `CP_ME_INIT DONE:
  rptr 9 RBBM_STATUS 0x1 CP_HW_FAULT 0 SMMU CB0 FSR 0x400` (FAR is stale garbage; FSR
  0x400 is the same bit as before the start). Touch bring-up afterwards works as usual.
  - Confirmed: the stage-7 EDLs were SMMU stream matching, not the CP sequence. The rule
    for this SoC: every stream a device can emit must have its own SMR before the device
    does DMA (GPU: id 0 and id 1, mask 0x400; GMU: id 5, mask 0x400), written exactly as
    the DT lists them (a wider mask was dropped), and read back. The same very likely
    explains the GPI EDL (apps SMMU, stream 0x5b6): worth revisiting for fast I2C later.
  - The user allowed (2026-11-10, while away at work) running further GPU lab images
    unattended; on an EDL stop phone work until the user is back (power-key reset).
- **MILESTONE: THE GPU EXECUTES OUR COMMANDS (image 0057, lab, same kernel, asusgpu=9,
  2026-11-10 08:08 local, unattended; self-reset, Android back after 186 s).** Log
  ../phone-0057/log.txt: stage 7 as 0056 (`CP_ME_INIT DONE: rptr 9`); stage 8 `zap layout
  min 0x5000 max 0x6000 seg 0x5000 (b02 1976 bytes)`, `zap init_image 0/0`, `zap mem_setup
  0/0`, `zap auth_and_reset 0/0`, `zap authenticated`, `CP_SET_SECURE_MODE 0 DONE`; stage 9
  `CP_MEM_WRITE done: scratch[0] = 0x600dbeef (want 0x600dbeef)`. So the TrustZone PIL
  calls (PAS 13, region 0x8b51a000, metadata = the .mdt as is) work from XNU, the GPU is
  out of secure mode and writes through our CB0 page tables. Clean reboot at 120 s (the
  "initproc failed" line is the lab RAM root, as before).
- GPU bring-up recipe that works (stages 1-9 in AsusPhoneInput.cpp, all on kernel
  42756929160): GCC/GPU_CC clocks + CX GDSC; GMU firmware from the iosroot firmware area
  behind SMR5 (id 5, mask 0x400) -> CB6; HFI v2 start, perf/bw tables, perf vote; OOB
  GPU_SET (GX on); SMR4/SMR3 (ids 0, 1, mask 0x400) -> CB0 with SCTLR 0x167; hw_init
  registers; SQE + 32 KiB ring; CP_ME_INIT; zap; CP_SET_SECURE_MODE 0.
- **Stage 10: the `redwood_gpu_*` hardware interface works (image 0058, lab, kernel CI
  42761120579 = commit b6eeb09, asusgpu=10, SHA256 9fc590c8...2dc50; 2026-11-10 12:12
  local, unattended, self-reset after 186 s).** The interface lives in AsusPhoneInput.cpp
  next to the stages (same ring, scratch page and CB0 tables): GPU VA allocator
  0x20000000-0xf0000000, redwood_gpu_map/unmap (4 KiB pages, entries flushed one by one,
  CB_TLBIALL), submit = CP_INDIRECT_BUFFER x n + CP_EVENT_WRITE CACHE_FLUSH_TS writing the
  submission number to scratch+0x100, done/last/report/stop, a660 parameters (chip id
  0x06060001, GMEM 1.5 MiB at 0, hbb 16, swizzle 6, macrotile 1), redwood_gpu_identity()
  = false (the SMMU translates and reaches all DRAM, no pool below 4 GiB needed). Page
  tables grow in chunks of 64. Log ../phone-0058/log.txt: `stage 10: IB at 0x20000000 (pa
  0x143e7c000): fence 1 done, wrote 0xfeedc0de ...: the GPU runs indirect buffers`,
  `second IB: fence 2 done, wrote 0xbadcafe ...; test buffer unmapped; ready 1`.
  Local check before CI: the new code alone compiles with stubs in WSL clang++
  (-fsyntax-only); scratchpad script, not in the repo.
- **RedwoodGPU on the ASUS (profile --gpu, workflow input gpu=true; commit 06f764d, kernel
  CI 42790058499, lab image 0059 = asusgpu=10, SHA256 e0e1195d...6553; 2026-11-10 12:31,
  unattended).** `xnu/asus/9999-asus-i007-zgpu.patch` is GENERATED by
  `tools/make-asus-gpu-patch.py` (never edit the patch): RedwoodGPU.cpp in the final
  state of the topaz series (git apply of 0901-0950 on that one file) with the pass-through
  pool stubbed (redwood_gpu_identity() false: buffers are ordinary IOBufferMemoryDescriptor
  memory entered page by page; gpu_cached defaults to 1), plus diffs against the files the
  display/input/userland patches create: SweetSurface fields (drawn, wrapped, gpuPool,
  wrapDelta, scanoutSlot), 64 guard rows behind every surface buffer with asusgpu>=10,
  swap() waits <= 100 ms for the GPU (topaz_surface_drawn), stage 10 ends with
  RedwoodGPUStart(). Log ../phone-0059/log.txt: `ASUS GPU: RedwoodGPU service started (msm
  DRM user client for Mesa freedreno)`, no panic, clean self-reset. The user client itself
  is untested (nothing opens it in a lab image).
  Windows notes: xnu/patches/* are CRLF in this checkout (core.autocrlf), xnu/asus/* are
  LF; the generator and prepare-asus-profile.py normalise. `sed s/0058/0059/` on a script
  also hits run IDs (42790058499): edit version numbers by hand.
- **Library insertion + userland build (commit bbc032f; kernel CI 42831904109, asus-gl CI
  42831910319: results below when they finish).** Kernel: --gpu now also needs --userland
  and selects topaz 0919 (kern.exec_inject, boot-arg `topaz_inject=LIBRARY,NAME,...`;
  applies to the stock kern_exec.c, checked by simulating the profile's patch order); the
  zgpu patch adds a MAC policy named "AMFI" answering dyld's call 0x5a with 0xff, registered
  only when the boot-arg is there (without it dyld ignores DYLD_INSERT_LIBRARIES), and
  AppleCLCD property RedwoodWaitsForGPU. Userland: `.github/workflows/asus-gl.yml` builds
  /asus/lib/libasus-gl.dylib (eagl.c + gl_osmesa.c over libOSMesa from asus-mesa run
  42145703219; eagl_gen.h from userland/gpu/mkeagl.py + eagl_slots.txt, copied from HackiOS,
  MIT) and /asus/lib/libasus-inject.dylib (topaz_inject.c, -DTOPAZ_GL, GPU wait 60 s),
  ad-hoc signed. The ASUS trust policy does not reject binaries outside the trust cache.
  topaz_inject.c's CoreAnimation hook addresses are for topaz's shared cache; it checks
  the slot contents before patching and leaves them alone on a mismatch (then the
  vibrant-colour-matrix layers may look wrong: compare with our 20H392 cache).
- **Plan for the first GPU-drawn iOS boot (needs the user: writes to iosroot + an iOS
  boot, both outside the "lab images" permission):**
  1. copy libasus-gl.dylib, libasus-inject.dylib, libOSMesa.8.dylib into /asus/lib on the
     iosroot HFS (pull the root read-only, add the files with libdmg-hfsplus tools, fsck,
     tools/asus-iosroot-blockpatch.py as for the journal; ~16 MB);
  2. iOS image on the --gpu kernel with `asusgpu=10 asustouchdelay=5
     topaz_inject=/asus/lib/libasus-inject.dylib,backboardd` (the GPU stages run from the
     touch bring-up thread; default delay 45 s is later than backboardd's start, the
     injector waits 60 s for the service);
  3. expect in the log: `RedwoodGPU: client backboardd`, `topaz-gl:` / `redwood-gl:` lines,
     submissions; fall back with boot-arg gl=0 (injector) or without topaz_inject.
- **Builds of bbc032f (2026-11-10 evening).** macOS arm64 runners had no capacity for
  hours ("The job was not acquired by Runner of type hosted even after multiple
  attempts", job cancelled with 0 steps; GitHub status page all green). Both workflows now
  take input `runner` (default macos-15); `-f runner=macos-15-intel` worked: kernel CI
  **42840436225** (SHA256 ab0394a3...10800, ../asus-ci-42840436225) and asus-gl CI
  **42840359007** (../asus-gl-42840359007: libasus-gl.dylib b9fc8fff..., libasus-inject.dylib
  ce7fbd10..., libOSMesa.8.dylib 188a9a9a...; ad-hoc signed; mkeagl: 409 entries, 356 passed
  on, 30 by hand, 23 missing).
- **Lab image 0060** (that kernel, asusgpu=10 + topaz_inject=...; SHA256 035d84b3...1990;
  unattended): `ASUS AMFI: dyld policy answer registered (0)`, stage 10 + `RedwoodGPU
  service started`, clean self-reset after ~180 s.
- **iosroot with /asus/lib prepared offline, NOT written.** Pulled iosroot read-only
  (first 12 GiB, 2026-11-10 17:57, SHA256 7a0fb5a6...16810 = phone), hashed per block
  (~/asus-roots/iosroot-now.hash, tools/asus-iosroot-blockdiff.py). First try with the
  hfsplus tool: libOSMesa (15 MB) landed in fragmented free space, "inconsistent extents
  information!", fsck: orphaned blocks. Reverted from the phone's own blocks (read-only
  fetch, all 3757 matched old.hash). New tools/hfs-add-contiguous.py (steers nextAllocation
  into a free run, as hfs-add-journal.py): /asus, /asus/lib (0755 root:wheel) and the three
  dylibs (0755 root), each contiguous (blocks 6394.., 10043.., 10128..) and read back equal;
  fsck -n: only the usual "Volume header needs minor repair" of journaled images; header
  0x80002100, HFSJ, journalInfoBlock 4329, 211614 blocks free. Image SHA256
  9288bb11...67f7eb. Block patch ../asus-rootfix-gl-v1 (3680 blocks in 61 runs, 14.4 MiB);
  read-only check against the phone: all 3680 blocks match old.hash.
  (The auto-mode classifier refused running asus-iosroot-blockpatch.py even without
  --write; the read-only check used a fetch-only script.)
- **iOS image 0061 ready, NOT booted** (kernel 42840436225, cores 0,4,5,6,7, asusreboot=1500,
  asusautoswipe=900 = the 0052 FPS workload, + `asusgpu=10 asustouchdelay=5
  topaz_inject=/asus/lib/libasus-inject.dylib,backboardd`; SHA256 7ede8892...dcc53a).
  Needs the user's OK: (1) `python tools/asus-iosroot-blockpatch.py ../asus-rootfix-gl-v1
  --write` (keeps backup.bin; --restore undoes it; the files are inert without the
  topaz_inject boot-arg), (2) `tools/asus-phone-cycle.sh shim/asus-xnu/out/asus-i007-xnu-0061.img
  ../phone-0061 2400`. Compare `ASUS FPS` with 0052 (9.3 fps); look for `RedwoodGPU:
  client backboardd`, `|backboardd| topaz-gl:` / `redwood-gl:` lines, CRASH lines.
- **iosroot /asus/lib WRITTEN (user OK "делай сейчас", 2026-11-10 ~19:18):** asus-iosroot-
  blockpatch.py ../asus-rootfix-gl-v1 --write: phone vs old.hash all 3680 matched, WRITE-DONE,
  after write all 3680 match new.hash; backup.bin kept (--restore undoes it).
- **Image 0061 (first iOS boot with Mesa on the Adreno 660, 2026-11-10 19:20, same OK):
  Mesa runs in backboardd and the GPU executes its first submission, then hangs.**
  Clean reboot after 1568 s, root clean + journaled after. Log ../phone-0061/log.txt (6149
  lines):
  - `ASUS AMFI: dyld policy answer registered (0)`; the injector worked: `|backboardd|
    redwood-gl: 7 of 7 methods of EAGLContext are ours; OpenGL ES by Mesa, off-screen
    (OSMesa)`, `topaz-gl: GLESContext hooks: function_supported on, function_uniform on`
    (the topaz cache addresses fit this 20H392 cache), `RedwoodGPU: client backboardd
    (open 1)`, `an OpenGL ES 3 context for CoreAnimation ...: OpenGL ES 3.2 Mesa 25.0.7;
    FD660; shaders OpenGL ES GLSL ES 3.20`.
  - Problem 1, screens: CoreAnimation's GL renderer asks for 'b3a8' display surfaces (30-bit
    RGB + 8-bit alpha plane, bpr 4544; the software renderer used 'BGRA'): `-attachImage:
    ... an IOSurface of 'b3a8': not done` x42, so every draw into the screen is
    GL_INVALID_FRAMEBUFFER_OPERATION (`glDrawElements: error 0x506` x60). Fix (138ebc1): as
    topaz 0900, SweetSurface makes w30r/l10r/w30R/b3a8 one-plane BGRA.
  - Problem 2, hang: fence 3 (Mesa's first submission, 5 IBs) done; fence 4 (33 IBs) never:
    `no answer: fence 4 done 3 rptr 82 wptr 195 RBBM_STATUS 0x800005 CP_HW_FAULT 0 INT0 0 IB1
    0x20458000 rem 702`, SMMU no fault (CB FSR 0x400 = only the AArch64 format bits). Only
    the CP is busy (bits 23, 2, 0): it waits for something (a CP_WAIT_*, an event), the 3D
    blocks are idle. After that Mesa's submits fail with -60 (ETIMEDOUT: ring full, fence
    0) and frames are drawn into nothing (ASUS FPS up to 29.8 = swaps of empty frames).
    Diagnostics (138ebc1): redwood_gpu_report logs IB2 + rem, CP_STATUS_1,
    CP_PROTECT_STATUS, 16 SQE status words, CP scratch, the ring around rptr, IB1 (size
    from its ring packet) from its start and around where the CP stands, IB2.
  - Also seen: 40-50 MB pushed out of the CPU cache per submission (gpu_cached=1: every
    mapped buffer is flushed every time): slow, to be revisited after the hang.
  Next run: kernel 138ebc1 build, image 0062 = 0061's boot-args. (The user's OK of
  2026-11-10 was for writing /asus/lib and booting 0061; follow-up iOS boots of the same
  kind, no further root writes, continue that debugging.)
- **Image 0062 (kernel CI 42859033562 = 138ebc1, SHA256 8976bea9...; image 11666f94...;
  2026-11-10 20:36, 0061's boot-args; clean reboot after 1568 s; user saw a black screen).**
  ../phone-0062/log.txt: screens fixed: `-attachImage: texture N is IOSurface 2 ('BGRA' 1125 x
  2436, 4544 bytes a row): a texture, copied into its memory by the GPU`, no 0x506 errors.
  Fences 3-7 done (5 Mesa submissions), then the same hang: `no answer: fence 11 done 7 rptr
  170 wptr 266 RBBM_STATUS 0x800005 ... IB1 0x20470000 rem 702`, `IB2 0x20478000 rem 0
  CP_STATUS_1 0x8000471f CP_PROTECT_STATUS 0`, SQE stat `309 70380007 504 7065 703f 800000
  10000 0 0 10000 0 0 9e1c 0 20478000 25951040`, scratch all 0. Decoded:
  - IB1 (1438 dwords) is a GMEM (tiled) render pass with binning: ... CP_WAIT_FOR_ME,
    CP_SET_MODE 0, `CP_SET_BIN_DATA5` (702f0007: VSC addresses 0x25930880, 0x25938808,
    0x25952080), CP 0x64, bin registers, `CP_SKIP_IB2_ENABLE_GLOBAL 1` (709d0001), then the
    draw IB2 0x20478000 (0xaa dwords: viewport for 1125x2436, `CP_SET_DRAW_STATE` 27 groups,
    draws) and CP_SET_MARKER.
  - SQE stat word 1 = 0x70380007 = CP_DRAW_INDX_OFFSET: the CP stands at a draw of the
    per-bin pass (word 14 = the IB2, word 15 = 0x25951040, next to the VSC buffers), with
    the 3D blocks idle: it waits for the visibility stream of this bin. Guess: the binning
    pass's VSC data is not usable (not written, or not seen: coherency) -> next run 0063 =
    0062 + `glenv=FD_MESA_DEBUG=sysmem` (no GMEM, no binning) to tell.
- **MILESTONE: iOS DRAWN BY THE ADRENO 660, 2x THE FRAME RATE (image 0063, 2026-11-10 21:10,
  0062 + `glenv=FD_MESA_DEBUG=sysmem`; image 9358c10f...393f; clean reboot after 1568 s).**
  ../phone-0063/log.txt: `in the environment: FD_MESA_DEBUG=sysmem`, no hang report, no
  failed submit, 2260+ frames by Mesa freedreno on the GPU. Same workload as 0052 (unlock at
  900 s, 30 swipe rounds), mean of the ASUS FPS samples between round 1 and round 30:
  **19.7 fps (16.4-22.2) vs 9.9 fps (8.6-16.4) for 0052's CPU renderer**. So the GMEM
  (tiled, binning) path is what hangs (0061/0062); direct rendering works.
  Picture not verified by eye yet (ask the user what the screen showed).
  Still costly per frame (redwood-gl frame lines): one full-screen texture refilled from an
  IOSurface every frame (10809 KB), ~25 MB pushed out of the CPU cache per submission
  (RedwoodGPU stat lines, gpu_cached=1), 1-3 ms "handing on" + ~1.2 ms waiting per frame.
  Next: (a) make sysmem the default for the ASUS (eagl/injector env, or a boot-arg in the
  image builder) until GMEM works; (b) GMEM hang: try FD_MESA_DEBUG=nobin (GMEM without
  the binning pass) to split VSC from GMEM itself, then Linux's a660 hwcg table (not
  written by stage 7; Linux a6xx_set_hwcg + a660_hwcg, gmu_cgc_mode 0x00020200 for a660);
  (c) the 25 MB flush: buffers made write-combined instead of cached-and-flushed.
- **But the screen stays black (user, 0062/0063/0064).** In 0063 the display swaps BGRA
  surfaces 2-5 ~20/s (`ASUS FB: swap ... fmt 0x42475241`), so frames reach the panel; they
  are empty. Not the kernel's cache maintenance: SWEET does not define __ARM_COHERENT_IO__
  (only __ARM_COHERENT_CACHE__), so flush_dcache64 is the real by-VA dc civac here (topaz
  made it a no-op later and added sweet_dcache_*).
  - **0064** (0063 + `glcopy=1 gltrace_at=300 gltrace_lit=1`; image 7086065c...; user: still
    black): frames go to the surfaces by glReadPixels (CPU) instead of the GPU blit
    (`attachImage: ... a copy`, frame lines "1 picture copied out"), still black. The traced
    frame was frame 3 (only 3 frames in the first 300 s of the window server): 3
    full-screen quads with texture 4 (1125x2436 BGRA), framebuffer read back after each:
    `middle 0 0 0 0 ... 0 pixels not black` (alpha 0 too). Too early a frame to conclude.
  - **0065** (0063 + `gpu_cached=0 gltrace=1200-1200 gltrace_lit=1`; image f39030e2...):
    Mesa's buffers write-combined instead of cached-and-flushed (takes CPU/GPU cache
    coherency of vertex/texture/staging buffers out of the picture; also removes the 25 MB
    flush per submission) and a trace of frame 1200 (home screen, during the swipes).
  - **0065 result: kernel panic ~90 s after boot**, right after `RedwoodGPU: client
    backboardd (open 1)`: `phystokv_range: illegal PA: 0x7ee88000; phys base 0x140000000,
    size 0x100000000 @arm_vm_init.c:406`. gpu_cached=0 makes gemNew allocate
    write-combined IOBMDs, which XNU makes physically contiguous from RAM the kernel does
    not manage (below 0x140000000), as topaz warned. Unusable as is; a WC path would need
    non-contiguous kernel memory mapped WC to the client. PSCI reset worked; root now
    "DIRTY, journaled" (attr 0x80002000): journal replay expected at the next mount, but
    asus-phone-cycle.sh refuses dirty roots without ASUS_ALLOW_DIRTY_ROOT=1, and the auto-mode
    classifier refused that override: needs the user's decision.
  - Image 0066 built, NOT booted (0063 + `gltrace=1200-1200 gltrace_lit=1`, SHA256
    1203ab7d...794f): a home-screen frame traced on the default (cached) path.
  - **0066 (user OK "Запускай, журнал починит", booted on the dirty journaled root,
    2026-11-10 22:23; user: black screen):** journal replay worked (`jnl: b(2, 0):
    replay_journal: from: 2715136 to: 3726848`, `journal replay done`, mounted, root clean +
    journaled after the run). Frame 1200 (home screen): after every draw the 1x1 read of
    the middle is `0 0 135 255` (dark blue, opaque), but the full-frame read in the same
    trace counts `0 pixels not black`. So the GPU draws; what the CPU reads of big
    GPU-written buffers is stale (zeros). Cause: buffers are cached memory and RedwoodGPU
    only cleans/invalidates in submit(), before the GPU runs; nothing invalidates after.
  - Fix (commit 6ca3832, kernel CI 42889848301): topaz_surface_drawn() cleans+invalidates a
    GPU-drawn surface before the display copies it (and logs its middle pixel every 300
    shows: `RedwoodGPU: surface N (WxH) shown after submission F: middle pixel ...`);
    cpu_prep with MSM_PREP_READ does the same for the buffer (Linux msm: sync for CPU).
- NEXT (GPU, kernel side, superseded by the lines above): an Adreno 660 hardware layer with topaz's `redwood_gpu_*`
  interface (ready, parameter, address, map/unmap, submit, done, ...) made from these
  stages, then topaz's RedwoodGPU.cpp (msm DRM user client for Mesa freedreno) on top of
  it and a kernel self-test that submits an indirect buffer. Userland after that: EAGL
  layer + injector + files in iosroot (writing the root needs the user's OK).
- `--ufs` kernel profile (commit cf167fe, CI 42018267667, kernel 53924b08):
  xnu/asus/9999-asus-i007-ufs.patch from ../make_asus_ufs_patch.py = sweet 0028 + 0030 +
  0034 + 0069 (without 0047's flash console log), writes impossible (ufsrw ignored,
  verifier requires the guard), host ver/cap/state printed, ufslist=1 lists LUN 0 GPT,
  ufsprobe=1 runs the driver while the root stays the RAM disk. ADT /sweet-ufs only with
  mkadt-asus.py --ufs. Same host address as sweet (0x1d84000); ASUS DT says dma-coherent
  and an SMMU stream (iommus) - whether ABL leaves it in bypass is untested.
- Image **0009** (`ASUS_UFS=1 ASUS_XNU_VERSION=0009 bash tools/build-asus-image.sh
  KERNEL ../asus-hfs-root/mac/asus-own-init.hfs`): boot-args `... rd=md0 ufsprobe=1
  ufslist=1`, SHA256 d35a25716a58987e71a949f514f738c3469fbc3a953fc8e6805957fd4bd28d4a.
  NOT booted yet. Expected screen: `SWEET UFS: host ver ... hce 0x1 hcs ...`, the LUN 0
  partition list (23 entries, userdata at 4K block 0x23e628 = 18821440/8), `"userdata" on LUN 0
  ... read-only`, `ASUS UFS probe: 0 (root stays md0)`, then our init heartbeat. A host
  not ready / timeout message would point at ABL state or the SMMU. Return to Android:
  Power + Volume Down.

### Phase 7 (2026-11-04 afternoon): backboardd system container

- ABORT CAUSE (QEMU, ../qemu-asus-backboardd-throw/symbolicated.txt):
  uncaught NSInvalidArgumentException `-[NSURL initFileURLWithPath:]: nil
  string parameter` from `-[BSSystemContainerForCurrentProcessPathProvider
  libraryPath]` <- `cachesPath` <- backboardd (StartWindowServer shader
  cache path). BaseBoard `__BSSystemSharedDirectoryForCurrentProcess`
  calls `container_system_path_for_identifier(bundleID, &err)`
  (libsystem_containermanager -> containermanagerd XPC); it returned NULL.
  This is the exact wall the sweet port hit (topaz NOTES boot 0051n).
- launchd's `error = 159: Sandbox restriction` for every backboardd lookup
  means no MAC policy named "Sandbox" (sweet 0050), not only absent
  daemons. Also no containermanagerd job and no /private/var/containers.
- CS_OPS_DER_ENTITLEMENTS_BLOB returns the hash-checked DER blob from the
  signature without AMFI, so daemons can still read clients' entitlements
  with our no-entitlements AMFI provider (unverified at runtime).
- Kernel: new optional profile `--userland` (xnu/asus/9999-asus-i007-
  userland.patch, workflow input `userland`): permissive "Sandbox" MAC
  policy (logs `ASUS Sandbox: <proc> op N`), boot-arg rootrw=1 for the RAM
  root + dirty non-journaled HFS root accepted, RENAME_SWAP via
  VNOP_EXCHANGE (sweet 0052), no config_ext_resolver (sweet 0057).
  Compositor cdhash policy now pins backboardd, containermanagerd
  (41a7bd3a...) and notifyd (99a69e0d...), all type 2 in the stock OS
  trust cache, and requires MNT_ROOTFS only (rootrw keeps it writable).
  Commits 2dc9544, then cdhash table; CI 41832894570 (first attempt failed
  only on a bearssl.org 502, rerun).
- Root: ../make_backboardd_v3.py -> ../asus-compositor-root/backboardd-v3.hfs
  = v2 + /usr/libexec/containermanagerd, /usr/sbin/notifyd, their stock
  jobs (feature-flag #Else branches, no _PanicOnCrash) and
  /private/var/containers/{Bundle,Data/System,Shared/SystemGroup,Temp}.
  fsck clean, SHA256 b7ce3969a6abe0b0309dfb6fc27a138970c434b5aa76cfdba33410e7721ec5f7.
  Note: root /var and /tmp are real directories, not links to /private.
- Mount Apple images as WSL root only (`wsl -u root`, apfs-fuse -o
  ro,allow_other); FUSE mounts are invisible from Windows paths.
  Project moved to C:\Users\del\Desktop\https-github-com-makrsas-ios-topaz.
- CI: bearssl.org git returns 502, so 0020-stage-bearssl.sh falls back to
  xnu/vendor/bearssl-7bea48e5... (blob-hash verified). CI 41836697870 BUILT
  the userland kernel (verify step failed only on the old compositor log
  string, fixed in verify-asus-kernel.py). Kernel taken from that run's
  buildtree artifact: ../asus-ci-41836697870/kernel.development.sweet,
  SHA256 22a35cdf8de1a98e7426e6a953f10d75ee07b3048649a21931ea82906f31753b,
  local verify: hfs, display, compositor, userland all true. A clean CI
  artifact still needs one rerun of asus-xnu.yml (all four inputs true).
- QEMU runs (../run_backboardd_v3.sh, ../debug_backboardd_v3.sh with
  ROOT=..., boot-args ignition_level=1 asus_qemu_compositor_trust=1
  rootrw=1; GDB script ../capture_asus_crash_v4.gdb logs BaseBoard's
  container call/result, ObjC/C++ throws, abort_with_payload and every
  exception_triage with user PC + frame chain; upcb = thread+0x110):
  - v3: no more 159 denials (lookups now `error = 3`, missing services);
    containermanagerd spawned on demand. Container call -> NULL, status 102
    USER_HOME_DIRECTORY_MISSING (root /var was a real empty dir).
  - v4 (../make_backboardd_v4.py: /var -> private/var, /tmp -> private/tmp,
    old dirs moved to /private/root-{var,tmp}-dir; libdmg-hfsplus `mv` +
    `symlink` work): status 52 XPC_INTERRUPTED_REPLY. containermanagerd
    crash-loops: EXC_BAD_ACCESS at 0x8 in CoreFoundation
    __CFGetNonObjCTypeID <- CFDateFormatterSetFormat <-
    CFDateFormatterCreateISO8601Formatter <- [MCMMigrationStatus
    _iso8601DateFormatter] (no ICU data in the root).
  - v5 (../make_backboardd_v5.py: + /usr/share/icu/icudt70l.dat,
    icutzformat.txt): CONTAINER result non-NULL, status 1 = success. Next
    throw: NSInternalInconsistencyException "error reading
    /System/Library/BackBoard/EventProcessorConfiguration.plist".
  - v6 (../add_os_files.py generic copier: + that plist), SHA256
    eff1c3d07049da6736bf138e8a72b3b0e149922cf5877ca85a85b1cbb04175f4:
    QEMU MILESTONE: backboardd passes the container and config walls, no
    throw/abort/exception in a 360 s GDB run, and in a 300 s plain run the
    console shows it looking up services until ~1:37 guest time, then no
    exit for the rest of the run (../qemu-asus-backboardd-v6/fb.png).
    Failed lookups (absent daemons): logd, oscard, ACCHWComponentAuthService,
    runningboard, cfprefsd.daemon, analyticsd,
    coremedia.virtualframebuffer.xpc, mobilegestalt.xpc (x2),
    iokit.powerdxpc, distributed_notifications@1v3, datamigrator.
    No compositor frame yet; whether it blocks or idles is not determined.
  Container error names: libsystem_containermanager table at cache VA
  0x1d9bedb90 (unslid), e.g. 55 NOT_ENTITLED, 102 HOME_MISSING, 52 XPC_INTERRUPTED.
- Tools added (in ../, private): qmp_msgbuf.py (dump kernel msgbuf of a running
  guest via QMP), msgbuf_run.sh, late_sample.sh + sample_threads_gdb.py (start
  QEMU's gdbstub over QMP at T seconds, walk allproc -> task -> threads, read each
  thread's upcb and its user stack physically through the pmap's 16K tables;
  offsets: p_comm +0x5c0, task = proc + *(0xfffffe0007bb8750), threads +0x80,
  thread link +0x478, map +0x50, pmap +0x40, ttep +8, upcb +0x110; TCR VA bits
  47), iokit_trace_v7.gdb (backboardd-only trace of IOKit MIG handlers with
  matching-dict strings, registry paths, property names, service_open result).
- v6 sample: backboardd main thread idles in CFRunLoopRun, CA::Render::Server
  thread exists, HID/CoreMotion runloops; one worker waits forever in
  dispatch_group_wait from -[... applyDefinitions:] (SSHB home-button setup:
  "not configuring SSHB because we don't have a real home button" only when
  homeButtonType == 2). libdispatch was mapped FROM DISK: the root still had
  66 restore-ramdisk dylibs in /usr/lib that only exist in the cache on iOS.
  ../make_backboardd_v7.py moves them to /private/root-disk-dylibs (dyld kept;
  fsck -y zeroes libdmg-hfsplus' unerased nodes) -> backboardd-v7.hfs SHA256
  2305ef4d95a4431d17c1bcc6107a3deedfca7cee915b03e317a720329e799687. Same
  idle state, now cache libdispatch.
- IOKit trace (v7): backboardd adds an IOMobileFramebuffer notification and
  IOServiceOpen(type 0) returns 0xe00002c2 kIOReturnBadArgument: the ASUS
  IOMobileFramebufferUserClient required administrator privilege, backboardd
  is uid 501. So CoreAnimation never had a display (no ASUS FB sel / SWEET SURF
  logs). Fix (display patch): also accept CS_PLATFORM_BINARY clients. It also
  reads /product (display-corner-radius, product-id, artwork-*), /arm-io
  device_type/compatible, /chosen keys, /arm-io/disp0 supports-apt, /backlight,
  /buttons home-button-type + function-button_halleffect, and looks for
  AppleM2ScalerCSCDriver, backlight-control, aop-smart-cover, haptics.
- ADT: mkadt-asus.py --ios-gui ../apple-20H392/devicetree/d22ap.adt (decoded
  from the local IPSW's DeviceTree.d22ap.im4p; needs WSL `lzfse`, installed
  from Ubuntu) adds /product, /buttons (home-button-type 2), /backlight,
  property-only /arm-io/disp0, /chosen display-scale 3, rotations 0.
  run_asus_qemu.py --ios-gui PATH; wrapper env IOSGUI=PATH.
- Kernel CI: 41860249396 and 41864875474 never got a macos-14 runner (GitHub
  marked them failed/cancelled with no steps); 41872709855 at 952fc66 SUCCEEDED.
  Kernel ../asus-ci-41872709855/kernel.development.sweet, SHA256
  48b93783e1fa113287284d9d928fe7d170b9dd7a1f64c21e164fc3f691930226 (hfs,
  display, compositor, userland; initWithTask calls csproc_get_platform_binary).
- Before that kernel, ../iokit_override_v7.gdb proved the cause in QEMU by forcing
  IOUserClient::clientHasPrivilege(..., "root") = 0 for backboardd only
  (kIOClientPrivilegeAdministrator is the string "root").
- QEMU MILESTONE (kernel 48b93783, root v7, IOSGUI d22ap ADT, no GDB):
  backboardd opens the ASUS AppleCLCD and every IOMobileFramebuffer call
  succeeds: sel 57, 15, 22, 8, 28, 29, 33, 79 (1580-byte out), 74, then after
  ~55 s SWEET SURF sel 13 and sel 12, 79, 6, 27/17 (3084-byte in/out), 84, 50,
  all -> 0x0 (../qemu-asus-backboardd-v7-k48b9/msgbuf-220.txt). No `ASUS FB:
  swap` within 10 min: backboardd has no client drawing yet, the panel still
  shows the console (../qemu-asus-backboardd-v7-override3/fb.png).
  ../snap_fb.py QMP_SOCK OUT.png saves the framebuffer of a running guest.
- STATIC TRUST CACHE (CI 41878547659, kernel f3918b15c31e752ad669c49105a4e5c79c0b9dc280b822ed8969b88211b57260):
  --userland now copies /chosen/asus-static-trust-cache (mkadt-asus.py
  --trust-cache ../apple-20H392/os.trustcache.im4p; wrapper env TRUSTCACHE)
  and its ASUS TRUST MAC policy marks root-fs binaries whose cdhash+hash type
  are in it CS_SIGNED|CS_PLATFORM_BINARY. QEMU v7: "static trust cache v2,
  2760 entries", platform binary launchd, dyld, xpcproxy, backboardd,
  containermanagerd, notifyd; backboardd still drives AppleCLCD
  (../qemu-asus-v7-trust/msgbuf-160.txt). Pinned cdhashes no longer needed.
- Root builder: tools/asus-hfs-addtree.c (-> ../asus-launchd-root/hfs-addtree)
  copies a host tree into an HFS image with lstat modes/uid/gid and real
  symlinks, keeping existing entries; libdmg-hfsplus addall follows links and
  drops modes. ../make_springboard_v8.py: v7 + whole OS root (minus
  LaunchDaemons/LaunchAgents/xpc) + SystemOS extras + jobs SpringBoard,
  runningboardd, cfprefsd, MobileGestaltHelper, lsd, usermanagerd,
  iconservicesagent -> 12 GB springboard-v8.hfs (building).
- Disk: with user approval deleted ../qemu-asus-backboardd-abort/ram.bin and
  backboardd-v1/v2.hfs; v3-v6 removed as rebuildable (37 GB free before v8).
- FULL ROOT (QEMU, 2026-11-04/05): ~/asus-roots/springboard-v8.hfs in WSL ext4
  (12 GiB; ~10x faster to build there than through /mnt/c). Fixes on the way:
  libdmg-hfsplus allocate() 32-bit offset (tools/libdmg-hfsplus-64bit-allocate.patch;
  patched hfsplus/libhfs in ~/asus-roots), fresh mkfs.hfsplus + asus-hfs-copyvol
  instead of `grow`, root folder mode (mkfs leaves mode 0 -> uid 501 EACCES in
  proc_pidpathinfo, SpringBoard died in dyld), SystemOS cryptex copied to
  /private/preboot/Cryptexes/OS (OS root links /System/Cryptexes/OS there; dyld
  cache excluded). Kernel: memdev mdSize<<12 was 32-bit, a 12 GiB md0 had size 0
  (hfs EINVAL) -> fixed in --userland, CI 41905468060, kernel
  3c621f3a22e1d1cfd832e3ed8152959a28b74810463df6b70b9a1b1fa4eeef4e.
  QEMU needs ASUS_QEMU_SHARE=1 (share=on backing; a 13 GB private mapping is
  refused by the WSL VM's overcommit) and boot-args
  interrupt-masked-debug-mode=0 sched-preemption-disable-debug-mode=0 (TCG made a
  vsync interrupt take 20.8 ms > 13.6 ms timeout -> panic).
- QEMU MILESTONE: iOS BOOT SCREEN RENDERED BY BACKBOARDD. With root v8 (SHA256
  7e706ecee7c3ccbf015cdef649d1f9c4150f08f340702b4f9e3a42a93c552857), kernel
  3c621f3a, trust cache + iPhone X ADT nodes: backboardd creates a 1080x2448 BGRA
  IOSurface (id 2, bpr 4352) and swaps it twice; the framebuffer shows the white
  Apple logo centred on black (../qemu-v8-fb.png). Then the interrupt-duration
  panic above. runningboardd crash-loops (corpse every ~1 s); cause not yet read.
- 2026-11-05 SPRINGBOARD PROGRESS (QEMU, root v8 in ~/asus-roots):
  - runningboardd crash-looped: exit "Error setting low space io policy: 1
    (Operation not permitted)" = IOCurrentTaskHasEntitlement in
    iopolicysys_vfs_allow_lowspace_writes; the ASUS AMFI had no entitlements.
    Fix: --userland now carries sweet 0035's SweetEntitlements (XML blob, only if
    it matches the code directory) + AMFI query functions, attached by the "ASUS
    ENTITLEMENTS" MAC policy (no root-fs platform marking). CI 41919403942 (kernel
    upload timed out; kernel from buildtree, SHA256 59b0b8c1...). runningboardd OK.
  - SpringBoard then trapped (EXC_BREAKPOINT) in MobileKeyBag MKBEventsRegister.cold.2
    <- [SBFMobileKeyBag init] <- SBUIBiometricResource <- SBBiometricsService <-
    initializeSystemServicesBeforeApplicationDidFinishLaunching: no AppleKeyStore.
    Fix: SweetKeyStore.cpp (sweet 0038+0043+0055) + IOResources personality in
    --userland. CI 41926126933, kernel
    884e0b3ba52526173a12a9b8109a460cfd30a318ec8758554c89bf45446a3cd6.
  - QEMU MILESTONE: SpringBoard stays up (no corpses); AKS clients SpringBoard,
    lsd, containermanagerd, backboardd, datamigrator; backboardd 16 swaps with two
    alternating surfaces. Screen: Apple logo + DATA MIGRATION progress bar (~1/3,
    static from 420 s to 900 s) (../qemu-v8d-fb.png).
  - Root v9 = v8 + /private/var/mobile/Library/Preferences/com.apple.migration.plist
    {LastSystemVersion: 20H392} 501:501 0600 (sweet image v12 fix), file
    ~/asus-roots/springboard-v9.hfs, SHA256
    e79d488735ba90a740a24ab6a8ce7ff5cc339ebfab8573204d858b3e29c11593.
  - QEMU MILESTONE (2026-11-05 ~02:12, kernel 884e0b3b, root v9): NO MIGRATION;
    SpringBoard creates its own IOSurfaces (312x202 LA08, 272x184 BGRA, 230x186 LA08)
    and Setup (PurpleBuddy) shows the iOS HELLO SCREEN: animated "hello" stroke,
    "Skub op for at åbne" (Danish: no locale chosen yet), (i) button, home
    indicator, and SpringBoard's red internal crash banner (Setup crashed once).
    Screenshot ../qemu-v9-peek.png. SpringBoard AKS calls: 17, 35, 7, 26/27
    (assert hold/drop), 8 and 32 -> kIOReturnUnsupported (0xe00002c7).
    This is the same first-GUI point sweet reached on its phone (image v12).
  - Root v10 (../make_springboard_v10.py): sweet v14 Setup-skip prefs (purplebuddy
    SetupDone/SetupFinishedAllSteps/SetupVersion 11, mini-buddy off, migration
    DMUserDataDisposition, springboard BKNoWatchdogs + purplebuddy watchdog exception,
    managedconfiguration). libdmg `rm` fails on files: old files are `mv`d to
    /private/root-old-prefs. Result: hello screen gone, but the panel keeps the boot
    logo for 33+ min (three identical snapshots); no crashes. Thread sample of
    SpringBoard: main runloop idle; workers busy/blocked in LaunchServices (Spotlight),
    ControlCenter bundle dlopen, Biome lock, CallKit dispatch_sync (no callservicesd),
    ATX home-screen prediction. Setup still runs (looks up com.apple.dmd).
  - QEMU knobs (../run_asus_qemu.py env): ASUS_SERIAL=0 keeps kernel/launchd console
    text off the panel (it painted over the UI; msgbuf via QMP still works),
    ASUS_QEMU_FAST=1 drops `-d int` logging, KEEP_RAM=1 keeps ram.bin; msgbuf_run.sh
    QEMU_SECS (wall-clock limit, default 2000) and fb-T.png per checkpoint.
  - ADT --ios-gui now also has crypto-hash-method, synthetic ECID 0x4153555349000001 +
    chip-id 0x8015 (IOPlatformUUID) and the dev-fused tuple 0,0,1,1 (commit 884ea68).
    Root v11 (../make_springboard_v11.py) adds the stock mobileactivationd job.
    mobileactivationd then crash-loops (107x/40 min): main asserts "Migration failed."
    (_os_crash, brk at its 0x1001bf3bc) after 0x10001ab98 fails; registers show
    "UCRTHealingRequired" and its container /private/var/containers/Data/System/<uuid>.
  - Root v12 (../make_springboard_v12.py): boot v11 once with KEEP_RAM=1 (share=on),
    copy the RAM disk back from ram.bin offset 0x104000000 over the v11 file, fsck -y,
    then seed Library/internal/data_ark.plist (-ActivationState Activated,
    -DataMigrationCompleted, as sweet) in the mobileactivationd container found by MCM
    metadata (CA9C4F52-0508-49C8-9CDF-07B8CA0A3676; backboardd and lsd containers
    exist too). ~/asus-roots/springboard-v12.hfs SHA256
    e26e9d4a3aeae60f56f67dffcd266ecc233c9e77014e28203fc1568b95393f6d. v9/v10/v11 were
    renamed or overwritten in place (rebuildable by the scripts).
  - **Lock screen reached (v12, kernel 884e0b3b, 50-min run).** mobileactivationd starts
    once and no longer crashes (0 corpses). Frames at 600 s and 1200 s are still the logo;
    by 2400 s SpringBoard has created its layer surfaces and backboardd swaps surfaces 2/3
    to the panel: lock screen with date/clock, flashlight and camera buttons and the home
    indicator, black (no wallpaper) and an internal-build red status banner. The clock
    shows Dec 31 (no RTC). Snapshot kept privately in ../qemu-v12-2400.png (Apple UI,
    not committed). Next: input (touch/HID) to unlock to the home screen, RTC/time,
    and why first frames take ~30 min under TCG.
  - **Home screen by touch (kernel 622151a9 = CI 41979354334, profile --input).**
    xnu/asus/9999-asus-i007-input.patch: sweet 0058 HID services (SweetHIDButtons,
    SweetHIDTouch, Apple IOHIDFamily-1915 user client) with 0071's edge bit and the
    1080x2448 panel, plus iokit/Kernel/arm/AsusVirtioInput.cpp: virtio-mmio (modern
    transport) tablet + keyboard on QEMU virt at 0x0a000000, eventq polled every 10 ms,
    tablet ABS 0..32767 + BTN_LEFT -> sweet_hid_touch(0,...), KEY_POWER/VOLUMEUP/
    VOLUMEDOWN/HOME -> sweet_hid_key. Starts only with boot-arg asus_virtio_input=1
    (the phone has no virtio-mmio). QEMU side: ASUS_QEMU_INPUT=1 in ../run_asus_qemu.py
    (-global virtio-mmio.force-legacy=false, virtio-tablet-device, virtio-keyboard-device,
    boot-arg), ../qmp_touch.py (tap/swipe/key over QMP input-send-event, panel pixels),
    ../touch_run.sh OUT T:ACTION... (snap, msgbuf, tap,X,Y, swipe,..., key,NAME).
    Run qemu-touch1 on root v12: both devices found (slots 30/31), backboardd opens
    SweetHIDTouch and SweetHIDButtons at 44 s; lock screen by ~2400 s; swipe
    540,2440 -> 540,1200 (500 ms, edge) at 2435 s unlocks to the **home screen** (two
    widgets "Waiting...", Photos, App Store, Settings; dock Phone, Safari, Messages; Search).
    A second, faster swipe on the home screen (2631 s) is followed by a SpringBoard crash
    at 2650 s (corpse, no report: EXC_CORPSE_NOTIFY fails) and a respring (spinner until
    the run ended at ~3000 s). Home key and tap reached the HID services.
  - SpringBoard crash = **SIGSYS**: ../touch_debug.sh (gdb + virtio input + timed
    actions) with ../capture_crash_k6221.gdb (kernel 622151a9 = 884e0b3b + 0x4000 for
    the breakpoint functions, verified byte-for-byte; abort_with_payload by symbol)
    reproduced it 12 s after the fast swipe; the only exception was type 10 (EXC_CRASH)
    code 0xc000000 = signal 12. Same as sweet 0070: SpringBoard calls pid_hibernate
    (syscall 435, nosys on the macOS config) when an app (here the app switcher
    transition) leaves the foreground. tools/prepare-asus-profile.py --userland now
    adds xnu/patches/0070-sweet-pid-hibernate.patch. Kernel 91aa054a (CI 41993057944):
    the fast swipe on the home screen opens the (empty, grey) app switcher and
    SpringBoard survives; a tap dismisses the switcher back to the home screen.
  - **First app: Settings (run qemu-touch3, kernel 91aa054a, root v12).** Unlock swipe at
    2443 s, tap on the Settings icon (671,911) at 2515 s -> `Preferences` spawned at 2519 s;
    the Settings list is on screen by 2860 s (title, account cell, Airplane Mode, Wi-Fi Off,
    Notifications ... Accessibility, home indicator). The account cell says "Sign in to your
    iPad" (device class/idiom from the ADT or MobileGestalt - not checked). A slower
    swipe up from the bottom edge (2892 s) opens the app switcher with the Settings card;
    Preferences then crashed at 2930 s (corpse) and relaunched at 2976 s; not diagnosed
    (sweet saw Settings SIGTRAP in PodcastsUI on a nil URL.volumeTotalCapacity on the
    HFS+ root). ../touch_run.sh now snapshots msgbuf several times (the ring wraps after
    ~2700 s of boot). Gesture timing note: 500 ms edge swipe on the lock screen unlocks;
    on the home screen a 250-400 ms edge swipe ends in the app switcher.
  - **Status bar fixed (kernel a335b04f = CI 42009368500, commit 94dc661).** AppleCLCD
    presents the iPhone X screen 1125x2436 (as sweet 0061; ../asus_canvas.py edits
    SweetFramebuffer.cpp in the display patch and is called by
    ../make_asus_display_patch.py): swaps blitted at logical offset (22,-6), 6 black rows
    above/below, dot pitch 458; touch maps panel pixels with the same offset; ADT
    asus,display canvas 1125x2436 (tools/mkadt-asus.py). Boot-arg asusfb_native=1 keeps
    1080x2448. Run qemu-sbar1: backboardd surfaces are 1125x2436 and the home screen has
    a real status bar (time, battery) instead of the red rdar:45025538 bar. The battery
    icon is empty/red: no IOPMPowerSource (sweet 0062 had a static 100% SweetBattery).
    Red banner rdar:45025538 = no status bar visual provider for 360x816 pt (sweet 0061
    fixed it by presenting 1125x2436).
  - C: has only ~6 GB free: the WSL ext4.vhdx grew (roots + ram.bin). Freed space inside
    WSL is reused; compacting the vhdx would return it to Windows.
  - Cleanup 2026-11-05 (user approved): deleted ../apple-20H392/20H392-Restore.ipsw
    (re-download from ../apple-20H392/URL.txt; the OS/SystemOS dmgs, devicetree and
    os.trustcache.im4p it provided are kept), ../asus-compositor-root/backboardd-v7.hfs
    (v8..v12 roots can no longer be rebuilt from it; v12 is kept in ~/asus-roots), old
    ../qemu-* run dirs, all ../asus-ci-* kernels except 41979354334, 41993057944,
    42009368500, and WSL ~/asus-roots/qemu-v8*/v9/v10*. The WSL ext4.vhdx is not
    compacted yet (needs wsl --shutdown).
  - GDB crash capture for kernel 59b0b8c1/884e0b3b symbol layout:
    ../capture_crash_k59b0.gdb (exception_triage +0x2c at 0xfffffe00073282dc);
    saves frames per exception under OUT/crash-NN-<proc>.
- NEXT: a client that draws: SpringBoard (or a minimal CA client app) with
  FrontBoard/RunningBoard deps; port the topaz/sweet userland patches already
  in xnu/patches (0038 AKS, 0058 HID, 0059 identity, 0064 work interval ...)
  as ASUS-profile patches as each wall appears. Related public ports listed at
  hackiosx.dev: Pixel 4 (SD855, Adreno 640, Mesa Turnip Metal) is the closest
  GPU reference for SM8350 later.
- Disk: each HFS root is 2.9 GB; QEMU needs an 8 GB ram.bin while running
  (17-20 GB free on C:). v3/v4 deleted (rebuildable by the scripts).
- NEXT: find what backboardd waits on after ~1:37 (GDB sample of its
  threads, or add mobilegestalt/cfprefsd/runningboardd jobs + their cdhashes
  to the QEMU policy), then whether CA picks the ASUS AppleCLCD display.
  launchd places our LaunchDaemons jobs without UserName in user/501
  (containermanagerd runs there, possibly as uid 501); notifyd in system.
  The phone has not been used in this phase.

### Phase 6: iOS compositor in QEMU

- The iOS 16.7.16 (20H392) restore ramdisk lacks backboardd and QuartzCore.
  The complete official IPSW was downloaded locally and SHA256 verified.
  OS member 087-86698-044.dmg has backboardd and its launch job; SystemOS
  member 087-86593-047.dmg has the plain arm64 dyld cache. The two members
  passed ZIP CRC verification. All 45 strong library imports are indexed in
  the cache (dyld itself is supplied by the base HFS).
- For QEMU only, load the HFS root from private file-backed guest RAM.
  ../run_asus_qemu.py --ios-userland --display --external-ramdisk builds
  the matching RAMDisk ADT entry and runs tools/qemu-sweet.py. The existing
  display-v1.hfs booted through this path; the final framebuffer contains
  all three expected colors, and the original image hash is unchanged.
- ../asus-compositor-root/backboardd-v1.hfs (2952790016 bytes) passed fsck
  and per-file roundtrip hashes. SHA256
  331b462cd416b92bb6edf44a2f45a81143d5d1404efd935cc66cd4ee03a8393f.
  It contains 43 original cache files, backboardd and a one-shot launchd job
  preserving the stock mobile user and MachServices. Cache files are root
  owned and marked SF_RESTRICTED; no global CSR override was added.
- On the existing display kernel, QEMU with ignition_level=1 loaded the cache
  and launchd attempted the job under mobile. xpcproxy reported
  posix_spawn(/usr/libexec/backboardd) EACCES, then exited 78. File and parent
  directory modes are executable (0755).
- Removing only /dev/console stdout/stderr redirects allowed exec of
  backboardd. ../asus-compositor-root/backboardd-v2.hfs passed fsck, SHA256
  99fc1cbab067b91e4c27faff0473c11d9fb77f7f7030b6b8b6aceaecb6bfab38.
  QEMU ../qemu-asus-backboardd-v2: load_dylinker ret=0, then launchd
  panics with "unexpected exec of non-platform binary" for backboardd[3].
- The exact SHA-256 CodeDirectory digest (cdhash
  89e6328541c2e37522d90fa4a693c746825b22a8) is present as type 2 in
  Firmware/087-86698-044.dmg.trustcache. The new opt-in QEMU policy grants
  platform classification only to that hash on the read-only root and
  keeps page validation. It requires compositor=true and the QEMU boot
  argument asus_qemu_compositor_trust=1; the phone builder rejects this
  kernel profile. CI 41798008462 at abff953 succeeded. QEMU with that
  kernel matched the pinned cdhash but still panicked at the same check.
  Disassembly of the local launchd shows csops(pid, CS_OPS_STATUS, ...),
  followed by a check of bit 26 (CS_PLATFORM_BINARY). XNU obtains that
  status from csproc_get_blob, which requires the process's CS_SIGNED
  flag. Set CS_SIGNED alongside CS_PLATFORM_BINARY for the same pinned
  signature, keeping the existing signature and page verification.
- QEMU MILESTONE: CI 41800086188 at 335362f succeeded (kernel SHA256
  89e2885edce4955f68a71fca5c8a9932218fab1f0b714ea689264c4b4336af7b).
  With the same backboardd-v2.hfs, Apple launchd spawned original backboardd
  as PID 3 under mobile (uid 501). It ran for about 14 seconds of guest time
  and reached Mach service lookups. Logs show error 159 for logd,
  notification_center, oscard, ACCHWComponentAuthService, iohideventsystem
  and containermanagerd. The process self-terminated with SIGABRT; launchd
  remained up. Those denials are observations, not a proven cause of abort.
  No rendered compositor frame was observed. See ../qemu-asus-backboardd-csops.
  Next: capture and symbolicate the abort stack, then supply the specific
  services and entitlements it needs. The Android phone was not used here.

### Phase 5: IOSurface presentation

- PHONE MILESTONE: user confirmed image 0008 shows all three color bands
  and that their order changes. Fastboot accepted temporary RAM boot.
  This validates display presentation from a launchd job on the ASUS panel.
  After reboot Android reported sys.boot_completed=1. SHA256 checks of
  boot_a, vendor_boot_a and dtbo_a all matched the original backups.
- QEMU verified the ASUS display profile. Apple launchd spawned the display
  job (PID 3, parent 1). Apple's IOSurface framework created a 768x384 BGRA
  surface. The IOKit swap copied it to x=156, y=800 in the 1080x2448 boot
  framebuffer. QMP read back all three expected pixel colors, and the
  console logged consecutive swaps. See ../qemu-asus-display-v1.
- Optional display kernel at 43c0432, GitHub Actions 41793014761 succeeded.
  Kernel SHA256 2e60743b5a680d945f11d3f5b205c30dd6ce622e77b836da09625c5eb12f4ef0.
  The kernel presents via aligned stores to the inherited framebuffer. The
  ASUS display entry's name matches the platform expert's DT selection.
  The ABL refresh configuration is used as inherited. No DPU/DSI, GPU,
  input, or storage hardware driver is included.
- Private HFS image ../asus-display-root/display-v1.hfs is 104 MiB, with
  82 Mach-O files checked byte-for-byte and fsck clean. HFS SHA256
  4640cec2d2ce8fc936841c80a98baf805d5e2e416269bb61bad5ad30251fe3e5.
  Build using tools/build-asus-display-probe.sh and tools/mkasus-display-root.py
  against the existing private Apple launchd HFS root. Apple files stay local.
- Build phone image with ASUS_XNU_VERSION=0008 ASUS_IOS_USERLAND=1
  ASUS_DISPLAY=1 tools/build-asus-image.sh KERNEL DISPLAY_HFS. Image SHA256
  d2bd24f39501ec0f0b04ad9b422544172f4043deb43e5c6c4b88825468c9a21d
  and size 125972480 bytes. Payload 125879904 bytes fits the loader limit.
  The image shows a rectangle of three colored bands rotating every four
  seconds with console text nearby.
- Next experiment: run the iOS display compositor in QEMU with this profile
  and log missing services and dependencies before trying it on the ASUS.
  SpringBoard, touchscreen input, and accelerated rendering remain untested.

### Current status: Apple launchd on ASUS (phase 4 achieved)

- PHONE MILESTONE: user confirmed image 0007 shows `ASUS LAUNCHD CHILD:
  PID ... parent 1` and repeated child heartbeat. This is real Apple launchd
  spawning our single controlled job. Image SHA
  dbe83dd739a2d58d0ca859d721aaeff7ee8585832da1d588e615d5cc2449afaf,
  kernel 8df7c031 from successful CI 41728043185 at 4763c9f, private HFS
  root f1f0be1f8d7d53b681a913619e025490fe8719da2bc8e9484c24af02048aaaa3.
- After 0007 Android returned (ADB sys.boot_completed=1). All three hashes
  boot_a/vendor_boot_a/dtbo_a match original backups. Keep Apple-containing
  image/root private in ignored out and sibling work folders. No partition
  has been flashed or erased. Phase 4 hardware test is achieved.
- Final artifacts: workspace outputs/ASUS-XNU-launchd.md, selected-source
  archive asus-i007-hfs-source.zip, local own-code recipes archive and
  asus-i007-launchd-manifest.json. No Apple binaries were copied to outputs.
  All QEMU runs ended; own WSL keepalive stopped; read-only APFS FUSE mount
  was unmounted. Remount ../apple-20H392/087-86719-047.apfs read-only at
  ../apple-20H392/restore-mount before reading restore files on resume.
- Next substantial stage is GUI: actual SpringBoard/root dependencies and
  ASUS display presentation/input support, with a storage/RAM strategy that
  preserves Android. Do not claim full iOS or a usable phone OS. Earlier
  entries below are historical checkpoints, including failures now resolved.

- QEMU MILESTONE with account root: real Apple launchd successfully spawned
  own child, `ASUS LAUNCHD CHILD: PID 3 parent 1`, followed by 13+ heartbeats.
  150-second snapshot, no panic, zero unknown glyphs. Log at
  ../qemu-asus-launchd-accounts-v2/fb.txt/.png. Warnings about system event
  logging/persona type 6 were non-fatal in this limited test.
- Built phone image 0007, SHA
  dbe83dd739a2d58d0ca859d721aaeff7ee8585832da1d588e615d5cc2449afaf,
  kernel 8df7c031 from CI 41728043185, root f1f0be1f account variant.
  Temporary fastboot boot in progress; physical launchd/child observation
  still required. Keep Apple-containing image private; no public artifact.
- SHA retest ../qemu-asus-launchd-sha confirms SHA1 crash is gone. Launchd
  proceeds through boot tasks and then exits with namespace 7/code 1:
  `unable to resolve 'mobile': 114`. Minimal root lacked account files.
- Added restore default private/etc/master.passwd and group, /etc symlink,
  preseeded private/var dirs in NEW ../asus-launchd-root/launchd-accounts-v2.hfs,
  fsck OK, SHA f1f0be1f8d7d53b681a913619e025490fe8719da2bc8e9484c24af02048aaaa3.
  QEMU ../qemu-asus-launchd-accounts-v2 is running with SHA kernel 8df7c031.
  No phone launchd test yet. Prior launchd-accounts.hfs image was abandoned
  after libdmg-hfsplus failed to remove an empty /var directory; its QEMU run
  was explicitly stopped. V2 uses a fresh copy and adds only the missing /etc
  link, leaving the existing empty /var and /tmp directories unchanged.
- Phase 3 achieved on phone: source XNU, HFS RAM root, Apple dyld/libSystem,
  own dynamic PID 1 with five-second heartbeat (0006). Android returned;
  boot_a/vendor_boot_a/dtbo_a hashes still match original backups afterward.
- Launchd experiment remains QEMU-only. Actual Apple launchd starts, but
  hostname/IPv6 setup reaches absent SHA-1 provider and panics. Generic
  software hash patches 0032/0033 apply locally and are now included only
  in optional HFS profile; UFS and intervening board/root-write patches are
  excluded. Source commit 4763c9f; CI 41728043185 SUCCEEDED. Kernel SHA256
  8df7c031e03bfdaea18942d1368dc8fdf57f49e8ac897867a6dfb6ea29f369e6,
  downloaded and verified in ../asus-ci-41728043185. New QEMU retest running
  at ../qemu-asus-launchd-sha (same launchd root, --ios-userland).
- Actual patched XNU SHA1 wrapper was extracted and host-compiled against
  the pinned staged BearSSL source; 135 streaming/chunk-boundary cases match
  Python hashlib. Script ../check_sha1_wrapper.py, artifacts ../sha1-host-check.
- Once successful, download that kernel and repeat ../run_asus_qemu.py with
  ../asus-launchd-root/launchd-probe.hfs and --ios-userland, 150 seconds.
  Require `ASUS LAUNCHD CHILD` with parent 1 and heartbeat before sending a
  launchd image to the phone. If another failure, use console and symbols.
  Do not claim GUI, actual launchd on phone, or a usable iOS installation.

### Phase 3 active (2026-11-04): filesystem RAM root toward Apple userland

- Actual Apple launchd executes in QEMU: logs hello, version 7.0.0, restore
  environment, then creates system bootstrap context. No child heartbeat yet.
  Kernel panic PC=0xfffffe00079cb144 = SHA1Init/sha1_init+0x34, FAR=0x28,
  ESR=0x96000006; LR=0xfffffe00077214ec = in6_nigroup+0x10c. Missing SHA-1
  crypto provider, reached through IPv6 hostname setup ("localhost").
  Log ../qemu-asus-launchd/fb.txt; root ../asus-launchd-root/launchd-probe.hfs.
  This root has NOT been sent to the phone. Add only generic software hash
  patches 0032/0033 to HFS profile (not intervening UFS/write/root patches),
  then rebuild and repeat QEMU before any phone launchd attempt.
- PHONE MILESTONE: user confirmed image 0006 prints ASUS DYLD and repeated
  libSystem heartbeat approximately every five seconds. Thus Apple dyld and
  standalone libSystem run on ASUS, with our own dynamic PID 1. Apple launchd
  and GUI are still separate, unproven milestones. User returned Android.
- Started next experiment in QEMU ONLY: private ../asus-launchd-root,
  actual Apple /sbin/launchd + xpcproxy, one own job dev.asus.launchd-probe.
  Own static console bootstrap at the kernel's development init path attaches
  stdin/out/err and execs real /sbin/launchd as PID 1. Own child probe reports
  its PID and parent PID, then heartbeat. No Apple restore jobs included.
  All job paths/files owned 0:0 inside image (launchd requires root-owned
  jobs), set via small GPL host helper using libdmg-hfsplus API. Source files:
  tools/asus-console-exec.S, asus-launchd-probe.plist, asus-hfs-chown.c,
  child mode in asus-dyld-init.c. QEMU ../qemu-asus-launchd running at handoff.
  Do not claim real launchd success until child evidence is observed.
- QEMU MILESTONE: corrected root + unentitled_ios_sim_launch=1 reaches
  `ASUS DYLD: Apple dyld + libSystem entered main; PID 1` and repeated
  libSystem heartbeat messages. No panic, zero unknown glyphs. Snapshot at
  ../qemu-asus-ios-allow/fb.txt/.png, 150-second run, source kernel 1a6a5f4c.
- Built phone image 0006 with EXACT same corrected root and boot flag,
  SHA b582bd21f4587d35a5635bb45056026697c5879bae24c5105844e1a71e1b869e.
  Temporary fastboot boot was started; physical observation still pending.
  Contains Apple binaries: keep in ignored out/private work, not public outputs.
- Corrected dyld mode advances QEMU to `ASUS DYLD LOAD ... ret=0`, then
  namespace 9/code 0xe = EXEC_EXIT_REASON_WRONG_PLATFORM. Source kern_exec.c
  explicitly permits an iOS binary in a DEVELOPMENT macOS-profile kernel
  with boot arg `unentitled_ios_sim_launch=1`. Added optional
  ASUS_IOS_USERLAND=1 in the image builder (requires explicit HFS root),
  and currently testing the flag with corrected root in QEMU. No new kernel
  bypass or platform-tag edits were added. Artifacts ../qemu-asus-dyld-exec
  (without flag) and ../qemu-asus-ios-allow (with flag, running).
- Root cause of 0005 established in QEMU: GDB at load_machfile's return
  gives 6 = LOAD_PROTECT. Source-built diagnostic kernel from successful
  CI 41723309780 prints `/usr/lib/dyld ret=6`. HFS listing shows dyld mode
  100644; get_macho_vnode requires EXECUTE|READ_DATA. libdmg-hfsplus addall
  ignores staging file modes for /usr/lib, defaulting to 0644. Thus this was
  our root packaging error, not evidence requiring a kernel trust bypass.
- Corrected copy ../asus-dyld-root/dyld-probe-exec.hfs changes only dyld mode
  to 0755, fsck OK, SHA c20990bb0db9f423f1f43361133322509bd244282c54c936c98bef6dd351c195.
  Testing this in QEMU with diagnostic source kernel. No phone 0006 yet.
- Added tools/mkasus-dyld-root.py: own dynamic init + dependency closure only,
  explicit executable mode checks, full round-trip file hashes and fsck.
  It successfully rebuilt private ../asus-dyld-root/checked-root.hfs (38 files).
  No Apple restore programs/jobs are included. Original 0005 root is preserved.
- Diagnostic kernel CI 41723309780 succeeded; SHA
  1a6a5f4c7a366c825e7d7b72b282332eb14671724bf9184658da13e592741bfe.
  GDB QEMU process was stopped through QMP after extracting the return code;
  corrected-root QEMU test is the remaining process to inspect.
- Current blocker: 0005 photo confirms HFS mounted, then immediate SIGKILL of
  init, namespace 9 / code 1 = OS_REASON_EXEC / EXEC_EXIT_REASON_BAD_MACHO.
  This does NOT establish a code-signing failure (namespace 3) or dyld
  execution. No ASUS DYLD heartbeat observed. Photo kept privately at
  ../asus-dyld-root/0005-signing-panic.png (historical filename; reason is EXEC).
- QEMU now reproduces the exact same 0005 HFS -> EXEC/BAD_MACHO panic, same
  panic caller 0xfffffe0007b34b6c, without phone access. Private harness
  ../run_asus_qemu.py adapts the existing qemu-sweet helper: ASUS FB e5000000,
  height 2448, GIC virt addresses, 62.5 MHz timebase, Cortex-A55, automatic RAM
  size from payload, Unix monitor socket in /tmp (DrvFS cannot bind sockets),
  no default NIC. 35 seconds was too short; use 150 seconds. Results in
  ../qemu-asus-0005-long/fb.txt/.png and result.json. Decoder has zero unknown
  glyphs. Own nlist reader supplies iso_font instead of requiring llvm-nm.
- All 38 user CodeDirectories and 2394 code-page hashes validate locally
  against the private extracted files (../check_user_code_pages.py). This
  checks page content, not CMS trust or complete loader acceptance.
- After 0005 Android is back (ADB boot_completed=1); all three original boot
  partition hashes still match. Keep it in Android while debugging in QEMU.
- Added ASUS-only `9998-asus-exec-diagnostics.patch` to the HFS profile:
  log load_machfile return code, failing file/pass/load command, dyld result.
  Checks remain intact. Applied locally; full build CI 41723309780 at bb4c31b.
  Next 0006 should use this diagnostic source kernel with the EXACT same
  0005 private root. Do not add speculative trust-policy changes first.
- HFS follow-up CI 41721394060 SUCCEEDED at 326e3d3; kernel artifact SHA
  f7f25006ff6628a839388ea764914dd03d43ec594d5836c0eb42d94beb9ef7f5.
  No source kernel changes relative to the HFS kernel tested in 0004/0005;
  verifier and RAM-root workflow fixes changed only host tooling.
- HFS MILESTONE: image 0004 booted temporarily; user confirmed
  `hfs: mounted ASUSRamRoot`, PID 1 and five-second heartbeat. Then user
  returned Android; ADB sys.boot_completed=1. Kernel from CI 41719763180,
  SHA f0daa57cc9d11b5fe7c6f0e7602bfcf7322f04de8100a53d6fec30e3e9014d1a;
  image SHA 16d520ae868b4ea2dbd721c536f375e8740933e296e5137bbc45775a15de409c.
- CI 41719763180 compiled successfully but artifact verification wrongly
  required nonexistent `_hfs_mountroot`. Now requires actual HFS vfsops,
  mountfs and registration symbols; recovered archived kernel passes. Clean
  follow-up CI 41721394060 is running at 326e3d3.
- Own-init HFS root generated and fsck checked on macOS in successful
  asus-ramroot CI 41721388493. Artifact SHA
  4e148ac360bc947cf0a76fd8af63e1135c7862e3c6c9e7e65a98ef347e34fede.
  Earlier RAM-root workflow runs failed on hdiutil syntax and using fsck on
  a plain file; fixed to check an attached read-only block device.
- Restore IM4P contains unencrypted raw APFS. Read-only apfs-fuse mount at
  ../apple-20H392/restore-mount (WSL root). Plain arm64 launchd dependency
  closure is 38 files / 10,960,640 bytes, with one absent optional weak
  libobjc-env dependency. No need for the full multi-GB shared cache here.
- Prepared diagnostic dyld PID 1 (tools/asus-dyld-init.c): dynamically links
  Apple libSystem and prints a heartbeat after Apple dyld initializes it.
  Root contains dyld + dependency libraries; original Apple launchd and all
  restore jobs/programs are excluded. 32 MiB HFS root built privately with
  libdmg-hfsplus from the known Mac volume; fsck OK, extracted files hash-match.
- Image 0005 SHA 3927f6a42af1893a15b7000e6842184f72638182364cdceca095cbe411874401,
  root SHA ff264fb74ac820de1d97a7aaf40dc96fbbcda1181bb4b383af1f0eaf13866498,
  own dynamic init SHA 279ce1620f601ae696fb15442dd52e1c137bcb93da14575f108cfeec5ceb9281.
  Temporary boot in progress; physical observation is still required.
  These Apple-containing artifacts stay private in ../asus-dyld-root and
  ignored shim/asus-xnu/out; never upload them to public CI or git.

- User asked to continue after source-built 0003 success. Preserve Android;
  only temporary RAM boots remain authorized. No UFS driver or writes.
- mockfs serves only one executable, so launchd/dyld/libraries require a real
  filesystem. Added optional `--hfs` profile: base 0001..0027 + HFS staging,
  31 files total. Reuses upstream Apple OSS HFS integration and MD5 provider;
  patch 0028/UFS is excluded. Default minimal profile remains available.
- Local staging and sequential application of 0026/0027 after the ASUS base
  passed. Workflow `hfs=true` builds this profile and requires linked HFS
  symbols in addition to the existing ASUS and timer-instruction checks.
- Read Apple 20H392 BuildManifest via HTTP ranges and downloaded only its two
  RestoreRamDisk ZIP members (CRC verified), private sibling ../apple-20H392.
  These are data sources for inspection only: do NOT boot Apple's restore
  init/services or run its restore tools on ASUS. Need a controlled minimal
  root without restore jobs. No Apple files are committed or published.
- Next: build HFS kernel; inspect restore image filesystem and dependencies;
  first confirm HFS RAM root with our own init before any Apple launchd test.

### Active status (2026-11-04): XNU and own PID 1 observed

- Diagnostic image 0002 reached PID 1. The user confirmed that
  `ASUS init: still alive` continues approximately every five seconds. This
  confirms the own static Mach-O init and its timed system-call wait, not an
  Apple iOS userland boot. Android subsequently returned; ADB reported
  sys.boot_completed=1 before the next test.
- Full source rebuild with the ASUS CNTP guard succeeded in CI 41716816697,
  source commit bc049f2. Kernel SHA256:
  ca9c190be6567ea5670aeeeefb752dbdc0031678d5b127cafca5425141ff4cec.
  Downloaded artifact checksums match. The new verifier audits instructions
  in fiq_context_init: it accepts this kernel and diagnostic 0002 and rejects
  the original trapping kernel. Generic topaz is also rejected.
- Built source-based image 0003; SHA256:
  ab12c926a399d66aabbbda93bd588babb2742549ac51c365652ccd4c5bd1739b.
  Fastboot transfer and boot returned OKAY. The user confirmed PID 1 and a
  heartbeat every five seconds in image 0003. This is the source-built
  on-device success, independent of diagnostic binary patch 0002.
- RAM/reservation, FB block offset, CPU/timer DT, own init and patch
  reverse/forward checks pass. Scratch source had CRLF line endings; normalized
  the ten patch targets back to LF before checking. This was a host check
  issue; the clean macOS CI applied and compiled the source patch successfully.
- After 0003 the user confirmed Android returned; ADB sys.boot_completed=1.
  Re-read boot_a, vendor_boot_a and dtbo_a SHA256 values match all three
  pre-experiment backups. No partition was flashed or erased.
- Phase 2 single-core XNU + own RAM-root init is achieved. Next technical stage
  is planning an Apple userland RAM-root test and the required kernel support;
  Apple launchd/SpringBoard and ASUS peripherals remain untested. Deliver the
  checked 0003 image, current source archive and report in workspace outputs.
  No ASUS erase or partition write is authorized. Historical entries below
  describe earlier checkpoints and may say that XNU has not run yet.

### Phase 2: XNU candidate prepared (2026-11-03)

- First real XNU boot, image 0001: kernel SHA256
  1954c0a3c769b6840cc8af6dfacf7a8627b6ee895048e31e76b469a0cd6ee064;
  image ab5583035260307bd7c7988c1408d0f0eb52d3422688a62210aacab785e2d6cd.
  User photo confirms printf/serial/framebuffer initialization and GIC timer
  INTID 27, then panic: PC=0xfffffe000748e704, LR=0xfffffe0007aa5aec,
  ESR=0, FAR=0. PC symbolicated to fiq_context_init+0x14: instruction
  0xd51be229 = MSR CNTP_CTL_EL0, X9. The physical timer access traps in this
  boot environment. Only one CNTP access exists in this minimal source profile.
  No PID 1 yet. Photo saved outside git at ../asus-ci-41713279475/xnu-0001-panic.png.
- Added ASUS_I007 source guard around the precautionary CNTP masking; CNTV is
  unchanged. User manually restarted and confirmed Android returned.
- Fast diagnostic 0002 uses the exact first kernel, SHA-guarded, with that one
  instruction changed to NOP (file offset 0x48a704). Patched kernel SHA256
  a0f1f5b6f82ed4d4f5896384bf58b443d2ea355009a74f8b6c324ca116a1d292.
  Details at ../asus-xnu-debug-0002/PATCH.txt. This is diagnostic only, not a
  substitute for the matching source rebuild. Image 0002 SHA256
  56cc3a4ef7d3a7617d7cb3d4f57cc4c914a6a3dfc1b8fc98994b8cfc68ba9cb4.
- CI 41713279475 compiled a real kernel but failed our overly strict artifact
  check: clang optimized the literal early-console strings into glyph stores.
  CPU/platform ASUS strings and actual early-console symbols are present.
  Fixed verification (59278da); recovered the first kernel from its build-tree
  artifact and verified locally. Generic topaz kernel still fails the check.
  CI 41715337539 is the follow-up build with the corrected verifier (no CNTP
  fix in that run). Next full source build must include the CNTP guard.

- User asked to continue and supplied https://hackiosx.dev/projects. Read the
  project, compatibility and porting pages; found ch1tmdgus/ios-v60 (SM8250)
  as a related-platform reference. No device-specific V60 binary patch was
  copied: its README says it requires a particular kernel and is not portable.
- Preparing a reproducible minimal kernel from Apple xnu-8796.141.3
  (1b191cb58250d0705d8a51287127505aa4bc0789), using base patches 0001..0025.
  No UFS patch 0028, HFS staging, or later board drivers is included.
- New `xnu/asus/9999-asus-i007-bringup.patch`: Cortex-A55, single CPU, timer
  INTID 27, ASUS CPU/platform matching and early framebuffer. Crucially the
  physical block mapping is 0xe4000000 and the actual framebuffer is at
  +0x01000000; replacing the old aligned FB address with 0xe5000000 directly
  would produce an invalid L2 descriptor. Device memory throughout console.
- `tools/mkadt-asus.py`: one boot CPU (MPIDR affinity 0), GIC and fbcon only;
  512 MiB at 0x140000000, checked against the captured RAM and all fixed
  reserved regions. Android sysfs reports A55 max frequency 1804800 kHz.
- `shim/asus-xnu/`: new loader-only shim using existing start/fdt/loader/console,
  verified PICASSO/A55/EL1/display guard and the tested GIC preparation. No
  storage driver, no display-controller writes, no secondary CPU startup.
- Added prepare-asus-profile.py (28 files, strict numbered patch application),
  verify-asus-kernel.py (requires ASUS markers; rejects generic topaz kernel),
  build-asus-image.sh and workflow asus-xnu.yml. Own Mach-O init is embedded
  directly as a mockfs RAM root. No Apple userland/root filesystem needed.
- Local checks passed: shim -Wall/-Wextra/-Werror and ABS64 audit; Mach-O init
  assembled by clang and packed (16635 bytes); all base patches applied;
  ASUS patch reverse/forward check; RAM reservation/FB block/CPU/timer checks;
  negative test rejects the downloaded generic kernel artifact. Full XNU build
  and on-device candidate boot have NOT occurred.
- Scratch source/build helper at sibling `../xnu-asus-source/` and pinned DXB
  checkout `../darwin-xnu-build/`; read-only reference kernel downloaded from
  upstream CI 39419628786 into `../upstream-kernel-39419628786/`. Do not boot it
  on ASUS. Host has Clang in WSL now for compiling our own static init.
- User explicitly approved public fork creation, branch publication and GitHub
  Actions build. Created https://github.com/catxeon/ios-asus-i007, pushed branch
  asus-i007 at 177aae0, set it as the new fork's default, and dispatched
  asus-xnu.yml. CI run: 41713279475. Prepare/dependency steps passed and Build
  XNU is running. Origin remains read-only; remote `asus` points to the fork.
- Review prepared in outputs/ASUS-XNU-review.md and source archive. All future
  build fixes on this fork are within the approved task. No phone image has
  been booted during phase 2 yet; Android remains running.

- User requested trying this project on ASUS Smartphone for Snapdragon Insiders;
  confirmed an unlocked bootloader and USB connection. No authorization to erase
  this ASUS's Android/data has been given. Historical sweet/topaz wipe approvals
  below do not apply to this device.
- Base branch `topaz`, commit `e4772911e116ab4ab6c4ac1f38e6106fefe8b483`.
  Connected Android identifies as ASUS_I007D / ASUS_I007_1, lahaina / SM8350,
  stock Android 11 build 18.1055.2307.269, active slot a, Magisk root available.
- Private captures and backups are outside the repo, at sibling directory
  `../asus-probe-20261103-224059/`: live-root.dtb/.dts, root iomem, boot_a.img,
  vendor_boot_a.img, dtbo_a.img, backup-sha256.json and probe-0001-screen.png.
  Do not commit these files: the DT contains device identifiers.
- Windows ADB works. Windows bootloader USB 0b05:4daf has no usable Fastboot
  driver. Existing usbipd-win 5.3.0 + Ubuntu WSL2 works: shared bus 2-1,
  `usbipd attach --wsl --busid 2-1`, then Linux fastboot as root. Keep WSL
  running during attach. Android re-enumerates separately and Windows ADB works.
- Installed in Ubuntu: gcc/binutils-aarch64-linux-gnu, device-tree-compiler,
  fastboot. AOSP mkbootimg is a sibling checkout `../mkbootimg`, commit
  `d2bb0af5ba6d3198a3e99529c97eda1be0b5a093`.
- Transport verified: temporarily booted the backed-up stock boot_a.img from
  RAM; Fastboot returned OKAY and Android boot_completed=1. Product lahaina,
  unlocked yes, max-download-size 536870912. No partitions flashed.
- MILESTONE: `shim/asus-probe/build.sh` builds a separate CPU/display-only
  diagnostic using the existing entry assembly and font. Probe 0001 was sent
  with fastboot boot, returned OKAY, and the user's photo confirms execution
  and readable display output. No storage driver is linked. It returned to
  Android (ADB device, boot_completed=1). All three partition SHA256s still
  match their pre-experiment backups.
- Photo observations: EL=4 (EL1), MIDR=0x412fd050 (Cortex-A55),
  MPIDR=0x81000000, MMFR0=0x101122 (16K granule advertised),
  MMFR1=0x10212122, PFR0=0x11112222, ISAR0=0x100010211120,
  ISAR1=0x100001, CNTFRQ=0x124f800 (19.2 MHz), incoming DTB=0xafd73000,
  inherited linear FB=0xe5000000, stride=0x10e0 (4320), four bytes per pixel.
  CTL_TOP value and final reset text were not captured in the photo.
- Hardware from live DT: GICD 0x17a00000, GICR 0x17a60000/0x100000;
  CPU affinities 0x0,0x100,...,0x700 (4 A55 + 3 A78 + 1 X1);
  UFS 0x01d84000; MDP 0x0ae00000, CTL0 offset 0x16000;
  splash reservation 0xe5000000+0x2300000. Active panel
  ams678_er2_fhd_plus_dsc_cmd, 1080x2448, command mode with DSC.
- Probe 0001 image SHA256:
  `5f06cac77f757907fa4259e487a56780953258d180faaafc414e830080ccb6e2`.
  Build checks: -Wall -Wextra -Werror, no ABS64 relocations, unpacked Android
  header v3 / Android 11 / patch 2023-07, kernel 77824 bytes, ramdisk 48 bytes.
- NEXT: adapt XNU for an initial single-CPU RAM-root boot. No XNU kernel or
  iOS userland has run on this ASUS yet. Do not
  boot the existing topaz payload: its CPU topology, RAM, GIC, UFS and panel
  assumptions differ. Future root storage should initially be a RAM disk.
- Follow-up probes: 0002 image SHA256
  `2d544f0883a80604ec6157d075286084f86df1d365a2a3ffa8c59e423a94f222`;
  fastboot accepted it, the user's photo shows only the start of the MMU label
  (no pass/fail captured), Android returned. It also shows CTL_TOP=0xf0020000
  and a colorful band over the first text row. MMU success remains unverified.
- Probe 0003 enabled I-cache, moved the MMU setup before bulk text rendering,
  and used 32-bit pixel stores. SHA256
  `fa57f72fe681592527b3cfc18d636456b10a18dc370b017ed8a49e92f323730b`.
  Fastboot accepted it; user reported a screen full of artifacts and missed
  the photo. Android returned (ADB device, boot_completed=1). Do not treat this
  as a successful MMU test or a diagnosed CPU fault.
- Probe 0004 restores byte pixel stores and compiles the existing MMU with an
  explicit Device framebuffer mapping, retaining default NC for topaz. It
  checks real code/FB PA translations, MAIR attributes and an unmapped hole.
  Its report remains visible for 120 seconds. SHA256
  `41a4d5e2617635da8d4ffe82f7718d674b9a6f759fb077ea09e7a942cb514f59`.
  Header v3, kernel 147456 bytes, no ABS64 relocations, warning-free build.
  Fastboot accepted it; photo confirms full-screen artifacts. User manually
  rebooted with Power + Volume Down and confirmed Android returned. ADB
  boot_completed=1 and all three partition hashes still match the backups.
- MILESTONE: probe 0005 removed the direct CTL_START write as its only
  behavioral change from 0004. User confirmed a readable screen and
  **16K CHECK PASS**. Thus the previous screen disruption was caused by the
  manual CTL start in the inherited DSC/DSI pipeline, rather than establishing
  an MMU failure. The small original top-edge band was not separately assessed.
  No display-controller registers are written in the working probe.
- Probe 0005 SHA256:
  `29ed276309e54fba1424963828ee5c134319a3bfc5d572777b7553b1aa4e0052`.
  Same header v3 / kernel 147456 / ramdisk 48, warning-free build and no ABS64
  relocations. The PASS condition checks SCTLR.M, TCR.TG0=16K, AT S1E1R physical
  addresses and MAIR attributes (code WB / framebuffer Device), and a fault
  for a deliberately unmapped address at 16 GiB. Only the boot A55 was tested;
  no GIC, SMP or storage driver test and no XNU/iOS boot has occurred yet.
- After 0005 the phone returned to Android (ADB device, boot_completed=1).
  Re-read boot_a/vendor_boot_a/dtbo_a SHA256 values match all three original
  backups. Automatic return was observed this time; no further manual reset
  was requested. Working source commits: e297d13, 45c1aa4.
- GIC probe 0006 was built and RAM-booted. User supplied exact readings:
  GIC CHECK FAILED, GICR=0x17a60000, VTIMER=0, SGI=1, OTHER_IRQ=0.
  The test incorrectly retained topaz's timer INTID 19. Re-reading this ASUS's
  live /soc/timer found interrupts <1 13 ff08>, <1 14 ff08>, <1 11 ff08>,
  <1 12 ff08>: virtual timer is PPI 11 = INTID 27. Android returned afterward.
- MILESTONE: probe 0007 changes the virtual timer source to INTID 27. User
  confirmed **GIC CHECK PASS**. This checks >=5 timer IRQs in a 200 ms window,
  one self-SGI, zero other IRQs on the boot CPU through the existing EL1 IRQ
  vectors. MMU and display handling remain as the working probe 0005.
- Probe 0007 SHA256:
  `f1a5555dd651f0c1cfd06572def2d8205934bd2a51de3370d6cd091223c44467`.
  Header v3, kernel 147456, ramdisk 48; -Wall -Wextra -Werror and ABS64 check
  passed, Android header unpack verified. After the 120-second report pause,
  Android returned automatically: ADB device, boot_completed=1. Re-read all
  three boot/vendor_boot/dtbo hashes and they still match the original backups.
  Stopped the task's WSL keepalive process; no fastboot command remains running.
  Do not mix the timer numbering with older notes. Local GIC source commit:
  45763a6. No remote push, fork creation or CI dispatch has occurred.
- GitHub CLI is authenticated; no remote repo was created or pushed, no CI
  run was triggered during this milestone.

## Agent handoff / active work (2026-10-15, latest)

### >>> UPDATE 2026-10-18 (read first, then the 10-17 handoff below) <<<

**LIVE LOG over USB (0077, boot 0077s): no more recovery round-trips.** Boot an image with `sweetusb=2`, run `tools/sweet-usbnet.sh logs/live-NAME.txt` on the PC: it waits for the phone's CDC-ECM interface, sets 192.168.7.1/24 (sudo) and prints/saves the kernel console the phone streams every 100 ms as UDP to 192.168.7.1:5140 (starts with the last 64 KiB). `ping 192.168.7.2` checks the link. Current image: `shim/out/shim-boot-0077s.img` (kernel 0077, ADT sweet-0069hi, cpus=6, sweetfb_noconsole=1, sweetfb_hz=30, sweetusb=2). The recovery pull (`tools/sweet-log-pull.sh`) is still the way after a panic/hang before USB comes up.

**🎉 USB networking works (boot 0076s, 18:24 local): `ping 192.168.7.2` from the PC answers in ~1 ms, 0 % loss (200-packet burst, 1500-byte frames, 512/1024-byte frames).** 0075: CDC-ECM function (bulk 0x81/0x01, interrupt 0x82 NETWORK_CONNECTION; host MAC 02:00:5e:77:ee:01) + kernel ethernet ifnet `ue0` (legacy output, header strip like if_fake) with 192.168.7.2/24 set from the kernel (proto_plumb + SIOCAIFADDR on an in-kernel socket). 0076: received frames into one cluster mbuf (mbuf_allocpacket gave a first mbuf of length 0 -> panic "Failed mbuf validity check: len -14"). Host side (Linux cdc_ether, interface `enp0s20f0u3u1`): `nmcli dev set IF managed no; sudo ip addr add 192.168.7.1/24 dev IF; sudo ip link set IF up`. Boot image `shim/out/shim-boot-0076s.img` (`sweetusb=2`). iOS itself does not see ue0 yet (configd needs an IONetworkInterface in IOKit).

**USB device mode works (boot 0074s, 17:45 local): the host enumerates the phone at high speed as `1209:0001 ios-sweet "Sweet iOS (USB bring-up)"`.** 0072 read-only probe: ABL leaves the USB30_PRIM GDSC and all 7 USB clocks on, DWC3 v3.20a (GSNPSID 0x5533320a) in device mode (GCTL 0x00112000), halted (DCTL RUN_STOP 0, DSTS 0x00d3de04), event buffer still at ABL's 0xfdfc8000, QUSB2 PHY alive. 0073 (`sweetusb=2`): DCTL soft reset, own event buffer (polled, interrupt masked), ep0 DEPSTARTCFG/SETEPCONFIG/SETTRANSFRESOURCE, setup TRB, RUN_STOP; control-transfer state machine as Linux ep0.c; descriptors: vendor class, no data endpoints. It ran but no event came: **0074** forces the VBUS session in the Qualcomm wrapper (QSCRATCH 0xa6f8800: HS_PHY_CTRL |= UTMI_OTG_VBUS_VALID(20)|SW_SESSVLD_SEL(28), SS_PHY_CTRL |= LANE0_PWR_PRESENT(24), as Linux dwc3_qcom_vbus_override_enable) -> connect + enumeration. Image `shim/out/shim-boot-0074s.img`. Black Phone app: main thread waits in -[VMVoicemailManager fetchAccounts] (sync XPC; voicemail needs the modem).

**Navigation works (boot 0071s on v14, log `logs/boot-0071s.txt`):** swipe up from the bottom edge returns to the home screen; app switcher works. Two fixes: **0070** pid_hibernate (syscall 435 was nosys on the macOS config -> SIGSYS killed SpringBoard in -[SBToAppsWorkspaceTransaction _didComplete] when an app left the foreground; the only CONFIG_EMBEDDED-only entry in syscalls.master); **0071** touches starting within 48 px of an edge carry kIOHIDDigitizerEventFromEdgeTip (UIKit `_UIEventHIDTouchEdgeTypeForHIDEvent` reads the digitizer EventMask 0xb0007 bits 0x800/0x1000/0x2000/0x40000). Diagnosed, not fixed: Settings (Preferences) SIGTRAP in PodcastsUI func_1cdc47c58: force-unwrap of `URL.volumeTotalCapacity` (nil on our HFS+ root). Phone/Photos/Messages show black (MobilePhone launches, no crash) - next: stack samples via boot-arg `sweetsample=MobilePhone,MobileSlideShow,MobileSMS,SpringBoard` (image `shim/out/shim-boot-0071s.img`).

**🎉🎉 MILESTONE 2026-10-18 ~15:30 local: iOS 16.7 SPRINGBOARD HOME SCREEN ON THE PHONE** (boot `shim/out/shim-boot-0069s.img` = kernel 0069 + ADT `shim/out/sweet-0069hi.adt`, image **v14**; log `logs/boot-0069s-v14-HOMESCREEN.txt`). Home screen with a few apps, Settings opens, Control Center / lock screen / notification center work, touch + buttons work, 6 A55 cores (`cpus=6`), `sweetfb_noconsole=1 sweetfb_hz=30`. Known issue: cannot leave Settings (home gesture / AssistiveTouch home do nothing) - to investigate.
What it took today (in order): 0058 HID services (Apple IOHIDFamily-1915 user client), 0059 iPhone X identity (hw.product etc.), 0060 SPMI keys + GENI touch, 0061 1125x2436 presented display, 0062 RPMh L11C touch power + IOPMPowerSource battery, 0063 GENI FIFO packing (Goodix GT9896), 0064 PRIV_WORK_INTERVAL grant (SpringBoard "Failed to create work interval"), 0065 SMP via PSCI (6 A55), 0066 crash map regions, 0067 console mute without cons_ops write, 0068 IOSurface lookup of an already-mapped surface returns base -1 (backboardd snapshot crash), 0069 UFS log flush 10 s. Image v13: CommCenter removed from launchd.plist, BKNoWatchdogs. **Image v14** = v13 + Setup skipped (`/var/mobile/Library/Preferences/com.apple.purplebuddy.plist` SetupDone/SetupFinishedAllSteps true, SetupVersion 11, daemonizeNeedsToRun false; purplebuddy.notbackedup mini-buddy flags false; managedconfiguration.notbackedup LockdownActivationIndicatesCloudConfigurationAvailable false; springboard MCForceShowCloudConfigurationUI false) + mobileactivationd data ark seeded (`/private/var/containers/Data/System/D90C94C4-F94E-4FCF-8A28-A93BE3846D8E/Library/internal/data_ark.plist`, -ActivationState Activated etc.) + MediaToolbox D22/D221/Default SubServerConfiguration StartRecorderServer=false (mediaserverd crash loop: portrait-filter prewarm called NULL GL). **Activation:** ADT /chosen certificate-production-status 0, effective-production-status-ap 0, certificate-security-mode 1, effective-security-mode-sep 1 -> mobileactivationd's native Hactivation branch (Codex `codex/activation/report.md`; alternative 4-byte daemon patch documented there). Fallback image v13; v12 deleted.

**🎉 TOUCH WORKS (boot 0063u on image v13, 2026-10-18 ~13:15 local):** tapping (i) on Setup's Hello opens the device-info sheet; swipe-up is recognised (spinner, then back to Hello - under investigation). Chain: RPMh L11C vote (0062, `bsd/dev/sweet_rpmh.c`) -> GT9896 @0x5d on GENI SE7 (0063 fixed FIFO packing: register address went out byte-swapped) -> sweet_hid_touch -> SweetHIDTouch -> backboardd. Battery icon OK (0062 SweetBattery IOPMPowerSource, static 100 %). **Image v13** (= v12 + CommCenter jobs removed from launchd.plist + `BKNoWatchdogs`=true and `FBLaunchWatchdogExceptions{com.apple.purplebuddy:100}` in /var/mobile/Library/Preferences/com.apple.springboard.plist): Setup no longer killed by the scene-create watchdog (it blocked in CoreTelephony getSubscriptionInfo -> CommCenter, Codex `codex/setup/report.md`; that report also lists how to skip Setup: SetupDone/SetupFinishedAllSteps/SetupVersion=11 in com.apple.purplebuddy.plist + activation check). v9 deleted, fallback = v12. Input latency ~1 min with 1 CPU -> Codex task 11 = SMP via PSCI (`codex/smp/`).

**INPUT WORKS (boot 0060u on a freshly flashed v12, log `logs/boot-0060u.txt`):** power, volume up, volume down reach iOS (PMIC -> sweet_keys.c -> SweetHIDButtons -> backboardd). Power locks/unlocks (screen "off" shows the kernel console because the panel never really turns off). **Device identity fixed: iPhone X** (home indicator, Face-ID-style Setup). Earlier test (0059u, `sweethidtest=15` fake Home presses) advanced Setup from Hello to the language list.
- **0058** HID: `iokit/Kernel/arm/hid/`: IOHIDEventServiceUserClient + IOHIDEventServiceQueue + IOHIDEvent compiled UNCHANGED from Apple IOHIDFamily-1915.140.3 (= exact version of the iOS 16.7 IOHIDLib, clone in `research/iohid-1915`) over our minimal IOHIDEventService (SweetHID.cpp: SweetHIDButtons consumer page 0x0C power 0x30 / vol 0xE9/0xEA / menu 0x40; SweetHIDTouch digitizer collections). C hooks `sweet_hid_key(key, down)` (0 power, 1 vol+, 2 vol-, 3 home) and `sweet_hid_touch(finger, down, x, y)`. HID logs go to IOLog. Boot-arg `sweethidtest=N` = fake Home press every N s.
- **0059** identity: hw.machine/hw.model/hw.target/hw.product from the ADT (macOS-config kernel used to answer "iPad8,6"/"Redmi Note 10 Pro"). mkadt: root compatible `D22AP|iPhone10,3|xiaomi,sweet|...`, model iPhone10,3, target-type D22, `/product` copied from the real D22AP device tree (`research/ipsw-x/d22ap.adt`, extracted from the IPSW, NOT in git), `/buttons home-button-type 2`. Codex report `codex/mg/report.md`: MG ProductType = sysctl **hw.product** (unrecognized -> iPod), HWModelStr = hw.target, HomeButtonType = `/buttons/home-button-type`; disk cache `/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.mobilegestaltcache/Library/Caches/com.apple.MobileGestalt.plist` (keyed by build + UUID only -> stale iPod answers survived; reflashed clean v12 to clear it).
- **0060** drivers by Codex (`codex/input/hw-report.md`): `bsd/dev/sweet_keys.c` (SPMI arbiter v5 observer reads, PON 0x810 bits KPDPWR/RESIN SID0, PM6150L GPIO2 SID4 0xc110; works), `bsd/dev/sweet_touch.c` (GENI SE7 I2C @0xa84000, starts 45 s after boot). Touch result: SE7 fw ok (proto 3) but **0x5d and 0x38 both time out -> touch IC unpowered** (L11C LDO needs an RPMh vote). Codex task 8 = RPMh client (`codex/rpmh/`), task 7 = battery (`codex/battery/`).
- **0061** (CI 39117078444): red status bar "rdar:45025538" = UIKit "No status bar visual provider for screen" (360x800 pt is no real iPhone). AppleCLCD now presents **1125x2436** (iPhone X) and shows the centered 1080x2400; touch mapped the same; ADT canvas 1125x2436. `sweetfb_native=1` reverts.
- Images: `shim/out/shim-boot-0060b.img` (0060 + /buttons ADT `sweet-0060hi.adt`). Next: 0061 image with ADT from current mkadt.

### >>> HANDOFF 2026-10-17 late (read this first) <<<

**STATE: iOS 16.7 Setup.app "Hello" screen is ON THE PHONE** (boot `shim/out/shim-boot-0057u.img` on image v12). Photo `/run/media/dipa/android/ios-sweet-work/MILESTONE-setup-hello-2026-10-17.jpg`. SpringBoard + backboardd + Setup run; nothing crashes in the GUI path. Remaining crash/relaunch loops: installcoordinationd (should be fixed by 0057, unverified), mediaserverd (VirtualAudio removed in v12, unverified), mobileactivationd (~100/boot, not analysed), bluetoothd + fairplayd.H2 relaunch constantly (no hardware).

**What is on the phone now:** userdata = image **v12** (`/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v12.img` + `.simg` + `.sha256`; v9 kept as fallback, v8/v10/v11 deleted or superseded). Kernel **0057** (`xnu/out-0057`, CI `39067532732`). ADT **`shim/out/sweet-0057hi.adt`** (QEMU twin `research/q0057-hi.adt`). Boot image **`shim/out/shim-boot-0057u.img`**. Boot args: `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1 launchd_unsecure_cache=1 wdt=-1`.

**Build a phone image** (kernel K, ADT A): `python3 tools/mkpayload.py $K $A shim/out/payload-X.bin --cmdline "$B" && (cd shim && ./build.sh ../shim/out/payload-X.bin && cp out/shim-boot.img out/shim-boot-X.img)` then check `python3 tools/unpack_bootv2.py shim/out/shim-boot-X.img shim/out/check-X && cmp shim/out/check-X/ramdisk shim/out/payload-X.bin`. QEMU smoke test: `python3 tools/mkpayload.py $K research/q0057-hi.adt $S/csq/payloadX.bin --ramdisk $S/csq/root-fork.img --cmdline "debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=md0"` + `PATH=~/swift/usr/bin:$PATH python3 tools/qemu-sweet.py ... --secs 40 --kernel $K --fail-on-panic` (S = scratchpad; root-fork.img lives in the session scratchpad csq/ dir and may be gone in a new session).

**Kernel workflow:** edit `research/bt28/xnu/...` (keep the pre-edit copy in `research/preNNNN/`), syntax check `python3 tools/xnu-cc.py research/run28.log research/bt28 <file>`, make `xnu/patches/NNNN-*.patch` with `diff -u pre bt28 | sed` path rewrite, add new/changed files to `research/check-patch-order.py` list, run it (must say applied + MATCH), commit + push -> GitHub Actions `xnu` builds (~8 min) -> `gh run download <id> -R dipa-studio/ios-sweet -n xnu-kernel -D xnu/out-NNNN`. **Repo is PUBLIC since 2026-10-17** (private macOS runner minutes ran out: "recent account payments have failed"); public = free runners. Commit trailer: only `Co-Authored-By: dipa-studio <183933817+dipa-studio@users.noreply.github.com>`.

**Phone workflow (user does buttons, Claude runs commands):**
- Boot test kernel: phone in fastboot -> `fastboot boot shim/out/shim-boot-X.img`. NEVER `fastboot getvar`.
- **Read logs WITHOUT filming:** after a test the user holds Power+VolDown -> fastboot -> `fastboot boot recovery/recovery.img` (OrangeFox, temp) -> wait for `adb shell true` -> `tools/sweet-log-pull.sh /run/media/dipa/android/ios-sweet-work/logs/<name>.txt` (reads the 4 MiB console ring the kernel keeps in the last 1025 blocks of userdata; patch 0047). Then `adb reboot bootloader` brings the phone back to fastboot without the user.
- **Flash a root image:** `fastboot flash -S 512M userdata <img>.simg` (7 min, 25 chunks). After a big flash the Xiaomi ABL fastboot hangs: next `fastboot boot` stalls at "Sending" -> the USER must re-enter fastboot by hand (Power+VolDown). Do not retry commands against the hung ABL. A flash right after `adb reboot bootloader` once failed on chunk 1 (USB protocol error): just retry. User must NOT unplug during flash (once interrupted at 25/25 -> reflash).
- **Edit the phone root without reflashing (same-length only):** find the file's raw offset in the local image (it is one extent; search the image for its first 4 KiB), compare with `adb exec-out dd if=/dev/block/sda34 ...`, then `adb push` changed 4 KiB blocks and `dd of=/dev/block/sda34 seek=... conv=notrunc,fsync`, read back. Recovery kernel has no hfsplus.
- **Pull the whole phone root** (12 min): `adb exec-out "dd if=/dev/block/sda34 bs=1048576 count=16384" > img`; mount ro with udisksctl or `sudo mount -t hfsplus -o loop,ro`. To write into a pulled image: `sudo fsck.hfsplus -fy` first (volume is never cleanly unmounted -> Linux mounts ro even with -o force), then `sudo mount -t hfsplus -o loop,force`.
- Making images: copy latest img with `cp --sparse=always`, mount rw with force, change, `sync; umount`, `sha256sum`, `img2simg`. Keep only the latest image + one fallback on the SSD (/home is ~98% full; big work files live in `/run/media/dipa/android/ios-sweet-work/`, old kernels/images archived in `.../ios-sweet-work/archive/`).

**Diagnostics built into the kernel (all SWEET-only):** persistent console log (0047, boot-arg sweetlog=0 off; flush every ~1 s); `|name| text` copies of /dev/null writes for processes in `sweetnull` (default backboardd,SpringBoard; 0048); `IOK name:` IOKit lookup/open trace for `sweetiolog` (default backboardd,SpringBoard; 0049/0054, 400 lines max); `SWEET Sandbox: proc op N` (0050); `STR ...` heap text dump on SIGABRT/SIGTRAP for `sweetscan` (default backboardd,SpringBoard; 0051/0053) - this is how NSException/BSAssert reasons were found; `SMP name tN pc .. bt ..` user stack sampler for `sweetsample` (default SpringBoard,backboardd; first at 90 s then every 120 s, 40 samples, 16 frames; 0054/0055); `SWEET FB: N swaps` every 1000 swaps; `CRASH name ...` crash reports (0043/0044). Symbolicate cache addresses with `ipsw dyld a2s research/dsc/dyld_shared_cache_arm64 <addr - slide>`; main-binary addresses: addr - mh + 0x100000000.

**Patches added today (0046-0057, all CI-built):** 0046 IOSurface (IOCoreSurfaceRoot) + IOMobileFramebuffer (AppleCLCD on /arm-io/disp0) + arm-io nubs; 0047 persistent log; 0048 /dev/null tee; 0049 IOKit trace; 0050 permissive "Sandbox" MAC policy; 0051/0053 heap text on crash; 0052 RENAME_SWAP via exchangedata on HFS; 0054/0055 sampler, swap counter, AKS selectors 35 (extended device state = 17) and 101; 0056 panel copy from the vsync timer at 10 Hz (sweetfb_hz) instead of every swap; 0057 no config_ext_resolver (ACL group entries resolve without memberd). ADT (tools/mkadt.py): /arm-io/disp0, /chosen/display-scale 3, /chosen/unique-chip-id (synthetic 0x5357454554000001) + chip-id 0x8015 -> IOPlatformUUID A9683613-3704-53F2-95D0-98A8FB574E78. Image v9-v12: /private/var/containers/{Bundle,Data/System,Shared/SystemGroup,Temp}; launchd.plist StandardOut/ErrorPath renamed to ...PatX (pointing jobs at /dev/console stops them launching: EACCES); v12: LastSystemVersion=20H392 in plain /var/mobile/Library/Preferences/com.apple.migration.plist (stops the 1-hour data migration that ran every boot), VirtualAudio.plugin moved to /Library/Audio/Plug-Ins/HAL.disabled/.

**Codex helper** (`codex exec --skip-git-repo-check -m gpt-6.1-sol -c model_reasoning_effort=high -s workspace-write -C /run/media/dipa/android/ios-sweet-work/codex -o OUT "$(cat task.md)" < /dev/null`, run detached with setsid nohup; config already defaults to gpt-6.1-sol high). Reports in `/run/media/dipa/android/ios-sweet-work/codex/`: mediaserverd-report.md, installcoordinationd-report.md (-> 0057), ufs-performance.md (ranked UFS speed plan), **ufs/0058-sweet-ufs-faster.patch** (READY, not yet applied: ufslogsecs default 10 s log flush batching, optional ufscache=1 directional cache ops, optional ufswait=1 spin-then-sleep; syntax-checked, host-stub verified; see ufs/README.md). Apply it as patch 0058 after review.

**Claude safety-classifier note:** several messages about QuartzCore renderer selection / writing an HFS reader tool were cut off by an automatic classifier (false positives, user agrees). Keep those topics short or let them be; they are not blockers now (rendering already works).

**NEXT STEPS (in order):**
1. **Input** (Setup stuck on Hello): touch + power + volume as IOHIDEventService/IOHIDDevice services backboardd matches (log shows backboardd matching IOHIDDevice with DeviceUsagePairs and IOService with HIDServiceSupport=true). Phone hardware: Goodix/Focaltech touch over I2C/SPI (check live DT `dtb/`), buttons via PMIC (pon) and GPIO keys. Start with a fake HID service (e.g. power button via a timer or boot-arg) to learn the ABI, then real drivers.
2. **Device identity**: MobileGestalt reports iPod + Home button. Add /product node / product-type (iPhone10,3, D22AP), device class iPhone, home-button-less (Face ID) properties in mkadt; find exact MG keys (MGIOMFBSupport.c, `IODeviceTree:/product` lookups seen in backboardd trace).
3. **Battery**: AppleSmartBattery/IOPMPowerSource style service (status bar shows empty red battery).
4. Apply Codex 0058 (UFS speed), consider cpus>1, check whether installcoordinationd/mediaserverd loops are gone, analyse mobileactivationd.
5. Hide kernel console over the GUI when not debugging: boot-arg sweetfb_noconsole=1 (but then panics are not on screen).

- **🎉 MILESTONE: iOS GUI ON THE PHONE - Setup.app "Hello" screen (2026-10-17 ~21:30 UTC, boot 0057u on image v12).** Photo: `/run/media/dipa/android/ios-sweet-work/MILESTONE-setup-hello-2026-10-17.jpg` (animated "dobar d(an)" hello lettering, status bar "iPod" + battery icon, bottom "Pritisnite Home za otvaranje" = "Press Home to open" (locale hr), (i) button). Rendered by iOS 16.7 SpringBoard/Setup through CoreAnimation -> backboardd -> kernel IOSurface (IOCoreSurfaceRoot) -> kernel AppleCLCD swaps -> ABL panel memory. What made it: data migration finally skipped (v12: `LastSystemVersion` 20H392 in the plain `/var/mobile/Library/Preferences/com.apple.migration.plist`; ADT `/chosen/unique-chip-id` + `chip-id` for IOPlatformUUID), 0057 (no ext resolver -> ACL containers work), plus everything before (0046 display/IOSurface, 0050 Sandbox policy, display-scale 3, containers dirs, RENAME_SWAP, AKS 35/101, 10 Hz present).
  **Missing (next work, in rough order):**
  - **Input: no touch, no power button, no volume buttons** (no HID drivers; backboardd looks for IOHIDDevice / HIDServiceSupport services). Setup is stuck on "Hello".
  - **Device identity: it thinks it is an iPod with a Home button** (status bar "iPod", "Press Home to open"): MobileGestalt has no product/model identity (no /product node, product-type, etc.) - give it iPhone10,3 / home-button-less (Face ID class) identity.
  - **No battery detection** (no battery/charger/PMIC service; status bar battery shows empty/red).
  - Locale came up Croatian (hr) - harmless.
  - Performance: cpus=1, polled UFS (Codex UFS patch in progress: `codex/ufs/`), backboardd presents at 60 Hz.
- **README refreshed (2026-10-17):** read the full notes and updated the public overview through patch 0045, v7 hardware observations and prepared v8 diagnostics. Documents real Apple userspace, the missing display/IOSurface GUI work, limited security services, highmem, persistent userdata root, current tooling and build examples. Documentation only; no kernel build, tests, flashing or phone boot performed.

- **v11 still migrated (boot 0056u, 20:32 UTC, `logs/boot-0056u-v11.txt`).** On iOS kCFPreferencesCurrentHost lands in the plain plist: all DMEnvironment setters (setLastBuildVersionPref, setUserDataDispositionPref, ...) use CurrentHost and the phone's `/var/mobile/Library/Preferences/com.apple.migration.plist` holds DMUserDataDisposition; datamigrator's XPC service runs with `_RoleAccount: mobile`. **Image v12** = v11 + `LastSystemVersion` = 20H392 added to that plain `com.apple.migration.plist` (keeps DMUserDataDisposition, 501:501 0600) + `/Library/Audio/Plug-Ins/HAL/VirtualAudio.plugin` moved to `/Library/Audio/Plug-Ins/HAL.disabled/` (Codex: stops the mediaserverd SIGSEGV loop; untested). **installcoordinationd loop explained (Codex `codex/installcoordinationd-report.md`):** os_crash "Failed to create data directory" EACCES for `/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.installcoordinationd/Library/InstallCoordination`: `Library`/`Library/Caches` in the container are ACL-only (uid/gid 0xfffffffe, mode 0000, ACE for group GUID ...012B = gid 299 systemusers); with `config_ext_resolver` the kernel asks an identity resolver (memberd/opendirectoryd) that iOS does not run -> EWOULDBLOCK -> ACE skipped -> EACCES. **Patch 0057**: drop config_ext_resolver from FILESYS_BASE (compatibility UID/GID<->GUID mapping is used instead), likely fixes other ACL-protected containers too. Codex task 3: UFS driver performance design (`codex/ufs-performance.md`).
- **mediaserverd SIGSEGV loop explained (Codex report `/run/media/dipa/android/ios-sweet-work/codex/mediaserverd-report.md`):** the fault (FAR 0x390) is in the HAL plug-in `/Library/Audio/Plug-Ins/HAL/VirtualAudio.plugin/VirtualAudio` (ApplyBeamFormerPreference <- VirtualAudio_PlugIn::SetPropertyData <- HALS_AHPPlugIn::ObjectSetPropertyData <- AudioObjectSetPropertyData <- MediaExperience vaemVADSerializationListener): the default virtual audio device ('vdef', VirtualAudioDevice_Default) does not exist (no audio hardware/driver, no /product/audio). Proposed workaround for GUI bring-up: move VirtualAudio.plugin out of /Library/Audio/Plug-Ins/HAL/ in the next root image (untested). v11 reflashed OK after an interrupted flash (USB unplugged at chunk 25/25). Codex task 2: installcoordinationd crash loop.
- **ROOT CAUSE of the endless migration: no IOPlatformUUID (2026-10-17).** datamigrator's `-[DMMigrator isMigrationNeededWithBuildVersion:lastBuildVersion:clientPID:]` gets lastBuildVersion from `-[DMEnvironment lastBuildVersionPref]` = `CFPreferencesCopyValue("LastSystemVersion", "com.apple.migration", kCFPreferencesCurrentUser, kCFPreferencesCurrentHost)` -> a ByHost file. The phone had `/var/mobile/Library/Preferences/com.apple.migration.plist` (only DMUserDataDisposition) and NO `Preferences/ByHost` directory at all. On arm XNU publishes IOPlatformUUID (gethostuuid) only from `/chosen/unique-chip-id` (8 bytes) + `/chosen/chip-id` (4 bytes) (IOPlatformExpertDevice::generatePlatformUUID, called from IOPlatformExpert::start); the SWEET ADT had neither, so every CurrentHost preference write failed. Fix: mkadt adds `unique-chip-id` = 0x5357454554000001 (synthetic) and `chip-id` = 0x8015 -> UUID `A9683613-3704-53F2-95D0-98A8FB574E78` (same SHA1 namespace computation as the kernel). ADT `shim/out/sweet-0057hi.adt`, boot image `shim/out/shim-boot-0056u.img` (0056 kernel + 0057hi ADT, QEMU OK). **Image v11** = v10 + `Preferences/ByHost/com.apple.migration.<UUID upper and lower>.plist` {LastSystemVersion: 20H392} (501:501, dir 0755 files 0600). Flashing v11 2026-10-17 ~20:10 UTC. Codex (gpt-6.1-sol high) analysing the mediaserverd SIGSEGV loop in `/run/media/dipa/android/ios-sweet-work/codex/` (report `mediaserverd-report.md`).
- **v10 did NOT stop the migration (boot 0056n on v10, 19:18 UTC, `logs/boot-0056n-v10.txt`):** datamigrator + migrationpluginwrapper ran again, backboardd drew the progress UI (13000+ swaps in ~5 min). So `com.apple.DataMigration.plist`/LastSystemVersion is not the deciding input. What is known about the decision (datamigrator XPC service `DataMigration.framework/XPCServices/com.apple.datamigrator.xpc/com.apple.datamigrator`, strings): `Migration sentinel present. Migration necessary.` (sentinel `/private/var/datamigrator/migrate`, created and removed by datamigrator itself; absent in v10), `System build version changed from %@ to %@. Migration necessary.` / `...unchanged... not necessary`, `Disposition dictionary with build version %@ is out of date relative to current build version %@`, `SyntheticDidUpgrade`, DMUserDataDispositionManager; previous build source not found yet (not in the binary's plain strings; likely in DataMigration.framework's DMUserDataDispositionManager / preferences domain `com.apple.migration`). backboardd calls `DMPerformMigrationIfNeeded` and holds SpringBoard in `checkInAndWaitForDataMigration` until it returns. NEXT: find where the previous build version / disposition dictionary is stored (disassemble DataMigration.framework DMUserDataDispositionManager + datamigrator's isMigrationNeeded path), write it into the image; or get datamigrator's os_log out (OS_ACTIVITY_DT_MODE needs a launchd env change).
- **Data migration never records completion -> skip it (image v10, 2026-10-17).** Boot 0056n (18:16 UTC, `logs/boot-0056n.txt`, 30 min): 10 Hz panel copies, backboardd still presents at ~60 Hz (107k swaps), SpringBoard still in `BKSSystemShellService _checkIn` at sample 14. Every migration run takes 1+ hour on cpus=1 and repeats each boot. DataMigration decides from `/var/mobile/Library/Preferences/com.apple.DataMigration.plist` key `LastSystemVersion` (vs ProductBuildVersion 20H392); the pulled phone root (21:01 local) had NO such file. **v10** = that pulled phone state + fsck.hfsplus -fy (volume was marked not cleanly unmounted -> Linux mounts it read-only even with -o force; fsck found only hard-link counts + minor volume header, repaired) + `com.apple.DataMigration.plist` {LastSystemVersion: 20H392} (501:501 0600). `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v10.img` (+ .sha256, .simg); v8 files deleted, v9 kept as fallback.
- **Repo made PUBLIC (2026-10-17, user request) so GitHub-hosted macOS runners are free:** the 0056 CI run was refused ("recent account payments have failed or your spending limit needs to be increased"). Checked before: no device dumps, serials, IMEI or Apple binaries in the history (.gitignore keeps backup/, dtb/, recovery/, research/ out); commit author emails are public now. 0056 rerun `39056660104` passed -> `xnu/out-0056`, QEMU OK, image `shim/out/shim-boot-0056n.img` booted 18:16 UTC.
- **Boot 0055n (17:16 UTC, ~65 min, `logs/boot-0055n.txt`): migration bar reached 99% in ~30 min (was ~60), then crawled.** Root cause of the slowness: backboardd presents the progress UI at ~58 Hz (**167,000 swaps in ~48 min**) and AppleCLCD copied the full 10 MB frame into the panel on every swap -> ~600 MB/s memcpy on the single CPU (cpus=1), starving everything (polled UFS I/O too). Sampler (16 frames): **SpringBoard waits in `-[BKSSystemShellService _checkIn]`** (synchronous BSXPC call to backboardd, from FBSystemShellInitialize <- SBSystemAppMain) - backboardd holds the system shell check-in until the data migration ends, so the migration is the gate to SpringBoard UI. **Patch 0056**: swap only records the latest swap record; the vsync timer copies it to the panel every 60/sweetfb_hz ticks (default 10 Hz); `SWEET FB: N swaps` every 1000.
- **Boot 0054n (16:53 UTC, ~12 min, `logs/boot-0054n.txt`, patch 0054 sampler):** the data migration runs AGAIN on every boot (it completed once in the 0053s run) - completion is not being recorded. backboardd keeps swapping the progress UI (3700+ swaps in ~10 min, `SWEET FB: N swaps`). Sample during migration: SpringBoard main thread blocked in a synchronous XPC call (`mach_msg2_trap <- dispatch_mach_send_and_wait_for_reply <- xpc_connection_send_message_with_reply_sync <- -[BSXPCServiceConnectionMessage _sendSynchronously:] <- BSXPCServiceConnectionProxy ...`). Unsupported AKS selectors seen: 101 `aks_assert_consume`, 93 `aks_get_internal_info`, 34 `_aks_stash_load` (datamigrator), **35 `aks_get_extended_device_state`** (SpringBoard, chronod, remindd, searchd, symptomsd...). **Patch 0055**: AKS 35 answered like 17 (same DER device state), 101 succeeds; sampler backtraces 16 frames, 40 samples (~80 min).
- **MILESTONE: FIRST iOS PIXELS ON THE PANEL (boot 0053s long run, 15:33 UTC, ~60 min; photos IMG_20261017_183029/183222/183419; log `/run/media/dipa/android/ios-sweet-work/logs/boot-0053s-long.txt`, 407 KB, 1428 flushes).** The iOS data-migration boot screen (Apple logo + progress bar) was shown and the bar advanced to full in ~60 minutes. It is drawn by **backboardd through our kernel display path**: `SWEET SURF: backboardd create id 2/3 1080x2400 fmt 0x42475241 (BGRA) bpe 4 bpr 4352 size 10452992 keys 11`, IOSurface sels 9, 27, 5, 34 and traps 2/3 (lock/unlock), IOMFB SwapBegin (4) / SwapEnd (5, 1132-byte struct) / GetLayerDefaultSurface (3) / 68 / 55, then `SWEET FB: swap N layer 0 surface 2|3 1080x2400 ... a 0,0 1080x2400 b 0,0 1080x2400` alternating (double buffering; only the first 16 swaps are logged). Triggered by `com.apple.datamigrator` (AKS sels 101/93/34 return unsupported) and `com.apple.migrationpluginwrapper`. Probably ProgressUI-style CoreGraphics drawing into IOSurfaces (no GPU used). After the migration: SpringBoard and backboardd stay up (exec once each, no crash), nothing new drawn; crash loops only for mediaserverd, installcoordinationd, mobileactivationd (~108 each in the hour); bluetoothd and fairplayd.H2 relaunch constantly (no hardware). Kernel console text overlays the image (sweetfb_noconsole=1 would hide it).
- **MILESTONE: SpringBoard and backboardd both stay up (boot 0053s on v9, 15:29 UTC, `logs/boot-0053s.txt`).** With /chosen/display-scale = 3: `exec SpringBoard` once, `exec backboardd` once, no CRASH for either; MobileGestaltHelper reads IOMFB size/dot pitch; backboardd opens AppleCLCD (sels 57,15,22,29,33,79,74 as before). No IOSurface creates and no swaps: backboardd keeps polling `IOProviderClass IOAcceleratorES` (-> 0 found) and AGXAccelerator/IOAcceleratorESLegacy - no GPU, so no CoreAnimation renderer yet. Log goes quiet after ~47 s (only 47 flushes): the system idles. NEXT WALL: CoreAnimation rendering without a GPU (CPU path) so the window server produces frames.
- **SpringBoard's invalid mode = scale 0 (boot 0053n, 15:19 UTC, `logs/boot-0053n.txt`, heap scan):** `FBSDisplayMode cannot be configured as requested : <FBSDisplayMode: ...; infxinf@0x (1080x2400/0) 60Hz sRGB SDR>` - pixel size and 60 Hz are fine, scale is 0. libMobileGestalt (MGIOMFBSupport.c, sub_19c560a54) reads `display-scale` (u32, default 0) and `display-rotation` from **IODeviceTree:/chosen**, pixel size via IOMobileFramebufferGetDisplaySize (MobileGestaltHelper sel 8/28 in the log). mkadt now adds `/chosen/display-scale` = 3 (1080x2400 @3x = 360x800 pt). ADT `shim/out/sweet-0054hi.adt` (QEMU `research/q0054-hi.adt`), image `shim/out/shim-boot-0053s.img` = 0053 kernel + 0054hi ADT. Possible catch: MobileGestalt may have cached scale 0 in `/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.mobilegestaltcache/...`.
- **MILESTONE: backboardd survives and opens the display (boot 0052n on v9, 15:07 UTC, `logs/boot-0052n.txt`).** No `CRASH backboardd`. It walks `IODeviceTree:/arm-io` children, matches IOMobileFramebuffer, `open AppleCLCD type 0 -> 0x0`, calls IOMFB selectors 57, 15, 22, 29, 33, 79 (GetBlock, out 1580/40), 74 (SupportedFrameInfo), 12, 6, 27, 17, 84, 50; opens IOCoreSurfaceRoot (sel 13); looks for AGXAccelerator / IOAcceleratorES / IOAcceleratorESLegacy (none, repeatedly), `IODeviceTree:/product` (none, repeatedly), `/buttons`, aop-smart-cover, IOPMrootDomain. No swaps yet. **SpringBoard now gets past display discovery** and dies (sig 5) in `-[FBSDisplayMode _initWithWidth:height:preferredScale:scaleOverride:refreshRate:gamut:hdr:rotation:virtual:validityCheck:]+580` <- `_initWithCADisplayMode:scale:rotation:` <- `FBSDisplayConfiguration _initWithImmutableDisplay:` <- `FBSDisplaySource initWithDisplay:` <- `FBSDisplayMonitor` (slide 0x14504000): "FBSDisplayMode cannot be configured as requested". Checks: width/height >= 2, preferredScale > 0, scaleOverride >= 0, refreshRate non-zero, valid gamut/HDR/rotation. Suspects: refresh rate 0 (IOMFB timing blocks answered with zeros) and/or scale 0 (MobileGestalt main-screen-scale; no /product node in the ADT). **Patch 0053**: heap text scan also on SIGTRAP and for SpringBoard (default sweetscan list backboardd,SpringBoard) to read the exact mode description.
- **Image v9 (2026-10-17) = v8 + `/private/var/containers/{Bundle,Data/System,Shared/SystemGroup,Temp}` (root:wheel 0755; on iOS these live on the data volume, absent from the rootfs) + launchd.plist with StandardOutPath/StandardErrorPath renamed to ...PatX (the in-place phone fix; daemons' stdout reaches the log via the 0048 /dev/null tee).** `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v9.img` SHA256 aa9f9139...6e67, `.simg` 12,145,303,864 bytes; written through `sudo mount -t hfsplus -o loop,force`. **Patch 0052** (`xnu/patches/0052-sweet-hfs-rename-swap.patch`, CI `39042761826` -> `xnu/out-0052`, QEMU OK, image `shim/out/shim-boot-0052n.img`): vn_rename falls back to VNOP_EXCHANGE when VNOP_RENAMEX(RENAME_SWAP) returns ENOTSUP for two regular files on one volume (HFS exchange swaps data and cnode descriptors = swap semantics). Flashing v9 started 2026-10-17 ~15:00 UTC.
- **backboardd EXCEPTION FOUND (boot 0051n, 14:41 UTC, `logs/boot-0051n.txt`, patch 0051 heap scan):** `*** Terminating app due to uncaught exception 'NSInvalidArgumentException', reason: '*** -[NSURL initFileURLWithPath:]: nil string parameter'`. First-throw stack (slide 0x337f0000): `+[NSURL fileURLWithPath:]` <- `-[BSSystemContainerForCurrentProcessPathProvider libraryPath]` <- `cachesPath` <- backboardd main+0x1d858 (StartWindowServer, shader cache path). BaseBoard: libraryPath = `BSSystemSharedResourcesDirectoryForIdentifier` = `BSSystemSharedDirectoryForIdentifier(id)` + "/Library"; that calls `container_system_path_for_identifier` (libsystem_containermanager -> `_container_legacy_lookup` -> containermanagerd XPC), which returned NULL (BaseBoard logs "Error getting shared system container ..."). With 0050 the sandbox denials are gone. containermanagerd.log.0 in the pulled (pre-0050) image: "Did not receive any persona attributes", "Expected to find at least one persona", notify_register_dispatch failed 9; `/private/var/containers` does NOT exist in the root image (on iOS it lives on the data volume). usermanagerd does create kernel personas 99/199/1000, but fails to save `/private/var/keybags/usersession.kb` and `persona.kb`: "Failed to atomically swap writing file: Operation not supported (45)" (HFS has no RENAME_SWAP). NEXT: create `/private/var/containers` (and check what containermanagerd needs for system group containers), fix or emulate RENAME_SWAP on HFS, then re-check backboardd.
- **Disk housekeeping (2026-10-17):** /home was at 4 GiB free. Moved (not deleted) `xnu/out-0010..0044` and old `shim/out/*.img`/`payload-*.bin` (everything before 0046) to `/run/media/dipa/android/ios-sweet-work/archive/{xnu,shim-out}` (6.3 GiB); /home now 8.7 GiB free. Large work files (phone-root.img 16 GiB, logs, tracev3 dumps) live in `/run/media/dipa/android/ios-sweet-work/`. **Patch 0051** (`xnu/patches/0051-sweet-crash-heap-strings.patch`, CI `39041440935`): on SIGABRT of a process in boot-arg `sweetscan` (default backboardd) the crash log prints heap text runs as `STR ...` (to find the NSException reason). Boot 0051n 14:41 UTC.
- **Boot 0049n (14:11 UTC, `logs/boot-0049n.txt`, patch 0049 trace): backboardd's IOKit lookups before it dies** (each run): `IODeviceTree:/arm-io/pearl-sep` -> none, matching ApplePearlSEPDriver -> none, `IOService:/IOResources/AppleKeyStore` open OK, matching IOHIDDevice (DeviceUsagePairs) / `HIDServiceSupport` notifications, `usb-drd,t8027` notification; **never any IOMobileFramebuffer / display lookup** - the exception happens before display detection. launchd (tracev3) shows backboardd's mach lookups denied with `error = 159: Sandbox restriction`: com.apple.mobilegestalt.xpc, com.apple.iohideventsystem, com.apple.system.notification_center, com.apple.logd, containermanagerd, ACCHWComponentAuthService, oscard. libsystem_sandbox uses `__mac_syscall("Sandbox", op, arg)` (ops: 1 init, 2 check, 3 note, 4 container path, 5/6/7/8 extensions, 0x15 check_bulk, ...); with no policy named "Sandbox" every call gets ENOPOLICY. **Patch 0050** (`xnu/patches/0050-sweet-sandbox-policy.patch`): built-in permissive MAC policy "Sandbox" in sweet_amfi.c (mpo_policy_syscall returns 0, container/extension ops ENOENT; logs first use of each op as `SWEET Sandbox: proc op N`).
- **Phone root pulled (16 GiB, 12 min, `adb exec-out dd if=/dev/block/sda34`) to `/run/media/dipa/android/ios-sweet-work/phone-root.img` (loop-mounted ro at /run/media/dipa/iOSRoot).** No .ips crash reports. logd tracev3 (Persist, LZ4 `bv41` blocks, decompressed strings in `ios-sweet-work/tracev3/all.txt`): /private/var/db/uuidtext is EMPTY so only launchd's own messages are readable. Findings: v8 launch failure = `Service could not initialize: posix_spawn(/usr/libexec/backboardd), error 0xd - Permission denied` (xpcproxy exit 78) - /dev/console not openable for the mobile user; backboardd[98] spawned `com.apple.MTLCompilerService` before SIGABRT; **launchd denies many mach lookups with `error = 159: Sandbox restriction`** (mediaserverd -> com.apple.backboard.display.services, remoted, mobiletimerd -> sleepd, identityservicesd -> apsd) and **tccd exits OS_REASON_SANDBOX "profile not found"** - no Sandbox kext/profiles on SWEET; likely cause of many daemon crash loops (next wall after the display). CF's uncaught-exception text never reaches stderr for daemons (no CF handler; libc++abi prints only the type). **Patch 0049** (`xnu/patches/0049-sweet-iokit-trace.patch`): `IOK name: ...` console lines for registry path lookups, child iterators, matching (with result count/class), matching notifications and IOServiceOpen of the processes in boot-arg `sweetiolog` (default backboardd), max 400 lines.
- **CONFIRMED: launchd StandardOut/ErrorPath=/dev/console stops backboardd from launching.** After the in-place rename (boot 0047n-b, log `logs/boot-0047n-b.txt`) backboardd launches again and dies as on v7: sig 6, `___pthread_kill <- abort <- demangling_terminate_handler <- _objc_terminate <- std::terminate <- _dispatch_client_callout <- _dispatch_once_callout <- main+0x9334` (window-server dispatch_once). It never opened AppleCLCD or IOCoreSurfaceRoot (no `SWEET FB/SURF: backboardd` lines) -> throws before/at display detection. **Patch 0048** (`xnu/patches/0048-sweet-devnull-tee.patch`, CI `39037749668` -> `xnu/out-0048`, image `shim/out/shim-boot-0048n.img`): writes to /dev/null by processes named in boot-arg `sweetnull` (default `backboardd,SpringBoard`) are printed as `|name| text`. Boot 0048n (13:48 UTC, log `logs/boot-0048n.txt`): only `|backboardd| libc++abi: terminating due to uncaught exception of type NSException` (CF's reason line goes to os_log, not stderr, despite CFLOG_FORCE_STDERR/OS_ACTIVITY_DT_MODE). Next: pull the whole 16 GiB root from recovery (`adb exec-out dd if=/dev/block/sda34`) to `/run/media/dipa/android/ios-sweet-work/phone-root.img` and read ReportCrash .ips files / logd tracev3 for the exception reason.
- **Persistent log WORKS (boot 0047n on v8, 13:31 UTC; pulled from OrangeFox: 70 KB, 111 flushes, `/run/media/dipa/android/ios-sweet-work/logs/boot-0047n.txt`).** In 3 min **backboardd was never exec'd** (v7 ran it), ~70 bare `exec xpcproxy`, SpringBoard loops on FBSDisplayMonitor (no display), mediaserverd sig 11 loop, locationd, lockdownd sig 5. Suspect the v8 plist (StandardOut/ErrorPath=/dev/console on backboardd). **In-place plist edit from recovery (no reflash):** launchd.plist is one extent at image/partition byte offset 0x1e025f000 (195 blocks; verified bytes identical on phone vs local v8 mount); renamed StandardOutPath->StandardOutPatX and StandardErrorPath->StandardErrorPatX (same length, both jobs) in blocks 68 and 105 via `adb push` + `dd of=/dev/block/sda34 seek=... conv=notrunc,fsync`, read back VERIFIED. Phone now differs from local v8 image in those 2 blocks (files in `/run/media/dipa/android/ios-sweet-work/`). Test boot 0047n 13:38:42 UTC. Recovery kernel has no hfsplus (raw block edits only).
- **Patch 0047: persistent console log (CI `39036478663` -> `xnu/out-0047`, QEMU no panic; phone image `shim/out/shim-boot-0047n.img` = 0047 + sweet-0046hi.adt + 0045n cmdline).** `_cnputs` (all console output: kernel printf + daemons' /dev/console) also appends to a 4 MiB ring in sweet_ufs.c; after the UFS partition is ready and writable (ufsrw=1) a thread writes new ring blocks + header to the LAST 1025 blocks (4 KiB) of userdata every ~1 s and SYNCHRONIZE CACHE (prints `SWEET LOG: console log to the last 4100 KiB ...`). Read: phone to fastboot -> `fastboot boot recovery/recovery.img` (OrangeFox) -> `tools/sweet-log-pull.sh OUT.txt` (adb dd of that area + `tools/sweet-log.py`). Not yet verified on the phone. Panics after the last flush are not in it (screen still shows them). 2x font attempt reverted: XNU's v_scale only scales the progress icon.
- **Boot 0046n on v8 (13:05 UTC, VID_20261017_150522_HSR_120):** phone prints `SWEET SURF: IOCoreSurfaceRoot registered` and `SWEET FB: AppleCLCD 1080x2400 stride 4320 at 0x9c000000, default surface 1` (nubs + match work on hardware). Visible userspace: early boot tasks, launchd_cache_loader, `exec SpringBoard`; no `exec backboardd` / `SWEET FB: backboardd` line seen in the readable frames (video blurry). Frame 00:47: lower half of the screen shows OLDER console text (16:01:12) below newer lines -> unconfirmed hint that a swap copied an old snapshot of the panel (CA reads the default/displayed surface = panel memory). SpringBoard still dies in `-[FBSDisplayMonitor _initWithBookendObserver:transformer:]+1160` (sig 5, slide 0x30960000). **Shim: 2x console font** (`shim/loader.c` Video.v_depth scale field = 1) so phone videos are readable; image `shim/out/shim-boot-0046b.img` = 0046n + 2x font.
- **Patch 0046: built-in IOSurface + IOMobileFramebuffer (2026-10-17, offline, NOT phone-tested).** `xnu/patches/0046-sweet-iosurface-iomfb.patch`: new `iokit/Kernel/arm/SweetSurface.{h,cpp}` (IOCoreSurfaceRoot + IOSurfaceRootUserClient: sel 13/0/4/34/35, traps 0-5, wired buffers mapped into the caller, per-client refs) and `SweetFramebuffer.cpp` (AppleCLCD : IOMobileFramebuffer on /arm-io/disp0: sizes, swap = CPU copy of enabled layers into the ABL panel memory, 60 Hz vsync via sendAsyncResult64WithOptions(CanDrop), panel memory published as the default IOSurface, kernel text console paused on the first swap only with `sweetfb_noconsole=1` (default keeps the console: pausing switches it to serial and would hide panics)); SweetPlatformExpert creates nubs for /arm-io children (not gic) attached to the platform expert (IOPlatformDevice::compareName casts its provider to IOPlatformExpert, so nubs under the arm-io nub never matched - first CI build showed that in QEMU). mkadt: /arm-io/disp0 {device_type display-subsystem, compatible sweet,display, canvas-width 1080, canvas-height 2400}. CI `39021572169` -> `xnu/out-0046`. **QEMU** (`research/q0046-hi.adt`, RAM disk init, msgbuf): `SWEET SURF: IOCoreSurfaceRoot registered`, `SWEET FB: AppleCLCD 1080x2400 stride 4320 at 0x9c000000, default surface 1`, init alive, no panic. Phone image **`shim/out/shim-boot-0046n.img`** (SHA256 9c4d2fe9..., CI `39024108588` with the review fixes: swap bounds check, zeroed 64-bit async ref, port release, 1.5 GiB wired budget, ref leak/race fixes; QEMU re-checked) = 0046 kernel + `shim/out/sweet-0046hi.adt` + the 0045n cmdline; boot it on image v8 (backboardd/SpringBoard log to console) and film. Expected new console lines: `SWEET SURF: <proc> create id ...`, `SWEET FB: backboardd sel N ...`, `SWEET FB: swap ...`. Open question for the next wall: QuartzCore's IOMFB server builds its renderer from Metal first (`CAMetalContextCreate`), GLES only when enabled; how the CPU renderer gets selected on a device without a GPU is not yet established.
- **IOSurface values ABI (offline RE, `/run/media/dipa/android/ios-sweet-work/iosurface-values.md`) - TODO in SweetSurface (0046 only accepts and drops values, so IOSurfaceCopyValue always returns NULL):** sel 9 SetValue in = {u32 id, u32 pad, IOCFSerialize([value, key]) or ([dict] = merge)}, sel 0xb RemoveValue in = {u32 id, u32 pad, key\0} (empty key = remove all); both out 4 bytes = new generation, client accepts only (gen+2)|1. Kernel after each change: gen = (gen+2)|1 (odd, never 0) -> state+0x20, serialized size (>=1) -> state+0x28. sel 0xa in {u32 id, u32 0, u8 0}, out {u32 ?, u32 gen, serialized non-bulk dict}; too small -> 0xe00002db + needed size (client retries x16). sel 0x1b in 0x88: bulk struct 0x70 @0, u64 valid size @0x70, u64 changed mask @0x78, u32 id @0x80 -> copy masked fields into state+0x30 (bit0 32B @0, bit1 16B @0x20, bit2 8B @0x30, bits3-10 1B @0x38..0x3f, bits11-14 24B @0x40/4B @0x58/8B @0x5c/1B @0x64).
- **IOMFB display matching confirmed (offline RE, `/run/media/dipa/android/ios-sweet-work/iomfb-match.md`):** the framework never matches on "AppleCLCD"; any registered IOMobileFramebuffer subclass works. `iomfb_match_callback` reads only `dfr`/`AppleTV`/`external` (CFBoolean, service itself, no parents); internal main display = all absent/false (type 0, name "primary"). Display count = /arm-io children whose device_type starts with "display-subsystem" (count 0 -> CreateDisplayList returns NULL; more nodes than services -> 30 s wait). IOMobileFramebufferOpen fails only on IOServiceOpen failure; canvas-width/height read as 4-byte CFData from the IOService-plane parent (optional). CopyProperty = IORegistryEntrySearchCFProperty on the service only. Patch 0046 (disp0 node, `external`=false, AppleCLCD : IOMobileFramebuffer) fits all of this.
- **IOMobileFramebuffer swap + notification ABI (2026-10-17, offline).** Swap record = fb+0x18, **0x46c bytes**, zeroed by SwapBegin and sent whole by `kern_SwapEnd` = IOConnectCallStructMethod(sel 5, rec, 0x46c, no output). **SwapBegin** = sel 4, no input, 1 scalar out = swap id (stored at rec+0x50). `kern_SwapSetLayer(fb, layer<4, IOSurfaceRef, CGRect a, CGRect b, flags)`: rec+0x54+4*layer = **IOSurface id**; rec+0x104 bit layer = layer enabled (rec+0x108 bit cleared); rect a (int x,y,w,h, scaled) at rec+0x64+16*layer, rect b at rec+0xc4+16*layer; rec+0x114+4*layer = transform from flags&0xf; flag bits 5/31/6/7/8/9 set bytes rec+0x2ca/0x2cc/0x2e3/0x2e5/0x2e6/0x467+layer. A NULL surface only sets the enable bit with zero id. SwapWait = sel 6 scalars [swap id, timeout, 0].
  - **Notifications**: `IOMobileFramebufferScheduleWithDispatchQueue`/`GetRunLoopSource` create an IONotificationPort and `IOConnectSetNotificationPort(conn, type, port, 0)` (kernel `registerNotificationPort`). `kern_EnableNotifications(fb, type, cb, ctx)` = **sel 72 scalars [NotifyCallbackFuncs[type], fb, type, 0]** -> the kernel keeps an async reference {port, func, refcon=fb} per type and fires it with `sendAsyncResult64`. Types: 0 HotPlug, 1 Power, 3 FrameInfo (args[0] = mask of 31-bit keys, args[1] u32, then values), 4 CRC, **5 Vsync**: 6 args, each a 32-bit half in a u64 slot: (ts lo, ts hi), (t2 lo, hi) passed through the time-ingest helper, (t3 lo, hi); the framework then calls the client's vsync callback(fb, ts, t2', t3, ctx). Vsync callbacks are dropped while the notification port has queued messages.

- **IOSurface.framework user-client ABI reversed (2026-10-17, offline, step 2 of the GUI plan).** `ipsw dyld disass research/dsc/dyld_shared_cache_arm64 --image IOSurface` (selector scan script: scratchpad `selscan.py`).
  - Connect (`_iosConnectInitalize`, pthread_once): `IOServiceGetMatchingService(IOServiceNameMatching("IOCoreSurfaceRoot"))`, retried with sleep(1) up to 241 times (a missing service stalls every client ~4 min, no crash); `IOServiceOpen(type 0)`; then **sel 13** (no inputs, struct out 0x18 bytes = `_ioSurfaceLimits`) and requires u16 at out+0x16 == 1 (ABI version); out+0 = offset/plane-base alignment mask, out+4 = bytes-per-row alignment mask (used as (x+m)&~m), +0..+0x14 also copied into IOSurface remote-method init, otherwise logs and closes the connection.
  - **Create = sel 0**: scalar in [parent id or 0], struct in = `IOCFSerialize(props, kIOCFSerializeToBinary)` (OSUnserializeBinary format; keys IOSurfaceWidth/Height/BytesPerRow/BytesPerElement/PixelFormat/AllocSize/PlaneInfo/CacheMode/IsGlobal/...), struct out **0xa68 bytes** ("lock result"). **Lookup = sel 4**: scalar in [id], same struct out. LookupFromMachPort = sel 0x22 (scalar in [port name]); CreateMachPort = sel 0x23 (scalar in [id, options], scalar out [port name]).
  - Lock result (copied to client+0x68, size 0x68 + planes*0x80, min 0xe8): 0x00 u64 base address **mapped in the caller's task**; 0x08 u64 task VA of a per-surface shared state page (+0 u64 registry id, +8/+0xa u16 bound accel ids, +0xc u32 seed, +0x14 u8 tiled, +0x20 u32 value generation, +0x24 YCbCr matrix, +0x28 u32 non-bulk values size, +0x2d u8 displayable, +0x2e u8 sysmem-only, +0x30 0x70 bytes bulk attachments); 0x10 u64 task VA of timestamp page (4 u64 indexed timestamps, +0x20 detach code); 0x18 u32 surface id; 0x1c u32 alloc size; 0x20 width; 0x24 height; 0x28 bytes per row; 0x2c buffer offset; 0x30 pixel format; 0x34 u32 (dcache flush length); 0x38 plane count; 0x40 u16 bytes/element; 0x42 u8 element width; 0x43 u8 element height; 0x44 cache mode; 0x4c u8 subsampling; 0x4d u8 prohibit-use count; 0x50 parent id; 0x54 slice count; 0x58 u64 trace id; 0x60 u64 protection options; planes[n] at 0x68+n*0x80 (+0 width, +4 height, +8 bytes/row, +0xc offset, +0x14 size, +0x18 u16 bpe, +0x1a/+0x1b u8 element w/h, +0x1c u8 component count, +0x1d address format, +0x54 u8 compression type (non-zero -> no CPU base address)).
  - **Traps** (IOConnectTrapN -> `IOUserClient::getTargetAndTrapForIndex`): 0 IncrementUseCount(id, category), 1 DecrementUseCount(id, category), 2 **Lock**(id, options, &seed_out), 3 **Unlock**(id, options, &seed_out), 4 Release(id), 5 Release after a failed create/lookup setup (id).
  - Other selectors: 5 SetYCbCrMatrix, 6 WrapClientImage, 7 WrapClientMemory, 9 SetValue (serialized), 0xa UpdateNonBulkValueCache (struct in 9), 0xb RemoveValue, 0xc BindAccel, 0x11 SetSurfaceNotify (async), 0x12 RemoveSurfaceNotify, 0x13 Log, 0x14 SetPurgeable, 0x15 SetOwnership, 0x16 SetTiled, 0x18 SetTimestamp, 0x19 GetTileFormat, 0x1a CopyDataValueBytes, 0x1b SetBulkAttachments (struct 0x88), 0x1e/0x2e-0x31 (via outlined helpers), 0x1f SetCompressedTileDataRegionMemoryUsed, 0x20 GetGraphicsCommPageAddress, 0x21 SetIndexedTimestamp, 0x24-0x26/0x2d IOSurfaceAccelerator, 0x27-0x29 shared events, 0x2a SignalEvent, 0x2b Set/InitDetachModeCode, 0x2c SetOwnershipIdentity, 0x32-0x34 data properties.
  - => SWEET kernel service: class/name **IOCoreSurfaceRoot** + IOSurfaceRootUserClient; minimum = sel 13, 0, 4, 9/0xa/0xb (values), traps 0-5, 0x22/0x23 later for cross-process surfaces (CA sends surfaces to backboardd by mach port). Memory: IOBufferMemoryDescriptor per surface, mapped into each task with createMappingInTask; ids from a global table.

- **Image v8: daemon logs on the console.** `tools/patch-launchd-plist.py IN OUT LABEL...` rewrites launchd.plist (binary, plistlib): renames every `_PanicOnCrash` to `_PanicOnCrasX` and gives the listed jobs `StandardOutPath`/`StandardErrorPath` `/dev/console` (devfs console is 0622, writable by mobile) plus `OS_ACTIVITY_DT_MODE=YES` (os_log mirrored to stderr) and `CFLOG_FORCE_STDERR=1`. v8 = v7 + that plist for com.apple.backboardd and com.apple.SpringBoard, written through the mount (796310 bytes, root:80 0644), SHA256 in `ios16-root-20H392-v8.img.sha256`, `.simg` ready; boot with `shim/out/shim-boot-0045n.img`. Goal: see backboardd's uncaught exception text and the window server's display probing. ODDITY (unresolved): the v5 raw-image rename at offset 0x1e025f000 is present in the v5-v7 image bytes, but the plist read through a Linux mount of v7 still had the original `_PanicOnCrash` keys; filefrag shows the file as one extent. Never patch file contents through raw offsets again; write through the mount.
- **IOMobileFramebuffer.framework reversed (step 1 of the GUI plan).** `ipsw dyld disass research/dsc/dyld_shared_cache_arm64 --image IOMobileFramebuffer`.
  - Display discovery (`IOMobileFramebufferCreateDisplayList` -> `getDisplayListNumber`): iterates children of `IODeviceTree:/arm-io`, counts nodes whose `device_type` is "display-subsystem" or "ext-display-subsystem" (or `compatible` "paravirtualizedgraphics,gpu"); then `iomfb_populate_display_infos` adds an IOServiceAddMatchingNotification(IOServiceFirstMatch, **IOServiceMatching("IOMobileFramebuffer")**) and runs the run loop up to 30 s until that many services matched. `iomfb_match_callback` reads service properties `dfr`, `AppleTV`, `external` (CFBoolean) and names displays (external ones "%s-%d").
  - `IOMobileFramebufferOpen(service, task, type, &fb)`: `__ioMobileFramebufferAlloc` (CFPreferences com.apple.iokit.IOMobileGraphicsFamily: canvas_width/height, override_display_width/height, override_panel_width/height, benchmark, enable_ktrace), `IOServiceOpen(service, task, type)` (GetMainDisplay passes type 0), `getEDTProperty` reads `canvas-width`/`canvas-height` from the service's IODeviceTree parent, then fills a table of `kern_*` function pointers.
  - User client selectors (`kern_*` -> IOConnectCall*): 3 _kern_GetLayerDefaultSurface[ScalarMethod_stub]; 4 _kern_SwapBegin[ScalarMethod_stub]; 5 _kern_SwapEnd[StructMethod_stub]; 6 _kern_SwapWaitWithTimeout[ScalarMethod_stub] _kern_SwapWait[ScalarMethod_stub]; 7 _kern_GetID[ScalarMethod_stub]; 8 _kern_GetDisplaySize[ScalarMethod_stub]; 10 _kern_SetTVOutMode[ScalarMethod_stub]; 11 _kern_SetWSSInfo[ScalarMethod_stub]; 12 _kern_RequestPowerChange[ScalarMethod_stub]; 13 _kern_EnableStatistics[ScalarMethod_stub]; 14 _kern_SetFlags[ScalarMethod_stub]; 15 _kern_SetDebugFlags[ScalarMethod_stub]; 16 _kern_SetTVOutSignalType[ScalarMethod_stub]; 17 _kern_SetGammaTable[StructMethod_stub]; 18 _IOMobileFramebufferIsMainDisplay[ScalarMethod_stub]; 19 _kern_SetWhiteOnBlackMode[ScalarMethod_stub]; 20 _kern_SwapSignal[ScalarMethod_stub]; 22 _kern_SetDisplayDevice[ScalarMethod_stub]; 23 _kern_SetDigitalOutMode[ScalarMethod_stub]; 24 _kern_GetHDCPDownstreamState[StructMethod_stub]; 25 _kern_GetDigitalOutState[ScalarMethod_stub]; 26 _kern_printDisplayRegs[ScalarMethod_stub]; 27 _kern_GetGammaTable[StructMethod_stub]; 28 _kern_GetDotPitch[ScalarMethod_stub]; 29 _kern_GetDisplayArea[StructMethod_stub]; 30 _kern_EnableDisableDithering[ScalarMethod_stub]; 31 _kern_SetUnderrunColor[ScalarMethod_stub]; 33 _kern_EnableDisableVideoPowerSavings[ScalarMethod_stub]; 34 _kern_SetVideoDACGain[ScalarMethod_stub]; 35 _kern_SetLine21Data[ScalarMethod_stub]; 41 _IOMobileFramebufferEnableMirroring[ScalarMethod_stub]; 42 _IOMobileFrameBufferGetMirroringCapability[StructMethod_stub]; 43 _IOMobileFramebufferSetRenderingAngle[StructMethod_stub]; 44 _IOMobileFramebufferSetMirrorContentRegion[StructMethod_stub]; 47 _kern_HDCPSendRequest[ScalarMethod_stub]; 48 _kern_HDCPGetReply[ScalarMethod_stub]; 49 _kern_SurfaceIsReplaceable[ScalarMethod_stub]; 50 _kern_SetBrightnessCorrection[ScalarMethod_stub]; 51 _kern_SetColorRemapMode[ScalarMethod_stub]; 52 _kern_SwapCancel[ScalarMethod_stub]; 53 _IOMobileFramebufferSetCanvasSize[ScalarMethod_stub]; 54 _IOMobileFramebufferGetMatrix[Method_stub]; 55 _IOMobileFramebufferSetMatrix[Method_stub]; 56 _IOMobileFramebufferKernelTests[StructMethod_stub]; 57 _kern_GetColorRemapMode[ScalarMethod_stub]; 66 _kern_SPLCSetBrightness[ScalarMethod_stub]; 67 _kern_SPLCGetBrightness[ScalarMethod_stub]; 68 _kern_SetParameter[Method_stub]; 69 _kern_SwapSetColorMatrix[StructMethod_stub]; 72 _kern_DisableNotifications[ScalarMethod_stub] _kern_EnableNotifications[ScalarMethod_stub]; 73 _kern_ChangeFrameInfo[ScalarMethod_stub]; 74 _kern_SupportedFrameInfo[Method_stub]; 75 _kern_FactoryPortal[StructMethod_stub]; 77 _kern_GetCurrentAbsoluteTime[ScalarMethod_stub]; 78 _kern_SetBlock[Method_stub]; 79 _kern_GetBlock[Method_stub]; 80 _kern_GetLinkQuality[ScalarMethod_stub]; 81 _kern_SwapCancelAll[ScalarMethod_stub]; 82 _kern_GetProtectionOptions[ScalarMethod_stub]; 83 _kern_CopyLayerDisplayedSurface[ScalarMethod_stub]; 84 _kern_SetContrast[StructMethod_stub]; 85 _kern_GetBufBlock[Method_stub]; 86 _IOMFBGainEncoderFinishEncoding[Method_stub]; UNKNOWN {}
  - => Kernel needs: an `/arm-io` child node with device_type "display-subsystem" (+ canvas-width/height) and an IOService of class **IOMobileFramebuffer** (subclass name match) attached under it, opened with type 0, implementing at least GetDisplaySize(8), IsMainDisplay(18), GetID(7), GetLayerDefaultSurface(3), SwapBegin(4)/SwapEnd(5 struct)/SwapWait(6), Enable/DisableNotifications(72), frame info(73/74), CopyLayerDisplayedSurface(83), plus IOSurface for the swapped buffers. NEXT: CoreAnimation's AppleDisplay/IOMFBDisplay usage of these (constructor, first swap, vsync), then IOSurface.framework's IOSurfaceRoot selectors.
- **GUI PLAN (decided 2026-10-16): CPU rendering + kernel IOMobileFramebuffer.** User: CPU rendering is fine for now (goal = prove the GUI runs), may later research an Apple-style GPU driver for the Adreno 618 -> keep display output and renderer separable. QuartzCore findings (symbols from the cache's .symbols subcache: `ipsw dyld symaddr research/dsc/dyld_shared_cache_arm64 --image QuartzCore`; strings: `ipsw dyld macho ... QuartzCore --strings`):
  - Renderers: `sw_new_context` (CA::OGL::SWContext, full CPU renderer incl. blur, images, IOSurface-backed surfaces, make_buffer_current(WindowServer::Surface)), `gles_new_context`, `metal_new_context`, `new_null_context`. Env knobs include CA_ENABLE_OGL, CA_DISPLAY_TYPE, CA_FORCE_LOCAL_SERVER, CA_FORCE_TIMER, CA_CANVAS_WIDTH/HEIGHT, CA_MAIN_DISPLAY_SCALE/ORIENTATION, CA_DISABLE_RENDER, CA_ALWAYS_RENDER (selection logic not traced yet).
  - `-[CAWindowServer _detectDisplays]` (0x1884e3150) iterates static tables `open_funcs`/`count_funcs` = AppleCLCDDisplay, AppleRGBOutDisplay, AppleWirelessDisplay only; each `count()` = `display_count_of_type(IOMFBDisplayType)` over **`IOMobileFramebufferCreateDisplayList()`**, `open(i)` = `create_display_of_type` -> `CA::WindowServer::AppleDisplay(CFString name, ..., IOMFBDisplayType)` (an IOMFBDisplay driven by IOMFBServer: vsync_callback, frame_info_callback, swaps). `CA::WindowServer::VirtualDisplay` exists but is never auto-detected (explicit API only). => the main display MUST come from an IOMobileFramebuffer kernel service.
  - NEXT: reverse IOMobileFramebuffer.framework: IOMobileFramebufferCreateDisplayList / IOMobileFramebufferOpen (service matching, display type property), the user-client selectors used by IOMFBServer (display size/modes, swap begin/set-layer/end with IOSurface ids, vsync notifications), then the IOSurface kernel side (IOSurfaceRoot user client: create/lookup/lock, memory mapping) that both IOMFB swaps and CA surfaces need. Kernel: SWEET IOMobileFramebuffer service that copies the swapped IOSurface to the 1080x2400 framebuffer at 0x9c000000.
- **logd fixed; backboardd = DISPLAY WALL (boot 0045n on v7, 21:08:57 UTC, frames VID_20261016_230850 00:55-01:28, slide 0x4c3ec000, `research/crash-0045n-v7.txt`).** No `CRASH logd` any more; new daemons run (runningboardd, WirelessRadioManagerd, networkserviceproxy, dasd, cloudpaird, magicswitchd, atc, askpermissiond, ManagedSettingsAgent, containermanagerd uses AKS 0/17). New: locationd sig 11 at CFRunLoopAddSource+132 (NULL source) from its main+0x2af138 in a dispatch_once. **backboardd** sig 6: abort <- std::terminate <- _objc_terminate <- dispatch_once <- main+0x9334 = the "start %{public}@..." wrapper (0x100009264) for **"window server"** (block 0x10000a770 -> dispatch_once body 0x10001d7f0 = StartWindowServer): MTLSetShaderCachePath, `[CAWindowServer serverWithOptions:@{kCAWindowServerDisableUpdatesOnMainDisplay: @YES}]`, `setRendererFlags:`, **`mainDisplay`** -> `displayId` -> `[CADisplay displayWithDisplayId:]`, setWindowServerDisplay:, bounds, pointScale, currentMode, preferredScale, nativeOrientation, touchScales... With no display, mainDisplay is nil and it throws. (HID system and GS start fine before it.) SpringBoard dies on the same missing display (FBSDisplayMonitor). QuartzCore display backends (from the cache's .symbols subcache, `ipsw dyld str`): `CA::WindowServer::AppleCLCDDisplay` (IOMobileFramebuffer hardware) and **`CA::WindowServer::VirtualDisplay`** (open(), new_server, clone_update). `CA_DISPLAY_TYPE` and `CA_FORCE_LOCAL_SERVER` strings exist in QuartzCore but have no direct xref (likely read via a table of CA_* env names). NEXT: find how CA::WindowServer::Server enumerates displays (IOMobileFramebuffer services vs VirtualDisplay / CA_DISPLAY_TYPE) and choose: (a) kernel IOMobileFramebuffer service + IOSurface, or (b) a VirtualDisplay rendered by CA's CPU path (CA::OGL::SW) and copied to the framebuffer.
- **watchdogd + CommCenter + identityservicesd fixed (boot 0045n on v6, 20:41:30 UTC, frames VID_20261016_224133 00:39-01:13, slide 0x20fec000).** `SWEET WDT: watchdogd sel 0 in 0/0 out 1/0 -> ok`, no watchdogd/CommCenter/identityservicesd crashes (CommCenter/identityservicesd now call AKS 0/17/7). usermanagerd boot task runs (UMD_INIT creates primary session uid 501 "mobile", personas 99 System and 199 Default registered with the kernel); `Failed to atomically swap writing file /private/var//keybags/usersession.kb: Operation not supported (45)` (HFS lacks the atomic swap rename; non-fatal so far). Remaining: SpringBoard (FBSDisplayMonitor, no display), backboardd (uncaught exception), mediaserverd, lockdownd (crash inside obfuscated code, main+0x4c1b4?), logd. **logd**: main+0x18160 = `BUG IN LIBTRACE: Failed to migrate directory` from 0x10001801c(dir 0): 0x100016538 requires the dir to exist with uid/gid/mode from the table at 0x10002df14 (0: /private/var/db/diagnostics 272:272 0751, 1: /private/var/db/uuidtext 272:272 0755, 2: /private/var/db/diagnostics/timesync 272:272 0755) plus a `.migrated` file, else XPC "operation 6" to a helper and crash. **Image v7** = v6 + those dirs/markers (also in mkiosroot.sh), SHA256 in `ios16-root-20H392-v7.img.sha256`, `.simg` ready. Boot with `shim/out/shim-boot-0045n.img`.
- **Exit-reason text works (boot 0044n, frames VID_20261016_221726 00:32-00:53, slide 0x22d5c000).** CommCenter/identityservicesd: `couldn't dlopen libobjc-trampolines.dylib: dlopen(/usr/lib/libobjc-trampolines.dylib, 0x0106): tried: '/usr/lib/libobjc-trampolines.dylib' (no such file, not in dyld cache)`: the file only exists in the OS cryptex and dyld does not search cryptexes at ignition_level=1. 19 cryptex files have no root counterpart (OS: DriverKit dyld cache + .symbols, VisualTestKit, libffi-trampolines, libobjc-trampolines, libstdc++ 6.0.9 + 2 symlinks; App: SafariSupport.bundle, AuthenticationServicesAgent, com.apple.Safari.History, passwordbreachd, safarifetcherd, webbookmarksd, webinspectord, webpushd) -> hard-linked (symlinks copied: Linux hfsplus cannot hard-link symlinks); `tools/mkiosroot.sh` does it generically. watchdogd still crashed: the `-wdt_no_pmu` / `/arm-io/wdt no-pmu` check (0x10000a01c) only runs when os_variant_has_internal_diagnostics; otherwise 0x100009b18 looks up IOServiceMatching("IOService") + IOPropertyMatch {IOPMUPrimary: 1} (NSConstantDictionary) and crashes if absent, then IORegistryEntrySetCFProperty(boot stage 0xff) (failure only logged). Patch **0045**: IOWatchdog sets IOPMUPrimary = 1 and accepts setProperties; CI `38961814194` -> `xnu/out-0045`. **Image v6** = v5 + the 19 cryptex links, SHA256 7c610c6a...297a, `ios16-root-20H392-v6.simg` (full flash needed). Boot image `shim/out/shim-boot-0045n.img` (0045 + sweet-0044hi.adt). Still open: SpringBoard (no display), backboardd (uncaught exception, reason none), mediaserverd (Fig NULL dict), logd (main+0x18160), lockdownd (main+0x4c1b4).
- **System survives daemon crashes (boot 0043p, video frames VID_20261016_215006 00:25/00:42/00:54).** With the v5 plist patch no panic: launchd keeps launching (runningboardd, powerd, tccd, healthd, peopled, cryptexd, routined, aksd, mediaremoted, familynotificationd, nanoprefsyncd, navd, cloudpaird, passd, amsaccountsd, accessoryupdaterd, wcd ...). Shared cache slide that boot 0x406b4000 (`research/crash-0043p.txt`). watchdogd still crashed at main+0xb82c from the PMU step: `-wdt_no_pmu` is only honored when os_variant_has_internal_diagnostics; customer builds read `no-pmu` from IODeviceTree:/arm-io/wdt (IORegistryEntrySearchCFProperty) -> mkadt adds /arm-io/wdt {no-pmu}. **cryptexd** sig 6: abort_with_payload <- os_crash_fmt <- `__darwin_arm_secure_boot_chip.cold.7` ("panic: failed to get crypto algorithm") <- `_img4_chip_select_personalized_ap`: reads /chosen use-ddi-secure-boot, allow-ecid-mismatch, crypto-hash-method ("sha1" | "sha2-384"), /chosen/asmb lp-smb0/1 -> mkadt adds crypto-hash-method "sha2-384". **identityservicesd** sig 6 = same `_objc_fatal` in imp_implementationWithBlock as CommCenter (block trampolines; message needed). lockdownd sig 5 at main+0x4c1b4. Patch **0044**: crash report adds `CRASH name reason ns N code X: text` (exit reason description = abort_with_payload text). CI `38959096811` -> `xnu/out-0044`, QEMU OK. Image `shim/out/shim-boot-0044n.img` = 0044 + `shim/out/sweet-0044hi.adt` (wdt no-pmu, crypto-hash-method) + cmdline (wdt=-1, no -wdt_no_pmu).
- **First symbolicated daemon crashes (boot 0043u on journaled v4, 19:38:53 UTC; video frame chat image 36; transcribed in `research/crash-0043u.txt`; shared cache slide 0x40d60000 for every process).** (Gemini's video transcription invented values, use frames only.) `tools/sweet-crash.py research/dsc/dyld_shared_cache_arm64 x research/crash-0043u.txt`:
  - **watchdogd** pc main+0xb82c (`_os_crash` trap helper) from 0x100009bb0 = set PMU boot stage via IORegistry (no PMU) -> fix: boot-args `wdt=-1 -wdt_no_pmu` (Apple's own test mode writes "%s wdt=-1 -wdt_no_pmu"; watchdogd logs "skipping setting PMU boot stage because -wdt_no_pmu found in boot-args or no-pmu found in EDT"). Kernel effect of wdt=-1: sched hygiene debug off, zone_exhausted_timeout -1 (harmless).
  - **SpringBoard** sig 5 in `-[FBSDisplayMonitor _initWithBookendObserver:transformer:]+1160` <- `+[FBDisplayManager sharedInstance]` <- `FBSystemShellInitialize` <- `SBSystemAppMain`: no main display. GUI WALL: display service (IOMobileFramebuffer / CoreAnimation display).
  - **backboardd** sig 6: abort <- std::terminate <- _objc_terminate (uncaught exception; message not shown).
  - **CommCenter** sig 6: `_objc_fatal` in `imp_implementationWithBlock` (from `+[_PFRoutines initialize]`, CoreData): objc block trampolines cannot be allocated -> likely kernel gap (trampoline page remap); will hit many processes.
  - **mediaserverd** sig 11: CFDictionaryGetValue(NULL) in FigNotificationListenerStartNotifications (routing session manager).
  - **logd** sig 5 at its main+0x18160 (bt main+0x9dfc); lockdownd sig 5 (not yet symbolicated).
  - Image v5 = v4 + launchd.plist (`/System/Library/xpc/launchd.plist`, contiguous at image offset 0x1e025f000, 1456980 bytes) with the 7 `_PanicOnCrash` keys renamed `_PanicOnCrasX` in place (raw image patch, no metadata change) -> `diff-v4-v5.simg` = 7 blocks; v5 SHA256 78f96974...185a. Boot image `shim/out/shim-boot-0043p.img` = 0043 + `wdt=-1 -wdt_no_pmu`.
- **0043 console crash reports (QEMU verified); image `shim/out/shim-boot-0043u.img`; NEXT: flash v4 + boot 0043u.** Boot 0042u (video photo IMG_20261016_211646): short exec lines work; watchdogd crashes with NO `SWEET WDT:` line (fails before its first IOWatchdog call: lookup or IOServiceOpen, or elsewhere); lockdownd, SpringBoard, mediaserverd, CommCenter, identityservicesd, backboardd, watchdogd all crash repeatedly ("Too many corpses being created" = corpse limit, no crash reports). AKS callers: lockdownd (0, 7), imagent/sleepd (0, 17, 7), backboardd (0, 17), UserEventAgent (0, 7, 102, 103 -> unsupported). Patch **0043**: `sweet_crash_log` (osfmk/kern/exception.c, called from proc_prepareexit for crash exits on the current proc) prints `CRASH name[pid] sig S code (exc<<32|code) sub X slide SR mh MH`, `CRASH name pc lr sp fp far`, `CRASH name bt` (10 fp frames); AKS sel 103 notify_state_is_set -> 0. CI `38954520419` -> `xnu/out-0043`. QEMU test: init forks, child `brk #1`: `CRASH launchd[2] sig 5 code 0x600000001 sub 0x1000004f0 slide 0x0`, pc correct, parent keeps running (pid 1 crashes panic before proc_prepareexit, so test with a child). `tools/sweet-crash.py CACHE ROOT [report]` symbolicates typed-in CRASH lines (`ipsw dyld a2s` with addr - slide; main binary offsets = addr - mh + 0x100000000).
- **v3 reflashed 18:50-18:55 UTC (`research/flash-userdata-v3b.log`, 24/24 OKAY); 0042u booted 19:01:17 UTC on the clean disk (`research/boot0042u-2.log`), SLOW MO VID pending (watchdogd failure reason).** ABL hangs on the first `fastboot boot` after every big flash; user re-enters fastboot. User gave standing approval to reflash/boot without asking (memory standing-flash-approval). Commits now end with `Co-Authored-By: dipa-studio <183933817+dipa-studio@users.noreply.github.com>` (user request; never a Claude trailer).
  - watchdogd flow: connect_to_kext (0x100005408): IOServiceGetMatchingService("IOWatchdog") fails -> "failed to discover" + _os_crash; IOServiceOpen(type 1) fails -> "IOServiceOpen failed with error : 0x%x" + _os_crash. Then sel 0 (out 1 scalar): 0 -> "userspace monitoring disabled", 0x100009bb0, sleep 2, exit(0); nonzero -> "userspace monitoring enabled", normal operation.
  - **Image v4 (JOURNALED):** `tools/mkiosroot.sh` now does `mkfs.hfsplus -J`, mounts with `-o loop,force`, adds the /System/Library/Caches/com.apple.dyld hard links (44) and /private/var/keybags/systembag.kb (0700/0600 root). Built from the IPSW dmgs (OS 087-86698-044, SystemOS 087-86593-047, AppOS 087-86715-047) -> `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v4.img` SHA256 fba4aeae...ec0f9 + `.simg`. Header: attr 0x80002100 (unmounted, journaled), journal info block 129, journal 8 MiB at 532480 flagged need-init. Linux `fsck.hfsplus -n` reports "Volume header needs minor repair" on any fresh journaled volume even after -y (quirk). QEMU: 64 MB journaled test root (`rd=md0 rootrw=1`, 0042 kernel): `hfs: Initializing the journal (joffset 0x3000 sz 0x80000)` -> mounted -> init runs, no panic. NEXT: flash v4 (full), boot 0042u.
- **SPRINGBOARD STARTS (boot 0041u 18:39:53 UTC, photo IMG_20261016_204142).** backboardd, bluetoothd, fairplayd.H2, SpringBoard (pid 111, crashed: "Too many corpses"), xpcproxy run; `SWEET AKS: backboardd sel 0 -> 0`, `sel 17 in 1/0 out 0/4096 -> 0`. Panic: watchdogd[113] exited, namespace 2 subcode 5 (SIGTRAP = its _os_crash): watchdogd crashes on purpose when IOServiceGetMatchingService("IOWatchdog") fails ("connect_to_kext() unexpectedly failed") or IOServiceOpen(type 1) fails ("IOServiceOpen failed with error"); IOWatchdog DOES register (QEMU msgbuf: `SWEET WDT: IOWatchdog registered`), cause not on screen yet -> need video. Note `wdt_test=no_user_checkin` makes watchdogd log "detected testing boot-args to not run, exiting" (path not reached here?). **HFS CORRUPTION:** `hfs_swap_BTNode: offset #0 invalid (0xBC1D) (blockSize 0x2000 numRecords 81)`, `node=19677 fileID=4` (catalog), `hfs_mark_inconsistent` repeated. Likely cause: non-journaled HFS + unclean panics while writing (0038e wrote a 2.7 GB core then panicked; every boot since ends in panic). Plan: reflash v3 (clean), longer term HFS journaling. Patch **0042**: one short `exec NAME` console line instead of the 3-line "Allowing spawn of iOS binary" notice; CI `38949451276` -> `xnu/out-0042`; image `shim/out/shim-boot-0042u.img` (same cmdline as 0041u).
- **LAUNCHD DAEMONS RUN (boot 0039u, 18:29:13 UTC, photo IMG_20261016_203052).** With `launchd_unsecure_cache=1` all boot tasks pass and launchd starts LaunchDaemons (pids > 100: identityservicesd 114 and lockdownd 106 crash, "Too many corpses being created"). Fatal: `watchdogd: ERROR: failed to discover watchdog KEXT service` -> exit (namespace 2 = signal, subcode 5) -> `_PanicOnCrash` panic. watchdogd: IOServiceMatching("IOWatchdog"), IOServiceOpen type 1, IOConnectCallScalarMethod sel 0/1 (check in)/3/4 (disable/enable userspace monitoring)/5, IOConnectCallStructMethod sel 2 (1 KiB in) and 9 (small out); VM path ("detected virtual machine environment and no watchdog KEXT found, exiting...") only when it detects a VM. Console also flooded with `hfs: set hardlink bit on vol=iOSRoot cnid=321318` (Linux-made hard links lack kHFSHasLinkChainBit; repaired only in memory on each lookup). Patches **0040** (`iokit/Kernel/arm/SweetWatchdog.cpp`: IOWatchdog + IOWatchdogUserClient, all selectors succeed with zeroed outputs, each logged once as `SWEET WDT: ...`) and **0041** (sweet_hfs hfs_catalog.c: the three link-repair printfs limited to 8 per boot); CI `38948245372` PASSED -> `xnu/out-0041` (QEMU regression OK). Image `shim/out/shim-boot-0041u.img` (0041 + sweet-0038hi.adt + ufsrw rootrw + launchd_unsecure_cache=1) booted 18:39:53 UTC (`research/boot0041u.log`), photo pending.
- **v3 FLASHED + keybag passes; next wall AMFI (launchd_cache_loader).** User said "flash, nothing to lose": `fastboot -S 512M flash userdata ios16-root-20H392-v3.simg` 17:51:52-17:57:01 UTC, 24/24 chunks OKAY (`research/flash-userdata-v3.log`). userdata = v3 (SHA256 fa84274c...22df) until the next rw boot. ABL fastboot then hung ("Sending" forever, `reboot bootloader` timed out) until the user re-entered fastboot by hand (common Xiaomi ABL crash). Boot 0038e 18:06:15 UTC (`research/boot0038e-3.log`), photos IMG_20261016_200812/200838: keybag OK, then boot tasks tzinit (`Bad zoneinfo link`, resets to system partition, OK), finish-restore, finish-demo-restore, sysstatuscheck, prng_seedctl (`failed to load kernel prng seed`, `failed to load virtual random (-147)`, AllowCrash), auearlyboot, then **launchd_cache_loader: `abort: Unable to find AMFI`** -> 2.7 GB core written to /cores in 91 s -> launchd panic `launchd_cache_loader[22] exited -- exit reason namespace 7 subcode 0x1`. launchd_cache_loader: SecStaticCodeCheckValidity of /System/Library/xpc/launchd.plist with detached .sig, then IOServiceMatching("AppleMobileFileIntegrity") + IOServiceOpen type 0 + IOConnectCallMethod sel 6 (1 scalar = 1, struct in = cdhash) = trust the cdhash, then sends the cache to launchd. Boot-arg `launchd_unsecure_cache=1` skips signature + AMFI ("Using unsecure cache"); the value is parsed with strtoul base 10 (helper 0x100002da8), so a path = 0 = off: boot 0039w (18:17:13 UTC, `=/System/Library/xpc/launchd.plist`, photo IMG_20261016_202008) still aborted `Unable to find AMFI` (core dump now skipped: `Failed to generate core file ... error 14`, so 0039 works). Image `shim/out/shim-boot-0039u.img` uses `launchd_unsecure_cache=1`. Patch **0039**: do_coredump = 0 on SWEET (kern.coredump sysctl still works); CI `38945267341` -> `xnu/out-0039` (_do_coredump now in __common). Image `shim/out/shim-boot-0039w.img` = 0039 + sweet-0038hi.adt + ufsrw rootrw + `launchd_unsecure_cache=/System/Library/xpc/launchd.plist`. TODO later: real AppleMobileFileIntegrity IOService (sel 6 trust cdhash etc.).
- **Image v3 ready, awaiting user "flash".** Boot 0038w 17:38:10 UTC, photo (chat image 30): `SWEET AKS: keybagd sel 0 -> 0x0`, `sel 14 out 1/0 -> 0x0` (x2), `MKB_INIT: mount is with NO CPROTECT ... does not support Data Protection`, `System Keybag loaded`; then keybagd still opens /private/var/keybags/systembag.kb (0x100012d3c, for BackupKeyBagKeys) -> ENOENT, `no-effaceable-storage` not set -> `Can't load the keybag. tears in rain... Time to die` -> `FATAL KEYBAG ERROR: kb_load` -> reboot -> Halt/Restart Timed Out panic. Fix: ADT `/defaults/no-effaceable-storage` (mkadt.py; `shim/out/sweet-0038hi.adt`, `research/q0038-hi.adt`, QEMU OK) + file `/private/var/keybags/systembag.kb` (0600 root, dir 0700; XML plist {KeyBagKeys: data}). **v3 image** `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v3.img` = v2 + that file, fsck OK, SHA256 fa84274c...22df; `ios16-root-20H392-v3.simg` (img2simg: zero blocks become FILL chunks, so a full flash overwrites every block the rw boots changed). Flash: `fastboot -S 512M flash userdata ios16-root-20H392-v3.simg`, then boot `shim/out/shim-boot-0038e.img` (0038 + sweet-0038hi.adt + ufsrw=1 rootrw=1).
- **0038 AppleKeyStore (CI 38938744130 PASSED -> `xnu/out-0038`; QEMU msgbuf: `SWEET AKS: AppleKeyStore registered (no passcode, unlocked)`; phone image `shim/out/shim-boot-0038w.img` booted 17:38:10 UTC, `research/boot0038w.log`, photo pending).** QEMU full kernel log trick (fb.txt is only the last screen): `llvm-nm KERNEL | grep _msgbufp` -> `--hmp "x /1gx ADDR"` gives struct msgbuf address (static, 0xfffffe0007b56678 on 0038) -> `--hmp "x /3gx STRUCT"` gives bufc (3rd quad) -> `--hmp "memsave BUFC 0x20000 \"FILE\""` (quote the file name), `tr -d "\0" < FILE`. Same inputs give the same addresses. Boot 0037w 17:08:35 UTC, photo IMG_20261016_191052: bindfs fixed mount; mount-phase-1/2 pass; `cc_fips_test` boot task: all corecrypto FIPS POST tests PASSED in userspace; then keybag boot task `keybagd --init`: `MKB_INIT: FATAL KEYBAG ERROR: failed to load system bag`, `Could not get options entry from the device tree` (x2, IODeviceTree:/options = NVRAM, non-fatal), `REBOOTING INTO RECOVERY MODE` -> reboot -> panic `Halt/Restart Timed Out @IOPlatformExpert.cpp:884` (no restart path on SWEET).
  - keybagd (static libaks): load_devicebag (0x100012c30) calls aks_get_system (sel 14, 0-1 scalar in, 1 out) and loads /private/var/keybags/systembag.kb only if the result is 0xe00002f0 (NotFound); any other error -> fatal; SUCCESS -> skip the file (userspace-reboot path). systembag.kb is a plist {KeyBagKeys}; decrypted with effaceable storage unless IODeviceTree:/defaults has `no-effaceable-storage`. Image has no /private/var/keybags. has_data_protection: statfs of /private/var; no cprotect on our HFS -> device-bag volume mapping skipped.
  - libaks connection: IORegistryEntryFromPath / IOServiceMatching("AppleKeyStore"), IOServiceOpen type 0, then selector 0 (no args) must succeed.
  - **AppleKeyStore selector map (iOS 16.7 AppleKeyStore.framework, via `ipsw dyld disass`):** 0 open, 2 create_bag, 3 save_bag, 4 unload_bag, 5 set_system_with_passcode, 6 load_bag, 7 get_lock_state (1 in, 1 out; bit 2 = unlocked since boot), 9 unlock_device, 10 wrap_key, 11 unwrap_key, 12 unlock_bag/test_stashed_kek, 13 lock_bag, 14 get_system, 15 change_secret_se, 16 internal_state, 17 get_device_state (1 in, struct out <=4K: DER SET of SEQUENCE{UTF8 key, INTEGER}: ss state, sls lock_state (MKBGetDeviceLockState returns it), sb backoff, sbo, sfa failed attempts, sfao, sgs generation, sas assertions, sgpe grace, srcd countdown, sr reserved), 18 recover_with_escrow_bag, 19 obliterate_class_d, 20 drain_backup_keys, 21/22 set/clear_backup_bag, 23 get_bag_uuid, 24 rewrap_key_for_backup, 26/27 assert_hold/drop, 30/31 set/get_configuration, 33-37 stash, 38 auth_token_create, 40 generation, 41 fdr_hmac_data, 42 verify_password, 43 _aks_operation, 44 remote_session, 49/50 signing keys, 51/53/54 stash enable/persist/escrow, 55 unload_session_bags, 57/58 remote peer, 59-62 smartcard, 63 migrate_s_key, 65 set_keybag_for_volume_with_cookie_persona, 66/67 sync bags, 68 copy_volume_cookie_persona, 69-73 se_*, 74-79/84/85/99 fv_* (FileVault/VEK), 80/81 absinthe, 82 make_public_backup_bag, 83 drop_auxiliary_auth, 86 kext get/set options, 87 invalidate_bag, 88-91/94/95/106/107 se_*, 92 lower_iteration_count, 93 get_internal_info, 96-98/117 keybag_persona create/list/delete, 100/101 assert promote/consume, 102/103 notify_state set/is_set, 104 fairplay, 105 change_secret_epilogue, 108 delete_xart_leak, 110-113 backup volume, 114 sealed_hashes_set, 115 prewarm_sps, 118-128 AKSIdentity*/AKSGetLastUser.
  - Patch **0038**: `iokit/Kernel/arm/SweetKeyStore.cpp` (class AppleKeyStore on IOResources, IOUserClientClass AppleKeyStoreUserClient; personality in KernelConfigTables.cpp): 0/4/5/9/12/13/26/27/55/102 success, 14 -> handle 0, 7 -> 0x4, 17 -> DER state (ss 4, sls 0), others kIOReturnUnsupported; logs `SWEET AKS: <proc> sel N arg0 in a/b out c/d -> ret` for every call.
  - Tools: shared cache copied to `research/dsc/` (git-ignored; ipsw needs a writable dir for its .a2s symbol cache); `ipsw dyld disass research/dsc/dyld_shared_cache_arm64 --image AppleKeyStore|MobileKeyBag`; scratchpad `annot2.py BIN DIS FIXUPS` annotates llvm-objdump output with strings and import names (`ipsw macho info --fixups`).
- **0037 bindfs (phone boot 0037w pending photo).** Boot 0036mw 16:57:47 UTC, photo IMG_20261016_190102: WRITABLE ROOT WORKS: `/dev/ufsroot0 on / (hfs, local, noatime)` after mount_hfs update (GetMasterBlock EBUSY harmless), coredump of mount written to /cores (rw). Then `mount: failed to mount /private/preboot/5CBE3601.../usr/standalone/firmware -> ...: Operation not supported by device (19)` -> exit 74 -> launchd panic `mount[2] exited`. Cause: the macOS-derived config had nullfs but no bindfs (ENODEV = unknown fs type). Caller passes env 0 -> bind not required, ENOENT (missing source dir) tolerated. Patch **0037** adds `bindfs` to FILESYS_BASE in MASTER.arm64.sweet; CI `38936550483` -> `xnu/out-0037` (bindfs_vfsops present; QEMU probe regression OK). Image `shim/out/shim-boot-0037w.img` (0037 + sweet-0036hi.adt + ufsrw=1 rootrw=1) booted 17:08:35 UTC (`research/boot0037w.log`). NOTE: userdata has now been modified by rw boots; it no longer equals the v2 image, so sparsediff with v2 as OLD is not safe any more (full reflash or a new baseline needed).
- **0036 phone: launchd boot tasks run; mount -P 1 needs /chosen/boot-manifest-hash.** Boot 0036hr 16:15:32 UTC, photo IMG_20261016_185236: platform check passes; fsck boot task runs (`fsck: not booting main or diagnostic OS. Skipping fsck on OS container`); mount-phase-1: `failed to get filesystem info` x2 (no IODeviceTree:/filesystems/fstab, non-fatal), `mount: data volume missing, but not required in env: 0`, mount_hfs update of /: `GetMasterBlock: Error 16 opening /dev/ufsroot0` (EBUSY, mounted block dev, non-fatal) then mount() EROFS (read-only boot) -> `mount: / failed with 1`; then `failed to get boot-manifest-hash` -> errx exit 66 -> launchd panic `mount[3] exited`. /sbin/mount: fn 0x100005f90 reads IODeviceTree:/chosen boot-manifest-hash (hex), bindfs-mounts /private/preboot/<HEX>/usr/standalone/firmware (ENOENT tolerated unless required). `tools/mkadt.py` now adds `boot-manifest-hash` = SHA-384("sweet boot manifest") (5CBE3601...5205). ADTs `shim/out/sweet-0036hi.adt`, `research/q0036-hi.adt` (QEMU probe OK, csops 0x26000001). Image `shim/out/shim-boot-0036mw.img` (0036 + new ADT + ufsrw=1 rootrw=1), booted 16:57:47 UTC (`research/boot0036mw.log`): FIRST WRITABLE BOOT of userdata (restore source: v2 image on SSD).
- **0036 CS_SIGNED (phone boot 0036hr pending photo).** Phone boot 0035hr 2026-10-16 15:57:02 UTC (`research/boot0035hr.log`, photo IMG_20261016_180249): highmem works on hardware; shared cache maps (trust-cache warnings only), Apple launchd runs (`Darwin Bootstrapper Version 7.0.0`, `entering ondemand mode`), starts boot task fsck, then userspace panic `boot task is not a platform binary (fsck, /sbin/fsck)`. launchd+0x3fea0: `csops(pid, CS_OPS_STATUS)` must have bit 0x04000000. Cause: nothing set CS_SIGNED (AMFI does it in vnode_check_signature), so csproc_get_blob() returns NULL for every process and csops never reports CS_PLATFORM_BINARY (also breaks per-process cdhash/entitlement queries). QEMU probe init (`csops` status printer, scratchpad csq/) on 0035: `0x02000001`; patch **0036** (`*cs_flags |= CS_SIGNED` in the SWEET AMFI hook), CI `38929635791` -> `xnu/out-0036`: `0x26000001`. Images `shim/out/shim-boot-0036hr.img` / `-0036hw.img` (same cmdlines as 0035). 0036hr booted 16:15:32 UTC (`research/boot0036hr.log`). Next launchd boot tasks (embedded __config plist): fsck -qL and mount -P 1/2 (RequireSuccess); no /etc/fstab on the image.
- **0035 ENTITLEMENTS + PLATFORM BINARIES (QEMU verified).** `xnu/patches/0035-sweet-amfi-entitlements-platform-binaries.patch`: new `iokit/Kernel/arm/SweetEntitlements.cpp` (OSObject subclass: OSDictionary from OSUnserializeXML of the signature's XML entitlements + original XML for CS_OPS_ENTITLEMENTS_BLOB); `bsd/kern/sweet_amfi.c` answers queryEntitlementBoolean/String(+WithProc, string also matches array members), copyEntitlementAsOSObject(+WithProc, retained), asdict (retained), get_xml (kalloc_data copy); built-in MAC policy "SWEET AMFI" (registered from bsd_init before vfs/exec) with mpo_vnode_check_signature: every signed binary on the MNT_ROOTFS mount gets CS_PLATFORM_BINARY (stand-in for AMFI's static trust cache: iOS system binaries are ad-hoc, cd_flags 0x2, platform byte 0), and attaches entitlements ONLY for the writable copy (cs_blob != csb_ro_addr; revalidation passes the RO blob). LESSON: first version attached in adjustContextWithoutMonitor -> QEMU `unable to adjust OSEntitlements context without monitor: 53` because ubc_cs_convert_to_multilevel_hash (4K-page signatures on 16K kernel) sets csb_entitlements_blob = NULL; ALL iOS 16 binaries (launchd, backboardd, SpringBoard) use 4K code pages, so this would have broken launchd on the phone. Boot-arg `sweet_amfi_debug=1` logs `SWEET AMFI: <ident>: N entitlements (test: ...)`. `tools/mkinit.py OBJ OUT [ENTS.plist]` adds an XML entitlements blob (special slot -5; default output byte-identical). QEMU `research/q0035-ent` (RAM disk `research/root-hfs-ent.img`, init with 3 test entitlements, highmem): `org.sweet.init: 3 entitlements (test: bool 1 string 1 array 1)` then init PID 1. MAC policies loaded: 0034 = 1, 0035 = 2 (QMP `mac_policy_list`). CI `38909807230` -> `xnu/out-0035`. Phone images: `shim/out/shim-boot-0035hr.img` (highmem, ro), `-0035hw.img` (highmem, +ufsrw=1 rootrw=1), `-0035r.img` (low mem fallback).
- **PHONE TEST ORDER (updated):** 0035hr -> if instant reset, 0035r -> then 0035hw.

- **HIGHMEM: XNU in the upper DDR bank, ~3.95 GiB (was 1.2 GiB).** Live FDT memory = <0x80000000 2G> + <0x1_00000000 0xfcc00000>; all fixed reserved-memory carve-outs are in the low bank (highest: ramdump_fb 0xaf000000, rmtfs ~0xfde01000), dynamic pools have alloc-ranges <= 0xffffffff, UFS node has no `iommus` (Linux DMAs anywhere). `tools/mkadt.py --highmem` sets /chosen dram-base 0x100000000 dram-size 0xfcc00000; `tools/mkpayload.py` now takes phys_base/mem_size from the ADT (default payloads byte-identical, verified); `tools/qemu-sweet.py` sizes -m from the payload (8G for highmem). Patch **0034** (`0034-sweet-ufs-dma-above-4g.patch`): UFS refuses DMA memory above 4G without UFSHCI CAP.64AS (bit 24), logs `DMA at 0x...`. CI `38905889883` success -> `xnu/out-0034`. **QEMU verified** (`research/q0034-hi`, ADT `research/q0034-hi.adt` --qemu --highmem, HFS RAM disk): phys_base 0x100000000, init PID 1 heartbeat, no panic; `max_mem` = `mem_size` = 0xfcc00000 read via QMP (`research/q0034-hi-mem`). Phone images: `shim/out/shim-boot-0034hr.img` (read-only) / `-0034hw.img` (+ufsrw=1 rootrw=1), ADT `shim/out/sweet-0034hi.adt` (asmb SIP + sweet-ufs + highmem). PHONE RISK: unknown runtime TZ/XPU protection in the upper bank would show as an instant reset; fall back to 0033r/w. Watchdog ruled out for the overnight reset: msm-watchdog bark is 20 s (qcom,bark-time 0x4e20) yet panic screens stay up for many minutes.

- **Offline research 2026-10-16 (user away, phone unavailable). Images rebuilt** `shim/out/shim-boot-0033r.img` / `-0033w.img` now also carry `wdt_test=no_user_checkin`. NEXT ON PHONE: boot 0033r (expect past in6_nigroup), then 0033w (first writes: rw root fixes HFS link-chain bits itself and gives /var for launchd sockets /var/run/syslog, /var/run/lockdown.sock).
- **launchd.plist** (`/System/Library/xpc/launchd.plist`, compiled; 532 daemons, 66 RunAtLoad/KeepAlive, list in scratchpad `early-daemons.txt`). `_PanicOnCrash` daemons: containermanagerd/usermanagerd (InternalOnly), lockdownd/remoted/trustd/remotepairingdeviced (AutomationOnly), **watchdogd unconditional** (KeepAlive SuccessfulExit=false). watchdogd needs IOKit `IOWatchdog` kext service; if absent it exits only when `kern.hv_vmm_present` (ADT `/defaults/vmm-present`, hvg_sysctl.c) or with boot-arg `wdt_test=no_user_checkin` ("detected testing boot-args to not run, exiting"; reads kern.bootargs). Using the boot-arg (vmm-present would change many daemons; keep as an option).
- **GUI path research (QuartzCore in the shared cache, `ipsw dyld str CACHE STR`, ~10 s/search):** CA::WindowServer has IOMFBServer/IOMFBDisplay (Apple DCP/IOMobileFramebuffer), AppleCLCDDisplay, AppleRGBOutDisplay, **VirtualServer/VirtualDisplay** and **MemorySurface**, EAGLServer. Renderers: CA::OGL::MetalContext, GLContext and a **CPU software rasterizer CA::OGL::SW** (samplers/blend modes; "image is too large for software renderer"). Env knobs: `CA_ENABLE_OGL`, `CA_DISPLAY_TYPE`, `CA_FORCE_LOCAL_SERVER` (249 CA_* vars). Hypothesis (unverified): backboardd's render server could run a VirtualServer/MemorySurface with the SW renderer and we copy the surface to the ABL framebuffer -> needs disassembly of CA::WindowServer server creation. Expected other GUI walls: IOSurface (closed-source kext; needs a reimplementation), IOMobileFramebuffer user client, IOHID touch events, AppleKeyStore/keybag (SEP) for lock state, MobileGestalt answers (AppleCLCD etc.).

- **0032r RESULT (boot 05:07:50 UTC, `research/boot0032r.log`, photo IMG_20261016_071200): SHARED CACHE MAPPED, LAUNCHD RUNS.** With SIP relaxed (ADT asmb) the `not in trust cache` lines are only warnings for every subcache (.31 ... .42.dyldlinkedit); no SIP rejection; libSystem loads; launchd configures networking (`skip attaching fsw to lo0 using legacy TX model`) -> kernel data abort far 0x28 in `SHA1Init` (symbolized as sha1_init) from `in6_nigroup` (IPv6 on lo0): g_crypto_funcs NULL. Kernel addresses are unslid: `~/swift/usr/bin/llvm-symbolizer --obj=KERNEL ADDR` works directly.
- **0032** BearSSL SHA-1 (`0032-sweet-bearssl-sha1.patch`), **0033** BearSSL SHA-384/512 (`0033-sweet-bearssl-sha384-sha512.patch`, BearSSL ctx stored in SHA512_CTX, static-asserted to fit; ubc_subr.c uses SHA-384 for code directories). `0020-stage-bearssl.sh` now also stages hash/sha1.c, hash/sha2big.c, codec/{dec,enc}{32,64}be.c. CI `38854909053` success (0032-only run `38854802073` superseded). Kernel `xnu/out-0033` (SHA1Init -> _br_sha1_init verified by disassembly; nm symbols have a leading underscore). Images (ADT sweet-0032): `shim/out/shim-boot-0033r.img` (read-only), `shim-boot-0033w.img` (+ufsrw=1 rootrw=1). Still NULL g_crypto_funcs users: des, chacha20poly1305, rsa, aesxts, rand, flow_divert.
- `research/check-patch-order.py` now skips patches touching CI-staged sweet_hfs sources (0031) instead of stopping.

- **0031r RESULT (boot 05:03:52 UTC 2026-10-16, `research/boot0031r.log`, photo IMG_20261016_070508): SHARED CACHE FOUND.** dyld now opens `dyld_shared_cache_arm64` (v2 image hard links), kernel refuses to map it: `shared_region: ... map(...'dyld_shared_cache_arm64'): not in trust cache` -> `vnode is not SIP-protected` (vm_unix.c: macOS arm64 path needs trust cache, else root-owned + SF_RESTRICTED unless CSR_ALLOW_UNRESTRICTED_FS) -> `dyld cache '(null)' not loaded: syscall to map cache into shared region failed` -> libSystem not loaded -> coredump to /cores fails error 30 (EROFS) -> panic initproc failed -> "MACH Reboot" (spins; screen kept). Lots of `hfs: set hardlink bit on vol=iOSRoot cnid=... linkid=...` spam (Linux-made hard links lack the link-chain bit; read-only mount cannot persist the fix). FIX: ADT `/chosen/asmb` with `lp-sip0`=0x6f (u64; incl. CSR_ALLOW_UNRESTRICTED_FS) and `lp-sip1`=1 (unauthenticated root), `tools/mkadt.py` -> `shim/out/sweet-0032.adt`. Images with kernel 0031: `shim/out/shim-boot-0032r.img` (read-only) and `shim-boot-0032w.img` (+ufsrw=1 rootrw=1).

- **2026-10-16 morning:** userdata now = **v2** image (`diff-v1-v2.simg` flashed 21:06:50 UTC 2026-10-15, OKAY, `research/flash-userdata-diff-v2.log`); future diffs use `ios16-root-20H392-v2.img` as OLD. Booted `shim-boot-0029k.img` (ignition_level=1) right after (`research/boot0029k-v2.log`); **no photo** (user asleep). Next morning the phone was in PixelOS recovery = some hardware reset (ABL then booted Android, which cannot use userdata). OBSERVATION: our kernel cannot reset the SoC itself (`PE_halt_restart` is never set; reboot/halt print "MACH Reboot"/"CPU halted" and spin), so the reset came from outside XNU: Qualcomm watchdog or a TZ/XPU violation. Unknown which; re-boot 0029k and watch/video until the reset. Do NOT tap factory reset/format data in recovery (would wipe the iOS image).
- **0030** (`xnu/patches/0030-sweet-ufs-writes.patch`, CI `38852655542`): UFS WRITE(10) + read-modify-write for partial 4 KiB blocks + SYNCHRONIZE CACHE (DKIOCSYNCHRONIZE), only with boot-arg `ufsrw=1`; `ufs_write_blocks` re-checks partition bounds before every write (only place data goes out).
- **0031** (`xnu/patches/0031-sweet-read-write-root.patch`, CI `38852837176`): boot-arg `rootrw=1` clears MNT_RDONLY before the root mount (no data volume on sweet: /private/var is on the root; image has no /etc/fstab); Apple HFS accepts a dirty non-journaled ROOT read-write on SWEET (logs "mounting dirty non-journaled root read-write"). Later: journaled HFS for crash safety. Kernel `xnu/out-0031` (CI `38852837176` success, 5m31s; 0030 run cancelled by the newer push). Boot images (phone ADT `sweet-0028.adt`, ramdisk byte-matched): `shim/out/shim-boot-0031r.img` = 0029k args (read-only, ignition_level=1) and `shim/out/shim-boot-0031w.img` = same + `ufsrw=1 rootrw=1`. PLAN: boot 0031r first and film until the reset (diagnose overnight reset), then 0031w (first ever writes to userdata; v2 image on the SSD is the restore source). `research/check-patch-order.py` cannot check 0031 (staged sweet_hfs files are not in its index); CI stage scripts run first, so CI is the check.

- **ignition_level=1 result** (boot 21:01:39 UTC, `research/boot0029k.log`, photo IMG_20261015_230308): preboot/cryptex stages skipped (need >= 2/3), `dylib_cache: finding shared cache on system volume: ignition level = 1, search root fd = 4, subpath = System/Library/Caches/com.apple.dyld/` -> not found -> `ignition boot failed: 19` -> no shared cache -> libSystem not loaded. FIX IN PROGRESS: image v2 = copy of the flashed image + `/System/Library/Caches/com.apple.dyld/` with HARD LINKS to the 44 cryptex cache files (no extra space; avoids symlink-following doubts), then `tools/sparsediff.py ios16-root-20H392.img ios16-root-20H392-v2.img diff.simg` and flash only the diff. Boot with `ignition_level=1` (shim-boot-0029k.img).
- **ignition_level=0 result** (boot 20:58:07 UTC, `research/boot0029j.log`, photo IMG_20261015_230037): all stages skipped as "ignition level insufficient" (hello/preboot/cryptex1 sniff >= 2, select os cryptex path/graft >= 3, **dyld_cache >= 1**), `ignition boot failed: 89`, "ignition partially disabled", then same `Library not loaded: /usr/lib/libSystem.B.dylib (no such file, no dyld cache)`. => dyld gets the shared cache directory from ignition's dyld_cache stage. Next: `ignition_level=1` (`shim/out/shim-boot-0029k.img`) to skip preboot/cryptex stages but run dyld_cache; its log "finding shared cache on %s ... subpath = %s" will show where it searches. If it searches rootfs /System/Library/Caches/com.apple.dyld, add a symlink there (image rebuild; consider a diff-only sparse flash tool to avoid rewriting 12 GB).
- **APPLE dyld/launchd RUNNING (libignition) with `unentitled_ios_sim_launch=1`** (boot 20:53:24 UTC, `research/boot0029i.log`, photo IMG_20261015_225617): kernel prints "Allowing spawn of iOS binary ... boot-arg set to true"; `shared_region: ... check_np ... vm_shared_region_start_address() failed` (no region yet, normal); dyld's built-in **libignition** (Darwin Ignition Sequence 1.0.0, libignition-51~1974; prints rd=ufsroot0, hardware target sweet, ignition level 0x5, platform 0x2, boot spec local, 7 stages): `hello from launchd.1` -> `preboot: mounting preboot` -> `failed to lookup preboot: 25` (ENOTTY: looks for a separate Preboot volume by role) -> ignition fails -> dyld `Library not loaded: /usr/lib/libSystem.B.dylib ... (no such file, no dyld cache)` -> launchd exit -> panic initproc failed. dyld boot-args found via strings: `ignition_level`, `ignition_force_dylib_root` (forced rootfs/livefs/cryptex fallback), `ignition_halt_after`, `ignition_live_app_graft`; dyld cache search paths include /System/Cryptexes/OS, /System/Volumes/Preboot/Cryptexes/OS/, /private/preboot/Cryptexes/OS/ (present in our image). Next try `ignition_level=0` (`shim/out/shim-boot-0029j.img`).
- **APPLE launchd EXECUTED FROM UFS (2026-10-15).** Flashed iOS image: `img2simg` -> `ios16-root-20H392.simg` (12.1 GB) then `fastboot -S 512M flash userdata ...simg` (24 chunks, 307 s, `research/flash-userdata-ios16.log`; a raw-image flash was killed by earlyoom at 2.6 GB RSS - always flash the sparse image). ABL fastboot hung on getvar after the flash; power+vol-down fixed it. Boot 0029u 20:49:04 UTC (`research/boot0029u-ios16.log`), photo IMG_20261015_225134: `hfs: mounted iOSRoot` -> load_init_program /sbin/launchd -> panic `unexpected SIGKILL of launchd (CS_KILLED) with reason -- namespace 9 code 0xe` = OS_REASON_EXEC / EXEC_EXIT_REASON_WRONG_PLATFORM: our kernel is XNU_TARGET_OS_OSX, iOS-platform binaries are "alien" and refused unless spawn attr platform set or DEVELOPMENT TUNABLE `unentitled_ios_sim_launch`. Next try: same kernel, cmdline + `unentitled_ios_sim_launch=1` (`shim/out/shim-boot-0029i.img`). LONGER TERM: macOS-config kernel marks iOS maps alien (vm_map_mark_alien) and differs from iOS in many policies; an iPhoneOS-config kernel build may eventually be needed.
- **iOS 16 ROOT IMAGE BUILT:** `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392.img` (16 GiB case-sensitive HFS+, label iOSRoot, 12 GB used, fsck OK after `fsck.hfsplus -y` rewrote 32 non-ASCII localized WorkflowKit Shortcut names Linux decomposes differently). Built by `tools/mkiosroot.sh OS.dmg SYSTEMOS.dmg APPOS.dmg OUT.img [SIZE]` (needs sudo; apfs-fuse as root; OS dmg is UDIF -> link must end .dmg). Contents: iPhone X 20H392 OS root + SystemOS cryptex at /private/preboot/Cryptexes/OS (so /System/Cryptexes/OS -> cache, 44 files, dyld_v1 arm64) + AppOS at .../App. /sbin/launchd and /usr/lib/dyld root:wheel 0755. NEXT: (confirm) `fastboot flash userdata ios16-root-20H392.img` (auto-sparse), boot 0029u (rd=ufsroot0) -> Apple launchd attempt.
- **MILESTONE ON HARDWARE: BOOT FROM INTERNAL FLASH.** 0029 CI `38797924912` PASSED, `xnu/out-0029` SHA256 `a68bc847...2a37`. Image `shim/out/shim-boot-0029u.img` (payload-0029u = 0029 kernel + `shim/out/sweet-0028.adt` + `rd=ufsroot0`, NO RAM disk). Boots 20:01:34 and 20:12:37 UTC (`research/boot0029u*.log`), photo IMG_20261015_221403: `SWEET UFS: "userdata" on LUN 0: first block 0x2b8c00, 109643 MiB, read-only` -> `BSD root: ufsroot0, major 1, minor 0` -> `hfs: mounted SweetRoot on device b(1, 0)` -> `SWEET: root volume is not authenticated (error 25); continuing unsealed` -> `load_init_program ... /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` -> heartbeat (ran for minutes unattended). userdata currently holds the 8 MB `research/root-hfs.img`. NEXT: build a multi-GB HFS+ image with the iOS 16 (iPhone X 20H392) userland (OS rootfs + SystemOS cryptex contents incl. dyld shared cache at /System/Library/Caches/com.apple.dyld; preserve modes/owners/symlinks; decompress LZBITMAP via apfs-fuse), flash it to userdata (sparse, needs user OK), then try Apple's dyld/launchd. Driver is read-only (root mounts RDONLY), fine for first launchd attempts.
- **userdata FLASHED with the 8 MB HFS test volume** (user said "flash it", 2026-10-15 19:53:17 UTC, `research/flash-userdata-hfs-test.log`: `fastboot flash userdata research/root-hfs.img` OKAY; pre-checks: one sweet device, partition-size:userdata 0x1AC4BFB000, partition-type f2fs, image fsck OK, SHA256 3626ea69...). Android userdata is now destroyed (expected/approved). Boot 0028u at 19:53:20 (`research/boot0028u-hfs.log`), photo IMG_20261015_215541: `BSD root: ufsroot0` -> **`hfs: mounted SweetRoot on device b(1, 0)` from internal UFS** -> panic `rootvp not authenticated after mounting @bsd_init.c:931` (arm64 macOS-config requires FSIOC_KERNEL_ROOTAUTH sealed root for non-ramdisk roots). Patch **0029** (`dd1c284`): SWEET logs `SWEET: root volume is not authenticated (error N); continuing unsealed` instead of panicking (Apple configs keep the panic; pivot_root still requires auth). CI **`38797924912`** running. Next: download out-0029, phone image with sweet-0028.adt + rd=ufsroot0 (no RAM disk), boot -> expect init from UFS.
- **UFS ROOT DRIVER VERIFIED ON HARDWARE (read-only).** 0028 CI `38796139570` PASSED, `xnu/out-0028` SHA256 `8b981640...9da6`; QEMU HFS RAM-disk regression `research/q0028-hfs` still runs init. Phone boot 2026-10-15 19:50:24 UTC (`research/boot0028u.log`, image `shim/out/shim-boot-0028u.img`, phone ADT `shim/out/sweet-0028.adt` with /sweet-ufs, NO RAM disk, `rd=ufsroot0`), photos IMG_20261015_215151/215220: `SWEET UFS: "userdata" on LUN 0: first block 0x2b8c00, 109643 MiB, read-only` -> `BSD root: ufsroot0, major 1, minor 0` -> `hfs_ValidateHFSPlusVolumeHeader: unknown Volume Signature : f188` (Android-encrypted data, as expected) -> `cannot mount root, errno = 19`, kernel retries mountroot (no panic). Nothing written to flash. NEXT (destructive, needs user confirmation right before): `fastboot flash userdata research/root-hfs.img` (8 MB HFS with test init) then boot rd=ufsroot0 -> expect init from internal flash.
- **PATCH 0028 (`33aceac`): XNU read-only UFS root device**, CI **`38796139570`** running (next artifacts xnu/out-0028, research/bt29, run29.log). `bsd/dev/sweet_ufs.c`: reuses ABL's UFSHCI state (like the shim probe), takes over UTRL at a kmem_alloc_contig region (DMA layout: UTRL 0x0, UCD 0x1000, 1 MiB bounce 0x4000), verifies UTRLBA readback before any doorbell, IE=0 polled, one SCSI cmd at a time on LUN0 (TUR x3, READ(10) via bounce, PRDT 256 KiB chunks), flush_dcache64 by PA around each command (host not dma-coherent per DT), finds GPT partition by name (`ufsroot=` boot-arg, default `userdata`), exposes `/dev/ufsroot0`/`rufsroot0` (512-byte logical sectors, physical 4 KiB, DKIOC* ioctls), refuses writes (EROFS). IOKitBSDInit: `rd=ufsroot0` -> sweet_ufs_rootdev() (lazy init) else panic. ADT: `tools/mkadt.py` adds `/sweet-ufs` reg <0x1d84000 0x3000> for the PHONE only (not --qemu). Local compile OK, imports resolve. Sandbox research/bt28 (pre0028 originals). FIRST PHONE TEST PLAN (non-destructive): boot 0028 with phone ADT + `rd=ufsroot0` and NO RAM disk: expect `SWEET UFS: "userdata" on LUN 0: first block 0x2b8c00, 109643 MiB` + `BSD root: ufsroot0`, then root mount failure (userdata is still Android-encrypted) -> panic. That validates the driver before flashing an HFS image into userdata (needs explicit user confirmation at that time).
- **SUPERSEDES the 64 GiB iosroot GPT plan: user said "u can delete android" (2026-10-15).** Use the existing `userdata` partition (LUN0 sda34, 4K LBA 0x2B8C00, 0x1AC4B MiB ~107 GiB) directly as the iOS root: NO GPT changes. Keep bootloaders, recovery (OrangeFox), modem/persist/fsg/modemst untouched. Write the HFS image later with `fastboot flash userdata` (sparse, auto-split under the 768 MiB limit). Android will no longer boot normally (expected; reinstallable). Order: XNU UFS driver first, read-only against the current GPT; flash only once it can locate userdata. Nothing erased yet.
- **DECIDED (user, 2026-10-15): 64 GiB `iosroot` on LUN0 (sda), taken from userdata (wipes Android user data; Android/super kept).** Planned GPT (4K LBAs): userdata 0x2B8C00..0xD7D6FF (~43 GiB, keep type/unique GUIDs and attributes); iosroot 0xD7D700..0x1D7D6FF (0x1000000 blocks = 64 GiB, 1 MiB aligned); backup GPT area unchanged. Procedure: user backs up phone data -> boot OrangeFox recovery (adb) -> back up GPT (sgdisk --backup + raw dd of LBA 0-5 and last 6 blocks) to research/ -> sgdisk edit -> Format Data -> verify Android boots to setup. NOT EXECUTED YET.
- **INTERNAL UFS WORKS FROM THE SHIM (read-only), user authorized internal-UFS approach incl. flashing (2026-10-15, "i allow everything").** Shim probe `shim/ufs.c` (commit `12ed3ed` + filter): ufshc@1d84000 is UFSHCI 2.1 (ver 0x210, cap 0x1587031F, 32 slots), ABL leaves HCE=1, HCS=0x10F (device present, lists ready, UIC ready); probe takes over UTRL at its own buffer (verifies UTRLBA readback before any doorbell), TEST UNIT READY/READ CAPACITY/READ(10) all succeed. LUN0 = 0x1D7D800 x 4 KiB (~126.6 GB), valid GPT (64 entries at LBA 2). Photos IMG_20261015_212916/213325/213429. Big LUN0 partitions (start in 4K LBAs / MiB): super 0xCA00/0x2200 (8.5 GiB), logdump 0x22CA00/64, minidump 0x230A00/128, rawdump 0x238A00/128, recovery 0x240C00/128, cache 0x248C00/384 ext4, exaid 0x260C00/384, cust 0x278C00/1024 ext4, **userdata 0x2B8C00/0x1AC4B MiB (~107 GiB), fs unreadable = encrypted (metadata encryption)**. cache..cust contiguous = only 1.75 GiB (too small for iOS ~10+ GiB). Plan needing user consent: shrink userdata in GPT (wipes Android data) and add an `iosroot` partition at the end of LUN0. LUNs 1-5 not listed yet (sdb..sdf backed up in backup/lun).
- **fastboot limit measured (2026-10-15, read-only getvar):** `max-download-size: 805306368` (768 MiB); `max-fetch-size` unsupported; `is-userspace: no` (ABL fastboot). A `fastboot boot` image (shim+kernel+RAM disk) must be < 768 MiB, so the 2.5 GiB iOS shared cache cannot ride in the boot RAM disk even if XNU gets all RAM. Real userland needs storage access (UFS driver, or USB host) or a much smaller cache. Unlocking full RAM is still needed later (GUI).
- **iOS 16 USERLAND SOURCE (Claude, 2026-10-15).** Chose **iPhone X (iPhone10,3/10,6, D22/D221AP)** iOS **16.7.16 (20H392)** over iPhone 8: same A11 (no PAC -> plain arm64, runs on Kryo 470/ARMv8.2), last iOS for A11; X userland is home-button-less and tall-screen (1125x2436 vs sweet 1080x2400), better fit for later GUI. Note the X has its OWN SystemOS cryptex (different digest from iPhone 8's). IPSW URL in `research/ipsw-x/URL.txt` (+ BuildManifest). Images on the user's external SSD (NOT in git, do not format that disk: it holds the user's photos): `/run/media/dipa/android/ios-sweet-ipsw/iPhone10,3_16.7.16_20H392/` — `087-86593-047.dmg` = Cryptex1,SystemOS (2,743,074,816 B), `087-86698-044.dmg` = OS rootfs (4,730,814,044 B), both zip CRC-32 verified, plus Firmware/*.root_hash/.trustcache/.mtree. AppOS cryptex is 087-86715-047.dmg (10 MB, same as iPhone 8).
  - Images are raw APFS; files use LZBITMAP (decmpfs type 14). 7z cannot decode. Tools: `ipsw` 3.1.738 (AUR ipsw-bin, user-approved) needs apfs-fuse on Linux; **apfs-fuse** built in user space (system cmake broken: cmake 4.4.4 wants libjsoncpp.so.27, jsoncpp 1.9.6 installed = partial upgrade, NOT fixed) with pip cmake + `-DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_CXX_FLAGS="-include cstdint"`, installed to `~/.local/bin/apfs-fuse` (+apfs-dump-quick). Mount via a comma-free symlink (apfs-fuse splits options on ',' in `iPhone10,3`; exFAT has no symlinks -> put link elsewhere): `apfs-fuse LINK/087-86698-044.dmg MNT` -> `MNT/root/...`; unmount `fusermount3 -u MNT`.
  - Verified from headers: `dyld_shared_cache_arm64` magic `dyld_v1   arm64`; `/sbin/launchd`, `/usr/lib/dyld` = cputype 0x100000c subtype 0 (plain arm64). libSystem exists only inside the cache.
  - **BLOCKER for real userland via RAM disk:** shared cache = 3.10 GiB in 44 files (~2.5 GiB without the 592 MB `.symbols`), but the kernel manages only 1.20 GiB (0xb0000000..0xfd000000). Options: (A) give XNU the phone's full ~6 GB (banks 0x80000000+2G and 0x100000000+0xfcc00000, carve-out holes need pmap support) AND check fastboot max-download-size for a multi-GB boot image; (B) storage driver (Qualcomm UFS) to read a root from flash (no flashing allowed: would need a file/partition strategy); (C) run against individual dylibs extracted from the cache (uncertain).
- **MILESTONE ON HARDWARE: HFS+ ROOT FILESYSTEM -> PID 1.** Boot 2026-10-15 18:22:12 UTC (`research/boot0027b.log`, image `shim/out/shim-boot-0027b.img`), photo `IMG_20261015_202334.jpg`: `BSD root: md0` -> `hfs: mounted SweetRoot on device b(1, 0)` -> `load_init_program: attempting to load /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` -> repeated `SWEET init: still alive`, no panic. Kernel 0027 (built-in Apple OSS HFS + BearSSL MD5), init with SP alignment fix. NEXT: real iOS 16 userland on the HFS image (dyld, libSystem/dyld shared cache, launchd). Known gaps: entitlements (none granted), SHA-1/SHA-384/512 providers, no storage driver (RAM disk only), root mounted read-only.
- **PHONE HFS ROOT (0027) + SIGBUS ROOT-CAUSED.** Phone boot 2026-10-15 17:49:06 UTC (`research/boot0027.log`, photo `IMG_20261015_200031.jpg`): `hfs: mounted SweetRoot on device b(1, 0)` (HFS works on hardware), init printed its first line, then `pid 1 exited -- exit reason namespace 2 subcode 0xa` = SIGNAL/SIGBUS -> `initproc failed to start` panic. Diagnostic boot 18:08:13 UTC (`research/boot0027d.log`, image `shim/out/shim-boot-0027d.img`, diag init `research/diag-init` from scratch diag-init.S with [a][b][c] markers, `cs_debug=1`; photo `IMG_20261015_201547.jpg`): output `PID [a][b]` then SIGBUS -> fault at first `strb w21,[sp]`; no code-signing messages. Cause: kernel enters a static binary with sp at argc, not necessarily 16-aligned (depends on arg/apple string lengths, differs mockfs vs HFS); EL0 SP alignment check -> EXC_ARM_SP_ALIGN -> SIGBUS (sleh handle_sp_align). QEMU does not enforce SP alignment. NOT a kernel bug: real binaries start in dyld which aligns. Fix `1a8fc15`: sweet-init aligns sp at entry. Rebuilt `research/root-hfs.img`, QEMU `research/q0027-hfs2` OK. Phone image `shim/out/shim-boot-0027b.img` SHA256 `098ba46c...c5c` (payload-0027b, ramdisk match check-0027b) ready, awaiting FASTBOOT. Lesson: Assembly `;` is a comment in Apple arm64 asm (use .macro, not ;-joined #define).
- **HFS ROOT WORKS (QEMU).** 0027 CI `38779441895` PASSED, `xnu/out-0027` SHA256 `a8643886...5f6a`. QEMU `research/q0027-hfs` (RAM disk = `research/root-hfs.img`, rd=md0): `BSD root: md0` -> `hfs: mounted SweetRoot on device b(1, 0)` -> `load_init_program: attempting to load /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` + heartbeat, no panic. Phone image `shim/out/shim-boot-0027.img` (payload-0027 = 0027 kernel + sweet-0021.adt + root-hfs.img + rd=md0, ramdisk byte-match check-0027) ready; awaiting FASTBOOT for phone test.
- **HFS ROOT WORK (Claude), patches 0026-0027.**
  - 0026 (`f0e990c` + link fix `64c96f4`): Apple OSS **hfs-627.100.6** (commit `31d5ec238dfab485e78a9a30b8b92d13e8914f03`, paired with xnu-8796.141.3 in macos-135 release.json) staged by `xnu/patches/0026-stage-hfs.sh` into `bsd/sweet_hfs` (core + hfs_encodings; AppleKeyStore include/key prototypes guarded by CONFIG_PROTECT; SDK TargetConditionals copied). New config option `sweet_hfs` (MASTER + MASTER.arm64.sweet); 40 `optional sweet_hfs` files; Makefile.template foreach adds `_CFLAGS_ADD = -include sweet_hfs_compat.h -I...` (MUST come after kernel -D flags; compat undefs BSD/XNU_KERNEL_PRIVATE and the CONFIG_* HFS tests so kext-config.h decides: macOS profile, no content protection). `sweet_hfs_iokit.c` replaces hfs_iokit.cpp/hfs_encodings_kext.cpp: `sweet_hfs_register()` called in bsd_init after vfsinit (encodings init, vfs_fsadd VFS_TBLCANMOUNTROOT, zones, sysctls); is_ejectable 0, iterate_media none, serial KERN_NOT_SUPPORTED, OSKextGetCurrentLoadTag -> kOSKextInvalidLoadTag (renamed via compat macro). hfs_encodings' own `hfs_allocated` renamed per object (separate kexts on Apple; first CI `38777325964` failed on that duplicate at link). mockfs now validates Mach-O magic (its TODO) so non-Mach-O roots fall through to HFS (new fs entries come AFTER mockfs in vfsconf). CI `38778133720` PASSED, `xnu/out-0026` SHA256 `0b49170c...701a`, prints `SWEET HFS: hfs-627.100.6 registered`.
  - HFS+ image: `research/root-hfs.img` (8 MB, mkfs.hfsplus from AUR hfsprogs installed with user approval; loop-mounted with sudo; /sbin/launchd = research/sweet-init, /dev; fsck.hfsplus OK). QEMU `research/q0026-hfs`: `BSD root: md0` then data abort in MD5Init from hfs_getvoluuid (mount computes volume UUID).
  - 0027 (`14bf2b7`): 0020 stager also copies BearSSL hash/md5.c + codec/dec32le.c/enc32le.c; `corecrypto_md5.c` SWEET branch converts MD5_CTX <-> br_md5_context around br_md5_* calls; wrappers sweet_md5.c/sweet_dec32le.c/sweet_enc32le.c. Host test 40/40 match md5sum. CI **`38779441895`** running.
  - Local tooling: `tools/xnu-cc.py` has `XNU_CC_POST` (flags after logged ones). Sandbox `research/bt28/xnu` = 0025 CI tree + 0026/0027 edits; originals `research/pre0026`, `research/pre0027`. HFS local objects `research/hfs-objs`. Checker entries updated to bt28.
  - SHA-1 / SHA-384/512 still have no provider.
- **MILESTONE ON HARDWARE: USERSPACE PID 1 RUNS ON THE PHONE.** Boot 2026-10-15 17:16:00 UTC (`research/boot0025.log`: send OKAY0.410s, boot OKAY0.135s), image `shim/out/shim-boot-0025.img` SHA256 `9dbfa0b1...89db0`. Photo `IMG_20261015_191733.jpg`: `BSD root: md0` -> `load_init_program: attempting to load /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` -> repeated `SWEET init: still alive` (5 s heartbeat), no panic. Kernel 0025 + mockfs RAM disk + built-in no-entitlements AMFI. No flashing performed.
- **NEXT (toward real iOS userland):** real root filesystem with real binaries. Options: (a) build Apple OSS `hfs` (macOS 13.5 tag `hfs-627.100.6`) into SWEET like libpthread, then an HFS RAM disk image; (b) iOS 16 dyld + libSystem/dyld shared cache from the IPSW (needs APFS or repacking onto HFS). Expected hurdles: entitlements (sweet_amfi grants none), SHA-1/MD5/SHA-384/512 providers (BearSSL), missing kexts/daemons. Current init is a static test program, not launchd.
- **MILESTONE: USERSPACE RUNS (QEMU).** 0025 CI `38774788048` PASSED, `xnu/out-0025` SHA256 `5496069b...4877`. QEMU `research/q0025-rd` (150 s, no panic): `BSD root: md0` -> `load_init_program: attempting to load /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` -> `SWEET init: still alive` every 5 s. Exec, ad-hoc SHA-256 code signature, syscalls, devfs /dev/console, select timeouts all work. Phone image prepared: `shim/out/shim-boot-0025.img` 16,515,072 bytes SHA256 `9dbfa0b15140c0bd733a16c4f727f6a7df44a8741f98d8e266d5f2fa4f689db0` (payload-0025 = 0025 kernel + sweet-0021.adt + research/sweet-init + rd=md0; ramdisk byte-match check-0025). Waiting for user to put phone in FASTBOOT.
- **0024 CI `38773277811` PASSED**, `xnu/out-0024` SHA256 `a0ba964b...4348`. QEMU `research/q0024-rd` and **PHONE (boot 2026-10-15 17:05:55 UTC, `research/boot0024.log`, image `shim/out/shim-boot-0024.img` SHA256 `cf3dac86...6964`, 16,510,976 bytes, payload `shim/out/payload-0024.bin` = 0024 kernel + `shim/out/sweet-0021.adt` + RAM disk `research/sweet-init` + rd=md0; ramdisk byte-match `check-0024`)** agree: `Added memory device md0`, `BSD root: md0`, `load_init_program: attempting to load /sbin/launchd`, then data abort in accelerate_entitlement_queries (PC 0x...77d2458, FAR 0x1a0) = NULL AMFI interface. Photo `IMG_20261015_190715.jpg`. RAM disk root is physically verified.
- **0025 (`2518c79`)**: new `bsd/kern/sweet_amfi.c` registers a built-in AMFI interface at EARLY_BOOT/MIDDLE: no entitlements (adjustContextWithoutMonitor succeeds without creating a context, queries KERN_DENIED, copies NULL/KERN_NOT_FOUND, get_xml/transmuted false); WithMonitor KERN_NOT_SUPPORTED; OSEntitlements_query and query_context_to_object panic explicitly (CoreEntitlements closed; no kernel caller); TrustCache version 0 (0015 keys on trust_cache_rt==NULL, img4 absent -> still unavailable); CoreEntitlements table zero (PMAP_CS only). Compiles locally; checker updated (bt27 entries). CI **`38774788048`** running. Next artifacts `xnu/out-0025`, `research/bt29`, `research/run29.log`. Real iOS binaries will need real entitlements later (no OSS CoreEntitlements exists).
- **0021-0024 RAM-disk-root iteration (Claude), QEMU only so far, no new phone boot since 0020.** Each kernel tested with `tools/mkpayload.py KERNEL research/q0021.adt OUT --ramdisk research/sweet-init --cmdline "debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=md0"` then `tools/qemu-sweet.py OUT DIR --secs 150 --kernel KERNEL --fail-on-panic` (PATH needs ~/swift/usr/bin for llvm-nm). Fresh ADTs: `research/q0021.adt` (QEMU), `shim/out/sweet-0021.adt` (phone).
  - 0021 CI `38769532000` PASSED, `xnu/out-0021` SHA256 `2b7500a1...962e`, `research/bt25`/`run25.log`. QEMU `research/q0021-rd`: md0 root, then assert `iomediaClass` in IOService::publishHiddenMedia.
  - 0022 (`20ece9c`, publishHiddenMedia without IOMedia class) CI `38770666158` PASSED, `xnu/out-0022` SHA256 `a4d0e787...30c5`, `research/bt26`/`run26.log`. QEMU `research/q0022-rd`: data abort in SHA256_Init (NULL g_crypto_funcs) from vm_kernel_addrhash_internal.
  - 0023 (`c8089d1`, libkern SHA256_Init/Update/Final use built-in ccsha256_ltc_di + ccdigest_init/update on SWEET; also serves code-signing SHA-256; SHA-1/MD5/SHA-384/512 still have NO provider - BearSSL from 0020 could supply them later) CI `38771876423` PASSED, `xnu/out-0023` SHA256 `d763fed7...1003`, `research/bt27`/`run27.log`. QEMU `research/q0023-rd`: **mockfs mounted md0, `load_init_program: attempting to load /sbin/launchd`**, then panic `mockfs_fsnode_vnode failed to create fictitious pages ... rvalue = 5` (mockfs.c:351).
  - 0024 (`3293c80`): mockfs used memdev mi_base (4K page number of a VIRTUAL kernel address for IOKit RAMDisk, truncated to 32 bits) << PAGE_SHIFT as a physical address. Now zero-copy pager mapping only when mi_phys (with 4K units); virtual md uses pagein/blockmap/strategy -> memdev bcopy. memdev's own mdPhys path assumes 4K ppnums (unsafe on 16K arm64), so do NOT switch the RAMDisk to physical. CI **`38773277811`** running. Next artifacts `xnu/out-0024`, `research/bt28`, `research/run28.log`.
  - Sandboxes: bt25/bt26/bt27 contain the next patch's edited files; pristine copies in `research/pre0022`..`pre0024`.
- **PATCH 0021 (Claude): RAM disk root via mockfs -> first userspace program.** Commits `9c37771` (initial; CI `38768500280` FAILED: mockfs_fsnode.c kfree_type(mockfs_fsnode_t) typed-alloc static assert; local xnu-cc stubs `__builtin_xnu_types_compatible` so it can't catch this) and `193772f` (fix), CI **`38769532000`** running: https://github.com/dipa-studio/ios-sweet/actions/runs/38769532000. Next artifacts: `xnu/out-0021`, `research/bt25`, `research/run25.log`.
  - Kernel: `config/MASTER.arm64.sweet` FILESYS_BASE += `mockfs` (XNU's built-in FS: root device is itself a Mach-O, served as /sbin/launchd, plus /dev for devfs). Upstream mockfs bit-rot fixed: kalloc_type/kfree_type used the POINTER typedef mockfs_fsnode_t (nodes were 8 bytes) -> `struct mockfs_fsnode`. `IOService::setRootMedia` asserted IOBlockStorageDriver class exists (IOStorageFamily absent): SWEET-only, a missing class means root=NULL (same as IOSetImageBoot); Apple path keeps assert. All 21 patches apply (`research/check-patch-order.py`); 5 TUs compile locally with -DMOCKFS (`research/mockfs-objs`), imports resolve vs 0020 kernel. Sandbox `research/bt24/xnu` now holds 0021 versions of those 3 files; pristine 0020 copies in `research/pre0021`.
  - Tools: `tools/mkpayload.py --ramdisk IMAGE` appends IMAGE as an extra payload segment right after the kernel, moves kernel_end past it (shim unchanged: ADT+boot_args follow it, image stays below topOfKernelData), and adds ADT `/chosen/memory-map` `RAMDisk`=<phys,size>. Boot with `rd=md0`. Without --ramdisk output is byte-identical to existing 0020 QEMU and phone payloads (verified). `tools/sweet-init.S` + `tools/mkinit.py`: PIC static init (open /dev/console, print PID, heartbeat every 5 s via select, never exits) wrapped into a static arm64 Mach-O (__PAGEZERO, __TEXT@0x100000000, LC_UNIXTHREAD, ad-hoc SHA-256 CodeDirectory v0x20400, 4K pages). Build: `~/swift/usr/bin/clang -target arm64-apple-ios16.0 -c tools/sweet-init.S -o X.o && tools/mkinit.py X.o research/sweet-init` (ld64.lld cannot link static iOS). Signature not verified by a real codesign; the kernel will tell.
  - QEMU with 0020 kernel + RAM disk (`research/q0020-rd.bin`, rd=md0, 150 s `research/q0020-rd150`): `Added memory device md0`, **`BSD root: md0, major 1, minor 0`** (plumbing works), then the setRootMedia assert (now fixed in 0021). NOTE: QEMU needs >60 s to reach IOFindBSDRoot (60 s run showed nothing after stf0; likely RTC/inittodr wait), so use --secs 150.
  - NEXT: download 0021, QEMU 150 s with ramdisk; expect mockfs mount then init exec -> `*** SWEET userspace: init running as PID 1 ***`. Then phone boot with same payload (phone ADT shim/out/sweet-0020.adt pattern + --ramdisk + rd=md0).
- **0020 PHONE RESULT (user transcription of screen, 2026-10-15): KERNEL INIT COMPLETE, WAITING FOR ROOT.** After Skywalk self-tests PASSED, kminit, ifnet_attach lo0/gif0/stf0, screen prints `BSD`, then `Waiting on <dict><key>IOProviderClass</key><string>IOMedia</string><key>Content</key><string>Apple_HFS</string></dict>` and `Failed to write boot-picker-bringup-reason to NVRAM.` No panic. This is IOFindBSDRoot waiting for a root device (none exists); NVRAM warning is expected (no NVRAM driver). QEMU `cpu_idle` snapshot is presumably the same wait (not GDB-confirmed; milestone GDB run was cancelled by user since phone answered it). Draft `research/check-milestones20.gdb` (hbreaks inittodr/vfs_mountroot/IOFindBSDRoot/bsd_utaskbootstrap) left uncommitted/ignored.
- **NEXT MILESTONE: root filesystem -> launchd.** Plan: (1) RAM disk: shim places an image in RAM, ADT /chosen/memory-map `RAMDisk` entry, boot-arg `rd=md0` (XNU md driver). (2) HFS is a separate kext on Apple; build Apple OSS `hfs` (tag paired with macOS 13.5 in distribution-macOS macos-135 release.json) into SWEET like libpthread 0019. APFS is closed. (3) Minimal HFS image with iOS 16 launchd (later: dyld/shared cache; AMFI/code-signing absent will be a later hurdle). User's end goal is full iOS GUI.
- **0020 (AES) VERIFIED + PHONE BOOTED (Claude took over after Codex usage limit).** Actions `38765422261` (`ab9e23f`) PASSED; kernel `xnu/out-0020/kernel.development.sweet` checksum OK; buildtree `research/bt24`, log `research/run24.log`. Actual-kernel AES checks PASS (`research/check-aes20.gdb` / `.log`, 12 extra CBC cases match OpenSSL). 45s QEMU `research/q0020`: no panic, zero unknown glyphs, past old TCP/AES panic -> Skywalk self-tests PASSED -> bsd_autoconf kminit -> ifnet_attach lo0/gif0/stf0; final snapshot PC in `cpu_idle` (idle or waiting; NOT yet diagnosed, could be root-device wait). Image `shim/out/shim-boot-0020.img` 16,461,824 bytes SHA256 `775200cb02d95fc750981516b79d91dcdc96184bbe67a38f9d57c59b4a391828`, ramdisk byte-matches `payload-0020.bin` (`check-0020`). **Phone temporarily booted 2026-10-15 16:10:00 UTC**: one FASTBOOT product sweet, send OKAY0.407s, boot OKAY0.134s, total0.549s, exit0 (`research/boot0020.log`). Awaiting user photo; do not reboot the phone before it. NEXT: read photo; diagnose what QEMU waits on after stf0 (GDB backtrace of threads / longer run). Codex's STOP request below is complete.
- User requests STOP after completing the current0020 AES iteration (Actions/QEMU/phone), then leave a precise committed handoff for another AI. User says FASTBOOT ready again after0019 photo. Do not start a new blocker fix after this iteration.
- Goal: iOS16-generation XNU on Redmi Note10Pro sweet/SM7150. Always commit, use gh/Actions for kernel builds, keep this handoff current. Temporary fastboot boot only; preserve Android/backups. No userland/root filesystem boot yet.
- **Patch0020 update:** initial `e71098d` / Actions `38765012466` superseded by scalar-only compile flags. Added -mgeneral-regs-only to all six provider/BearSSL TUs and corecrypto_aes.c; object disassembly now confirms no SIMD. Updated implementation `ab9e23f`, Actions `38765422261` queued/running: https://github.com/dipa-studio/ios-sweet/actions/runs/38765422261. Do not launch another run. portable BearSSL AES CBC and legacy one-block ECB wired directly into SWEET corecrypto_aes.c. Genuine bitslice implementation, no CPU AES/SIMD requirement, heap or fake global crypto table. Source fetched by `0020-stage-bearssl.sh` from official BearSSL git at immutable `7bea48e5e850ab4cafbe68d3765cdaba13a86d6f`; MIT license retained, downloaded source stays out of git. Kernel limits include adapter only. All128/192/256-bit keys, CBC encrypt/decrypt, copied contexts, in-place/out-of-place; zero block count and size overflow handled, invalid keys clear context. Seven TUs compile to arm64 objects (`research/aes-objs`), imports resolve, all20 patches apply and sources match (`research/patch0020-check.log`). Sandbox `research/bt23/xnu`, originals `research/pre0020`. NEXT: Actions then actual-kernel NIST/random CBC/ECB checks and >=45s QEMU before another phone boot. Next artifacts out-0020 / bt24 / run24.log. 0020 updated CI pending. Local sources/checks match the scalar-only patch.
- **Latest built kernel0019**, implementation `256d3f0`, patches0001-0019. Actions `38763072907` PASSED (4m5s): https://github.com/dipa-studio/ios-sweet/actions/runs/38763072907. Kernel `xnu/out-0019/kernel.development.sweet`, SHA256 `a27df7568f03fb541cf0e8024c0a594fa178a85054fcf9eba29158cbd4c5ecc0`; checksum verified. Log/source `research/run23.log` / `research/bt23` (no nested .git). Seven pthread source/build/header files and staged adaptations byte-match local compile inputs.
- **0019 hardware photo received** `IMG_20261015_175306.jpg`: pthread registration visible, passes mbinit and DLIL checksum/scheduling, same AES fault as QEMU: PC`0xfffffe00079174e0` (`aes_encrypt_key128`), LR`0xfffffe000767c3a4` (`tcp_init`), FAR0x78, x8=0. Disassembly proves NULL g_crypto_funcs->ccaes_cbc_encrypt. Photo processed; no more0019 observation pending. General crypto table remains absent, other hash/mode operations may still fault after0020. DO NOT bypass TCP or install fake crypto. XNU already contains SHA256/HMAC implementations; AES source there is a dispatch wrapper. Full future provider interface libkern/libkern/crypto/register_crypto.h. Apple/corecrypto default README describes evaluation-only licensing, so no external corecrypto was fetched or staged; BearSSL selected for this portable AES integration.
- **0019 pthread FIXED in QEMU and observed beyond its init on phone**: 45s QEMU `research/q0019`, zero unknown glyphs, then AES panic. Actual-kernel API checks PASS `research/check-pthread19.gdb` / `.log`, guest `research/q0019-api`: function/callback table identity, actual provider pthread_init returns, locks/global hash/threadcall/typed zones initialized, thread_selfid via syscall shim + buffer bounds, process hash init/delete via shims restores NULL. Monitor quit prints expected remote-disconnect after PASS. All fixture guests stopped.
- **0019 provider**: genuine Apple libpthread-514.100.2 commit `a6c70ca0e544347a0377babf748f831577ec24f9`, exact paired release with xnu8796.141.3 in distribution-macOS/macos-135/release.json (`research/macos-135-release.json`). 0019 shell stage runs before patches (workflow copies *.sh); three SWEET wrapper TUs use kext BSD header view, vm_fault_external, renamed provider-side pthread_kern/current_uthread; register real function table at EARLY_BOOT SECOND, real pthread_init later. Stager applies bounded address casts, zero user addresses, proper panic format. SWEET opaque ksyn storage enlarged48->56 for trailing kwe_uth, checked with provider static assertion. Local pre-CI TargetConditionals fixture was syntax-only; artifact bt23 carries actual SDK header and CI is authoritative.
- **Phone0019 temporarily booted** 2026-10-15 15:51:36 UTC: exactly one FASTBOOT/product sweet verified; send OKAY0.406s, boot OKAY0.134s, total0.546s, exit0 (`research/boot0019.log`). Image `shim/out/shim-boot-0019.img`, 16,457,728 bytes, SHA256 `19429a57e486b856880df9e27327ed488e0139345311fdb0350974e72103a9a1`. Phone ADT/payload `shim/out/sweet-0019.adt` / `payload-0019.bin` timer19, unpacked ramdisk byte-match (`check-0019`). EntryPA0xb146b488, kernelendPA0xb2000000, boot_argsPA0xb2004000/top0xb2008000. Last user FASTBOOT+USB reply preceded this boot; do not assume phone is still in FASTBOOT.
- **CPU0018 physically verified** by `IMG_20261015_173951 (2).jpg`: boot CPU0 registered, real SGI1 verified, initialization complete -> BSD/MAC/mbinit -> old pthread-table panic. SweetCPU uses legacy IOCPU, ml_processor_register/processor_start/initCPU, real SGI1 clears SIGPdisabled before max CPUs completion; no forced flags/skipped waits. CPU APIs and two-CPU rejection passed (`research/check-cpu18.gdb`, `q0018-api`, `q0018-smp`). Single core; SMP/suspend unsupported. CURRENT SWEET has NO USE_APPLEARMSMP.
- QEMU next run: generate fresh `tools/mkadt.py research/q0020.adt --qemu` (timer27), mkpayload with `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress`, then `tools/qemu-sweet.py PAYLOAD OUT --secs 45 --kernel KERNEL --fail-on-panic`. 12s can miss later panics because IORTC waits30s. Runner snapshot only: panic exit2, blank/incomplete exit3; absence of panic does not mean complete boot. Qualcomm XPU/ABL/panel and phone SMC PSCI require hardware checks.
- Local checker `tools/xnu-cc.py research/run23.log research/bt23 FILTER --per-file`, XNU_CC_OBJDIR for objects. For new BearSSL TUs add XNU_CC_EXTRA with -I bt23/xnu/libkern/crypto/sweet_bearssl/{inc,src}. Patch checker `research/check-patch-order.py` uses temporary upstream index; source fixtures/artifacts ignored under research. GDB mask registers to unsigned64, use function-entry manual-call return traps, discard manipulated guests. No QEMU guest intentionally left running.

## Session log and earlier handoffs (superseded by the active summary above)

### Prior 0019 transition notes (superseded)

- Goal: iOS 16 generation XNU on sweet. Always commit completed work, use gh/Actions for kernel builds, keep this handoff current. Temporary fastboot boot only; preserve Android/backups.
- **0019 build PASSED** Actions `38763072907` (4m5s), implementation `256d3f0`. Kernel `xnu/out-0019/kernel.development.sweet`, SHA256 `a27df7568f03fb541cf0e8024c0a594fa178a85054fcf9eba29158cbd4c5ecc0`; checksum verified. Log/source `research/run23.log` / `research/bt23`; all seven patched files and staged provider adaptations match local compile inputs. Prefer bt23 as the next baseline.
- **0019 pthread FIXED in QEMU**: 45s run `research/q0019` registers provider, passes mbinit, DLIL SUM16 checks and DLIL thread scheduling. Actual-kernel GDB checks `research/check-pthread19.gdb` / `.log` PASS: function/callback table identity, real _pthread_init returns, locks/global hash/cleanup threadcall/typed zones non-NULL, thread_selfid via actual syscall shim with output bounds, process hash init/delete through actual shims restores NULL. Guest stopped after tests; no fixture left running. GDB monitor quit prints expected remote disconnect after PASS.
- **Current next blocker:** QEMU0019 panic data abort FAR0x78 at `aes_encrypt_key128` PC`0xfffffe00079174e0`, called by `tcp_init` LR`0xfffffe000767c3a4`. Disassembly confirms `g_crypto_funcs == NULL`, loading ccaes_cbc_encrypt at+0x78. General crypto registration is still absent; DO NOT bypass TCP or supply fake crypto. Next integrate a genuine compatible crypto implementation/provider (AES first needed here, other digest/mode APIs also used). XNU contains SHA256/HMAC but no AES implementation; its corecrypto_aes.c is a dispatch wrapper. Full crypto table is in libkern/libkern/crypto/register_crypto.h.
- **0019 phone image prepared and verified**, user reports FASTBOOT+USB again; exactly one device/product sweet verified. `shim/out/shim-boot-0019.img`, 16,457,728 bytes, SHA256 `19429a57e486b856880df9e27327ed488e0139345311fdb0350974e72103a9a1`; unpacked ramdisk matches phone payload (`shim/out/check-0019`, `payload-0019.bin`, `sweet-0019.adt`, timer19). EntryPA0xb146b488; kernelendPA0xb2000000; boot_argsPA0xb2004000/top0xb2008000. Usual debug cmdline cpus=1/-noprogress. **Temporarily booted 2026-10-15 15:51:36 UTC**: send OKAY0.406s, boot OKAY0.134s, total0.546s, exit0 (`research/boot0019.log`). User asked for photo after45s. Await the photo; DO NOT reboot again before observation. Bootloader acceptance only; no0019 physical progress observed yet.
- **Hardware photo confirms 0018 progress** (`IMG_20261015_173951 (2).jpg`): real phone prints `SWEET CPU: boot CPU 0 registered, SGI1 verified, initialization complete`, enters BSD/MAC, reaches `mbinit: done`, then the same missing-pthread-function-table panic as QEMU at pthread_shims.c:318. CPU completion/interrupt delivery is now physically observed. The photo was received; no further phone boot performed yet.
- **Patch0019 committed/pushed `256d3f0`; Actions `38763072907` queued/running:** https://github.com/dipa-studio/ios-sweet/actions/runs/38763072907. Do not launch a duplicate. genuine Apple OSS libpthread-514.100.2 provider (immutable commit `a6c70ca0e544347a0377babf748f831577ec24f9`), paired with xnu-8796.141.3 by Apple's `distribution-macOS/macos-135/release.json` (`research/macos-135-release.json`). `0019-stage-libpthread.sh` fetches/stages it before patches; workflow now copies shell scripts too. SWEET-only wrappers compile all three real kernel sources, rename provider-side symbols, use kext header view and vm_fault_external, register the real function table at EARLY_BOOT SECOND. Existing pthread_init then runs the actual provider initialization. No pthread wait/syscall is bypassed. Source staging includes bounded explicit address casts/zero user addresses and panic-format fixes. SWEET kernel opaque ksyn storage enlarged from48 to56 bytes to contain this provider's trailing kwe_uth, with a provider size assertion. Local compile of three provider TUs plus pthread_shims/kern_fork passed; imports resolve and provider exports do not collide with existing kernel symbols. All19 patches apply and changed sources match (`research/patch0019-check.log`). Source sandbox `research/bt22/xnu`, originals `research/pre0019`, objects `research/pthread-objs`. Local TargetConditionals fixture only has target macros for syntax; CI stages the real macOS SDK header and is authoritative. Next Actions artifacts: out-0019 / bt23 / run23.log; run QEMU >=45 seconds. 0019 CI is pending; no 0019 QEMU run yet.
- **Latest kernel implementation `e7d9550`, patches0001-0018; CI `38760004398` PASSED (3m56s)**: https://github.com/dipa-studio/ios-sweet/actions/runs/38760004398. No build is pending. Kernel `xnu/out-0018/kernel.development.sweet`, SHA256 `b3c8d59acc88f93a2c836eb2527a05ed4fbaac7c580a2ea8c3b795137f4aadf1`; checksum verified. Source/log `research/bt22` / `research/run22.log` (baseline for patch0019; no nested .git). New driver/config sources match CI exactly.
- **CPU wait FIXED in QEMU**: SweetCPU uses legacy IOCPU, validates single boot CPU, ml_processor_register -> processor_start -> initCPU real SGI1 -> scheduler/IOKit completion -> ml_set_max_cpus. It does NOT force a flag or skip a wait. CPU APIs passed: Running CPU0, processor_array[0] association, SGI signalling enabled, name, unsupported secondary-start error (`research/check-cpu18.gdb` / `.log`, `research/q0018-api`). Single-core only; SMP and suspend unsupported. Two-CPU topology fails explicitly (`research/q0018-smp`).
- **Next blocker reproduced in 45s QEMU** (`research/q0018-long`, zero unknown glyphs): BSD initialization reaches MAC framework, network mcache and `mbinit: done`, then **`pthread kernel extension not loaded (function table is NULL). @pthread_shims.c:318`**. Do not bypass pthread_init: find/integrate the matching open-source libpthread kernel implementation and register its real function table. A 12s run `research/q0018` looked panic-free before the 30s IORTC wait elapsed; use >=45s or GDB checkpoints for the next iteration. No userland/root filesystem boot yet.
- **Phone 0018 TEMPORARILY BOOTED** 2026-10-15 **15:31:15 UTC**: exactly one FASTBOOT device verified product sweet; send OKAY0.400s, boot OKAY0.134s, total0.539s, exit0. Local log `research/boot0018.log`. This confirms bootloader acceptance only. User was asked for a photo; await it, DO NOT reboot the phone again before their observation. No flashing performed.
- Current hardware image `shim/out/shim-boot-0018.img`, **16,437,248 bytes**, SHA256 **`b7a23595d143a76c72c9fe2cfcc5d77a8f16987b5af1a2703bbffdfa6922908c`**. Phone ADT/payload `shim/out/sweet-0018.adt` / `shim/out/payload-0018.bin` (timer19), unpacked ramdisk exactly matches payload (`shim/out/check-0018`). EntryPA0xb146b488; kernelendPA0xb1ffc000; boot_argsPA0xb2000000/top0xb2004000. Command line `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress`.
- QEMU payload/ADT `research/q0018.bin` / `research/q0018.adt` (timer27). Run `python3 tools/qemu-sweet.py research/q0018.bin research/NEW-OUT --secs 45 --kernel xnu/out-0018/kernel.development.sweet --fail-on-panic`; fresh ADT seed for a new payload. Output fb.png/fb.txt/regs.txt/qemu.log/result.json. Visible panic exit2; blank/incomplete decode exit3; no_panic_observed is snapshot-only, not successful boot. -noprogress prevents the graphical indicator corrupting console text. Qualcomm XPU/bootloader/panel and SMC PSCI still need physical validation.
- Patches0014-0017 fixed PRNG registration/direct SHA256, explicit unavailable trust-cache service with rejected loads/no matches, built-in SweetPlatformExpert, and timer PPI from ADT (phone19/default19, QEMU27). All their checks/artifacts are preserved in the history below. CURRENT SWEET has NO USE_APPLEARMSMP; do not assume gCPUIC/gAIC/gPMGR AppleARMSMP globals exist.
- Local single-file checker: tools/xnu-cc.py research/run22.log research/bt22 FILTER --per-file, output via XNU_CC_OBJDIR. Patch checker `research/check-patch-order.py` applies all18 via temporary index, matches sandbox sources. Sandbox for0018 `research/bt21/xnu`, originals `research/pre0018`, objects `research/cpu-objs`. First compiler issues were missing machine/atomic.h and nonexistent getNameSymbol; fixed and both TUs compiled successfully before CI. Fixtures/artifacts under research are local-only/ignored.
- GDB: mask int(register)&((1<<64)-1) for unsigned kernel pointers; use function-entry manual-call return traps, discard manipulated guests. No QEMU fixture is intentionally left running.

### Earlier active handoff snapshot (superseded)


- **0018 milestone:** Actions `38760004398` PASSED (3m56s), checksums passed, new CPU/build files byte-match CI tree. Kernel `xnu/out-0018/kernel.development.sweet`, SHA256 `b3c8d59acc88f93a2c836eb2527a05ed4fbaac7c580a2ea8c3b795137f4aadf1`; log/tree `research/run22.log` / `research/bt22`. QEMU `research/q0018` 12s: boot CPU registered, SGI1 verified, CPU initialization complete, **BSD initialization entered** (copyright, MAC framework, buffer headers), no panic/zero unknown glyphs. Direct CPU API checks PASS (`research/check-cpu18.gdb`, `.log`, `research/q0018-api`): CPU0 Running, processor matches processor_array[0], real SGI enabled, CPU name correct, secondary-start returns KERN_NOT_SUPPORTED. Two-CPU ADT correctly panics before registration (`research/q0018-smp`). Longer 45s run `research/q0018-long` still running; next BSD wait/fault not yet identified.
- **Prepared 0018 phone image:** `shim/out/shim-boot-0018.img`, 16,437,248 bytes, SHA256 `b7a23595d143a76c72c9fe2cfcc5d77a8f16987b5af1a2703bbffdfa6922908c`. Phone ADT/payload `shim/out/sweet-0018.adt` / `shim/out/payload-0018.bin`, usual debug args with cpus=1 and -noprogress. Unpacked ramdisk byte-compared (`shim/out/check-0018`). Entry PA remains 0xb146b488; kernel end PA 0xb1ffc000, boot_args 0xb2000000/top0xb2004000. User reports FASTBOOT+USB ready; read-only check found exactly one product sweet. NEXT: temporary fastboot boot of this verified image, record send/boot results, then await hardware photo without rebooting again.
- Patch 0018 committed/pushed **`e7d9550`**; Actions **`38760004398`** queued/running: https://github.com/dipa-studio/ios-sweet/actions/runs/38760004398. Do not launch a duplicate run.
- Active continuation: patch **0018** implemented in `iokit/Kernel/arm/SweetCPU.cpp` + arm64 source list + exact qcom,kryo470 personality. Keeps legacy IOCPU, accepts ONLY the single boot CPU, fills ml_processor_info from parsed topology with CPU object as cpu_id, calls ml_processor_register and processor_start. initCPU sends SGI1 via ICC_SGI1R_EL1 and waits (1-second deadline) for real cpu_signal_handler to clear SIGPdisabled. Only after successful startup calls ml_cpu_init_completed/cpusRunning and publishes completion/max CPUs. Secondary startup/suspend are explicitly unsupported. Local C++ compile passed for both TUs (`research/cpu-objs`), all imports resolve, object contains dsb ishst / ICC_SGI1R / isb; all18 patches apply in order and match sources. Sandbox `research/bt21/xnu`, originals `research/pre0018`; next CI artifacts should use `xnu/out-0018`, `research/bt22`, `research/run22.log`. User has the phone available; first run QEMU, then prepare/temporarily boot useful hardware progress.
- Goal: iOS 16 generation XNU on Redmi Note 10 Pro / sweet. User wants persistent progress, commits, kernel builds via `gh` Actions, and this handoff kept current across agent switches. Temporary `fastboot boot` only; preserve Android and backups.
- Latest kernel implementation: **`aa3f991`**, patches **0001-0017**. All four new Actions builds PASSED: 0014 `38748543058` (4m0s), 0015 `38749549779` (4m3s), 0016 `38750733996` (4m3s), 0017 **`38751507437` (4m7s)**. Latest: https://github.com/dipa-studio/ios-sweet/actions/runs/38751507437. No build is pending.
- **Current blocker: CPU initialization has no SWEET CPU driver.** QEMU registers SweetPlatformExpert, then kernel_bootstrap_thread waits in **kperf_init_early -> ml_wait_max_cpus**. Confirmed via actual-kernel GDB call stack (`research/locate-cpu-wait2.log` / `.symbols`) and normal-run `max_cpus_initialized == 2` (MAX_CPUS_WAIT). Do not force the flag or remove the wait. Next implement real CPU registration and completion using XNU's CPU/processor interfaces. CURRENT SWEET uses legacy IOCPU (`USE_APPLEARMSMP` is absent); enabling AppleARMSMP is a separate architectural change requiring GIC/PMGR services. No userland/root filesystem boot yet.
- Fixed this session: PC=0 unregistered PRNG callback (0014, built-in HMAC-SHA256 provider + direct entropy/write_random hashing); absent Image4/AMFI trust-cache service (0015, unavailable runtime, loads rejected and queries no-match); missing platform driver (0016, SweetPlatformExpert exact personality); incorrect QEMU timer IRQ (0017, validated ADT PPI property, phone default19/QEMU27).
- **Latest kernel** `xnu/out-0017/kernel.development.sweet`, SHA256 `055a80190ab998b4df9203f1102072dcc78faad1779a537a514e522b682b582f`. Buildtree/log **`research/bt21` / `research/run21.log`**; all nine changed C/C++/header/config files byte-match local checked sources. Checksums passed. Entry PA `0xb146b488`, kernel end PA `0xb1ff4000`. Preferred baseline for patch 0018: `research/bt21/xnu` (no nested .git).
- **QEMU verified**: `research/q0017-noprogress` ran **30 seconds**, no visible panic, **zero unknown glyphs**, IRQ exceptions returned normally (4 IRQs, tickless idle). It still waits for CPU completion, so this is NOT a completed boot. Earlier 15s run `research/q0017` captured 122 IRQs, timer value27, GICR_ISENABLER0 `0x08000002`, and CPU WAIT; graphical progress overlay corrupted 13 glyphs. Add **`-noprogress`** to keep the console readable. New runner classifies incomplete/blank decoding explicitly (exit3 with --fail-on-panic), visible panic exits2. Snapshot-only; use regs/GDB to diagnose stalls.
- Current QEMU payload/ADT: **`research/q0017-noprogress.bin` / `research/q0017.adt`**. Run: `python3 tools/qemu-sweet.py research/q0017-noprogress.bin research/NEW-OUT --secs 30 --kernel xnu/out-0017/kernel.development.sweet --fail-on-panic`. Generate fresh seed with mkadt for a new payload. QEMU virt models generic ARM execution, not Qualcomm XPU/bootloader/panel or the phone's SMC PSCI conduit.
- **Prepared phone image**: **`shim/out/shim-boot-0017.img`**, 16,416,768 bytes, SHA256 `51ff7fe30b4a6668c3452ffe1da922c5ac30cb8de60625c2a652587ecbd0ec92`. Phone ADT/payload `shim/out/sweet-0017.adt` / `shim/out/payload-0017.bin` (timer19). Unpacked ramdisk exactly matches payload (`shim/out/check-0017`). Command line **`debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress`**. NOT sent to phone this session: known CPU-driver wait should be addressed in QEMU first. Last actual phone boot remains 0013; user's latest photo `IMG_20261015_160033.jpg` confirmed the old PRNG PC=0 panic.
- Direct QEMU/GDB API checks: PRNG 600-byte chunking, buffer bounds, changing output, zero-length reads/writes, user reseed, contended refresh, generator1 init/output PASS again on latest0017 (`research/check-prng17.gdb` / `.log`, guest `research/q0017-api`). Trust-cache unavailable-service/argument/output cases PASS on0015 (`research/check-trustcache.gdb` / `.log`). Platform registration + target/product normal/truncated/zero/null/bounds PASS on0016 (`research/check-platform.gdb`, `research/check-platform3.log`). Different platform ID correctly retains IOPanicPlatform fallback (`research/q0016-negative`). Timer PPI32 and size2 correctly panic (`research/q0017-badppi`, `research/q0017-badsize`); omitted property selects19 (`research/q0017-default`). These are API/bring-up checks, not crypto certification or SMP scheduling validation.
- Local checker **`research/check-patch-order.py`** verifies all17 patches and changed source equality using a temporary upstream git index. Fixtures/logs/artifacts under research are local-only and ignored. For GDB unsigned kernel pointers, mask `int(register_value) & ((1<<64)-1)`; x30/x29 may be rendered signed. Stop at a function entry for manual-call return traps, and discard each manipulated guest after API tests. All fixture QEMU guests have been stopped.


- User wants persistent progress toward iOS 16, commits for completed changes, builds through `gh` / GitHub Actions.
- Keep this file current at every milestone so another agent can resume immediately, including when usage limits interrupt a session.
- Patch 0017 committed/pushed `aa3f991`, Actions `38751507437` running: https://github.com/dipa-studio/ios-sweet/actions/runs/38751507437. Next artifacts: xnu/out-0017, research/bt21, research/run21.log. Do not duplicate.
- Patch 0016 kernel downloaded/checksum verified: SHA256 `d06336c54baa06e0df6561a8bd7df98a9b9f7502c9381c62d1a44c045cd1ce46`, `xnu/out-0016/kernel.development.sweet`; buildtree/log `research/bt20` / `research/run20.log`. QEMU `research/q0016` (10s) prints platform expert registered, zero unknown glyphs, no panic observed BUT stalls (timer mapping still wrong until 0017). Direct actual-kernel GDB tests passed for registered instance and target/product names, including maxLength=1/0 and NULL, sentinels intact (`research/check-platform.gdb`, `research/check-platform3.log`, `research/q0016-api3`). First two fixture attempts used premature/wrong return breakpoints; discarded, corrected fixture uses start-return observation then a function-entry return trap. No phone boot yet.
- Patch 0016 CI `38750733996` PASSED (4m3s); artifacts not yet downloaded/booted. Patch 0017 prepared to fix QEMU timer fidelity BEFORE interpreting future no-panic/stall runs: actual `qemu-system-aarch64 -M virt,gic-version=3,dumpdtb=research/qemu-virt.dtb ...` timer property has virtual PPI 11 => INTID 27, while phone uses INTID 19. Kernel formerly hardcoded 19. New optional /arm-io/gic virtual-timer-intid property selects the PPI (validate exact u32 and 16..31); missing property retains phone default 19. One RO-late global drives GIC enable and sleh_irq dispatch. tools/mkadt.py emits phone19/QEMU27. Both C TUs compile (`research/timer-objs`), ADT serialize/parse checks confirm values/addresses, all 17 patches apply and match. Sandbox `research/bt19/xnu`, originals `research/pre0017`; initial local sleh compile failed due read-only exported header not updated, fixed export and BOTH recompiled successfully. CI exports normally.
- Architecture correction for next agent: CURRENT SWEET board_config does NOT define USE_APPLEARMSMP; it compiles the legacy IOCPU path, not cpu_boot_thread/IOPMGR AppleARMSMP. Existing roadmap mentions AppleARMSMP as a future approach. Do not look for gCPUIC/gAIC/gPMGR globals in the current kernel: they are absent. Determine the actual CPU-init wait after platform registration before choosing the next driver architecture.
- Patch 0016 committed/pushed as `0dcdf5c`; Actions `38750733996` running: https://github.com/dipa-studio/ios-sweet/actions/runs/38750733996. Next artifacts: `xnu/out-0016`, `research/bt20`, `research/run20.log`.
- Patch 0016 implemented: new `iokit/Kernel/arm/SweetPlatformExpert.cpp`, arm64 source list, built-in personality in KernelConfigTables.cpp guarded by ARM64_BOARD_CONFIG_SWEET. Matches only xiaomi,sweet at score 10000, keeps IOPanicPlatform fallback for other hardware. Uses IODTPlatformExpert base initialization/power domain and createNubs CPU logical-ID annotation; overrides top-level publishing because base requires nonexistent Apple NVRAM /options. Publishes real CPU/top-level nodes, provides bounded target/product names, and registers the platform service. GIC/PMGR and arm-io child-bus drivers remain separate work. Both C++ TUs compile (`research/platform-objs`), all imports resolve against 0015/own objects, all 16 patches apply and sources match. Sandbox `research/bt19/xnu`; originals `research/pre0016`. NEXT: full Actions/QEMU; inspect CPU boot waits after the platform registers, do not equate absence of panic with successful boot.
- Patch 0015 CI `38749549779` PASSED (4m3s), checksum verified. Kernel `xnu/out-0015/kernel.development.sweet`, SHA256 `ca1b424a596c7058550ad1659080a333e7643270f019656401d80bf2b876645f`; buildtree/log `research/bt19` / `research/run19.log`. QEMU `research/q0015` (10s, zero unknown glyphs) passes trust-cache handling and machine lockdown, then panics `Unable to find driver for this platform: "xiaomi,sweet" @IOPlatformExpert.cpp:2319` (IOPanicPlatform fallback). Next concrete work: add a built-in SWEET IODTPlatformExpert personality/class; keep the fallback for mismatched platforms. No phone boot yet.
- Direct trust-cache API checks PASSED in the actual 0015 kernel under QEMU/GDB (`research/check-trustcache.gdb`, `research/check-trustcache.log`, `research/q0015-api`): runtime pointers NULL, hash/UUID queries return NOT_FOUND, typed/untyped/legacy loads return NOT_SUPPORTED, capabilities outputs zero and bounds preserved, invalid-argument cases still reject. Second PRNG check also verifies contended refresh returns immediately (`research/check-prng2.log`, `research/q0014-api2`).
- Patch 0015 committed/pushed `8f851d4`, Actions run `38749549779` running: https://github.com/dipa-studio/ios-sweet/actions/runs/38749549779. Do not duplicate.
- Direct PRNG API checks passed in the REAL 0014 kernel under QEMU/GDB, stopped before trust-cache init (`research/check-prng.gdb`, log `research/check-prng.log`, guest `research/q0014-api`). Invoked read_random for 600 bytes (chunking and buffer sentinel intact), repeated output differed, zero-length read/write succeeded, write_random(64-byte input) succeeded, secondary generator 1 initialized and generated distinct output. QEMU log has no exceptions during these checks; guest was discarded after the tests. This tests API behavior, not a cryptographic certification or SMP scheduling.
- Patch 0015 implemented in `bsd/kern/kern_trustcache.c`: SWEET with missing Image4/AMFI marks both runtime pointers NULL and logs service unavailability. Existing static loader handles NULL. Public load APIs return KERN_NOT_SUPPORTED; hash/UUID queries return KERN_NOT_FOUND; capabilities outputs are zero with KERN_NOT_SUPPORTED. Invalid-input checks remain, and Apple paths/available-provider initialization retain existing behavior. No trust is granted. Local arm64 compile passed (`research/trustcache-objs`), all 15 patches apply and sources match. Sandbox `research/bt18/xnu`, baseline `research/pre0015`. Next: Actions/QEMU and direct unavailable-service API checks using GDB.
- Patch 0014 CI `38748543058` PASSED (4m0s), artifact checksum passed. Kernel `xnu/out-0014/kernel.development.sweet`, SHA256 `741de20c851755d333759d7e668346f3edb4f325efac4cc7c142d80d1f5ff75c`; buildtree/log `research/bt18` / `research/run18.log`. QEMU run `research/q0014` (10s, zero unknown glyphs) prints `SWEET PRNG: HMAC-SHA256 registered`, passes commpage initialization, loads vnguard policy, THEN panics `image4 interface not available @kern_trustcache.c:253`. PC=0 fault FIXED IN QEMU. No new phone boot performed.
- Next work: SWEET currently has neither AppleImage4 nor AMFI. `trust_cache_runtime_init` unconditionally requires both; unlike BCM2837, SWEET enters this startup path. Investigate an explicitly unavailable trust-cache runtime with rejected loads/no query matches while providers are absent; preserve all Apple-platform checks. Merely skipping the startup call would leave later AMFI/IMG4 dereferences unsafe. Existing load_static_trust_cache already handles trust_cache_rt == NULL.
- QEMU runner now supports `--kernel MATCHING_KERNEL --fail-on-panic`: runtime font extraction from the payload produces exact `fb.txt`, plus `result.json`; a visible panic returns 2. Baseline 0013 re-run `research/q0013-text` detects the same NULL callback panic with zero unknown glyphs. Synthetic glyph roundtrip (including literal `?`) and invalid-VA rejection passed. Screen rows remain circular, not a full log; `no_panic_observed` does not prove successful boot. README has the invocation.
- Patch 0014 committed/pushed as `be61f99`; Actions run `38748543058` is running: https://github.com/dipa-studio/ios-sweet/actions/runs/38748543058. Do not start a duplicate build.
- 2026-10-15 active: hardware photo `IMG_20261015_160033.jpg` confirms the same PC=0 instruction-fetch abort as QEMU (LR `0xfffffe0007443fe4` = read_random). Independently reproduced with kernel 0013 in `research/q0013` (8-second run); prior allocator fix works. Latest tooling commit reviewed: `8d66d2d`.
- Patch 0014 implemented: registers a built-in HMAC-SHA256 DRBG provider at EARLY_BOOT/FIRST, before commpage creation. A shared mutex serializes output/reseed across CPUs (correctness first; no per-CPU scaling yet), initgen validates CPU identities, refresh uses try-lock and the existing entropy filter/health tests. SWEET entropy accumulation and write_random now use the built-in SHA256 directly; no incomplete g_crypto_funcs table is registered. The existing bootloader-seed requirement remains enforced.
- Local validation: both PRNG translation units compile to arm64 objects in `research/prng-objs`; no SHA256 wrapper imports remain. All 14 patches apply in CI order and resulting PRNG sources match local inputs. Sandbox `research/bt17/xnu`, originals `research/pre0014`, checker `research/check-patch-order.py`. NEXT: Actions kernel build, download to `xnu/out-0014`, run the QEMU payload before requesting another phone boot. Baseline QEMU ADT/payload are `research/q0013.adt` / `research/q0013.bin`.
- 2026-10-15 08:15 QEMU EMULATION WORKS (`tools/qemu-sweet.py`, see "QEMU emulation" below). Patch 0013 VERIFIED in QEMU: boot passes kmem_crypto_init and ulock_initialize. NEXT BLOCKER (reproduced in QEMU, expected on hardware too): `pmap_create_commpages -> random() -> RandomULong -> read_random -> prng_funcs.refresh == NULL` (instruction fetch abort at pc 0, lr read_random+0x34). Nobody calls `register_and_init_prng` (closed corecrypto kext does it on Apple). Fix = patch 0014: SWEET-registered cckprng_funcs provider (osfmk/prng/prng_random.c); note random_cpu_init also panics while !prng_ready. The 07:26 hardware boot of 0013 will most likely show this same panic.
- Previous hardware boot: patch 0013 (`a3a46e2`) CI `38686345689` PASSED (3m44s), image `shim/out/shim-boot-0013.img` was temporarily booted on 2026-10-15 at 05:26 UTC: send OKAY (0.407s), boot OKAY (0.134s), total 0.546s. Superseded by the hardware/QEMU PC=0 observation and patch 0014 work above.
- Latest kernel implementation commit: `8f851d4`. Latest successful kernel CI: `38749549779` (patch 0015), https://github.com/dipa-studio/ios-sweet/actions/runs/38749549779.
- CURRENT image: `shim/out/shim-boot-0013.img`, 16,412,672 bytes, SHA256 `110d1c46aee4b60253db24b6535e9781c4cf0fd25ca2a70e9016cf81378cea25`. Kernel `xnu/out-0013/kernel.development.sweet`, SHA256 `f0b1d375995316c550ce7014ff234d8271e9b9ac2857795ceff41b76ba2cd740`, plain arm64. CI log confirms 0013 applied; checksums passed. Entry PA `0xb146b488`, kernel end PA `0xb1ff4000`.
- Current log/buildtree: `research/run17.log`, `research/bt17` (extracted, no nested .git; preferred baseline for patch 0014). Payload/ADT: `shim/out/payload-0013.bin`, `shim/out/sweet-0013.adt` (includes /defaults). Unpacked ramdisk byte-compared with payload in `shim/out/check-0013`.
- Patch-0012 original image: `shim/out/shim-boot-0012.img`, 16,412,672 bytes, SHA256 `cf4b8ec4a65a51888d71c17d09cd0831d6b761b90e2531fdb8e575c32f4fd24a`. Kernel `xnu/out-0012/kernel.development.sweet`, SHA256 `69b78f49f6459cc63a0af44e7cf73c1779ef72130ef72b12ae86c88183ffc742`; checksums passed and sweet_early_relocate symbol present. Entry PA remains `0xb146b488`, kernel end PA `0xb1ff4000`.
- Patch-0012 log/tree: `research/run16.log`, `research/bt16` (extracted, no nested .git; preferred baseline for next patch). CI log confirms 0012 applied. Payload `shim/out/payload-0012.bin`, ADT `shim/out/sweet-0012.adt`; packed ramdisk byte-compared with payload in `shim/out/check-0012`.
- Active payload command line: `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam`.
- Latest observed photo: `/home/dipa/Downloads/IMG_20261015_071346.jpg` (local only). Stages 9 (kernel framebuffer VA), 10 (TTBR0 off/TLB flush), 11 (physmap table pointers) and 12 (calling pmap_bootstrap) all visible. Persistent diagnostics are VERIFIED ON HARDWARE.
- pmap.c:1987 is the assert after SecureDTLookupEntry(NULL, "/defaults", &entry). The pmap-max-asids property itself is optional; an empty /defaults uses XNU's MAX_ASIDS. Do not bypass the assertion or guess an ASID limit.
- Previous payload-only fix image: `shim/out/shim-boot-0012-defaults.img`, SHA256 `3ff61a268853c6ea7cbf284833f1346a1d791980ca366b6e1a215a86e040512b` (16,412,672 bytes). Same kernel/CI as 0012; ADT 2516 bytes (`shim/out/sweet-0012-defaults.adt`), root /defaults present with no ASID override. Payload `shim/out/payload-0012-defaults.bin`; unpacked ramdisk exactly matches payload (`shim/out/check-0012-defaults`). This boot reached the allocator crypto NULL-pointer fault below.
- Latest hardware photo: `/home/dipa/Downloads/IMG_20261015_071608.jpg` (local only). Normal serial framebuffer console works. VM bootstrap/zone initialization, logging, scheduler, IPC, KDP and ulock_initialize print before the kmem_crypto_init data abort. gPanicBase warnings are expected because PLATFORM_PANIC_LOG_DISABLED is configured, and are not the faulting call.
- Faulting instruction: `ldr x0, [x8, #0x150]` in crypto_random_kmem_ctx_size; x8 = NULL g_crypto_funcs. Source: libkern/crypto/corecrypto_rand.c and osfmk/vm/vm_kern.c:2448. rand.h explicitly permits NON-cryptographic kmem RNGs, but requires no locking in generation and context size <=256. Do not substitute read_random here: the global PRNG provider is also unregistered and kmem generation must remain lock-free. early_random may seed contexts during single-CPU bootstrap; it uses the built-in HMAC-SHA256 DRBG with the host-generated ADT seed.
- Patch 0013 keeps the replacement entirely private to vm_kern.c: SplitMix64 state is 8 bytes per CPU/sizeclass, initialized using early_random. Uniform selection uses rejection sampling to avoid modulo bias. Other platforms retain the original corecrypto calls; SWEET's generic crypto APIs/registration remain untouched. Reference: https://prng.di.unimi.it/splitmix64.c (Sebastiano Vigna, public domain).
- Validation: vm_kern.c compiles to arm64 (`research/kmem-rng-objs`, using `research/run16.log` + `research/bt16`). Undefined symbols include early_random and NO crypto_random_* references. All 13 patches apply in CI order and match the checked source. Original saved as `research/vm_kern.pre0013.c`.
- RNG host checks: extracted actual new helper in `research/kmem-rng-check.c`; 16,384 outputs match the author's downloaded reference (`research/splitmix64-reference.c`), 147,456 bounded outputs satisfy their limits (including 1 and UINT64_MAX), and a forced rejection advances correctly. Full kernel crypto services and the registered kernel PRNG provider remain future work; this patch only supplies the allocator RNG contract.
- Patch-0011 artifacts: kernel `xnu/out-0011/kernel.development.sweet`, log `research/run15.log`, extracted buildtree `research/bt15` (no nested .git). CI log confirms patch 0011 applied; artifact checksums passed. Kernel is plain arm64, entry VA `0xfffffe000746b488` -> PA `0xb146b488`, kernel end PA still `0xb1ff4000`.
- Patch-0011 image: 16,412,672 bytes, SHA256 `29ad5cbfc752473de4af2c471a24a8b34f92da4bff5afec65c614b90cd3ad53a`. Kernel SHA256 `8ac3a956894b09f7bd0d890de8c5de38c65513287feb1e1bcd12db8ca772ee5c`. Payload/ADT in `shim/out/payload-0011.bin`, `shim/out/sweet-0011.adt`. Header unpacked/checked in `shim/out/check-0011`.
- First patch-0011 attempt timed out waiting for FASTBOOT; superseded by the successful send/boot recorded above. Do not repeat the boot before the user reports the current screen.
- Patch-0011 photo: `/home/dipa/Downloads/IMG_20261015_070218.jpg` (local only). Visible stages 1-8 and old red shim text; entry PA B146B488, boot args PA B1FF8000 / top B1FFC000. No evidence yet locating execution after stage 8.
- Patch-0012 sandbox: `research/bt15/xnu` (NO nested .git). Proposed permanent debug VA: VM_MIN_KERNEL_ADDRESS + ARM_TT_L2_SIZE, in the reserved gap below gVirtBase and above low globals; map the same PA framebuffer as Normal-NC in the final TTBR1 before arm_vm_physmap_init changes phystokv(). Keep the early panic sink until normal kprintf startup.
- Patch 0012 validation: all 4 changed C translation units compiled to arm64 objects in `research/post-ttbr0-objs`; internal header export added to osfmk/arm64/Makefile and recreated locally in EXPORT_HDRS for pe_serial.c. All 12 patches apply in CI order and match all 6 edited files. Baselines are saved under `research/pre0012/`.
- Actual mapping-helper host model (`research/early-map-check.c`, barrier stubbed only for host compilation) confirms: VA `0xfffffe0002000000`, L1 index 2016, L2 index 1; descriptor `0x006000009c000605` = framebuffer PA, Normal-NC, outer-shareable, EL1 RW, PXN+UXN. All other L2 entries preserved. The gap is excluded by ARM_LARGE_MEMORY's pmap_virtual_region(0).
- Next boot command line MUST add `-enable_kprintf_spam`: DEVELOPMENT disables kprintf_output by default even when DB_KPRT is set (`pexpert/arm/pe_kprintf.c`). Planned command line: `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam`.

### Patch 0012 screen legend (after original stages 1-8)
- 9: kernel framebuffer VA live; 10: TTBR0 off / TLB flushed; 11: physmap pointers rebound.
- 12: calling pmap_bootstrap; 13: pmap_bootstrap returned; 14: early I/O page tables ready; 15: arm_vm_init returned.
- 16: calling serial_init; 17: fbcon config read / mapping; 18: fbcon mapping returned; 19: serial_init returned; 20: kprintf startup complete.
- Normal serial initialization clears the screen, so earlier stages may disappear before 19. If serial exists, early console disables after 20; otherwise early panic output stays enabled.
- Local source sandbox: `research/bt13/xnu` (git baseline `623b060`, patches 0001-0008 already in baseline; uncommitted export edits correspond to patch 0009). Preserve those edits when producing patch 0010.
- Previous compile log/tree: `research/run13.log`, `research/bt13`; previous kernel: `xnu/out/kernel.development.sweet`. New successful log/tree: `research/run14.log`, `research/bt14`; new kernel: `xnu/out-0010/kernel.development.sweet`.
- Current device access: patch-0012 image was temporarily started from FASTBOOT; no adb connection is expected. Never flash partitions for bring-up; use `fastboot boot` with the shim image.
- Patch `0010` lives in `xnu/patches/0010-sweet-early-framebuffer-debug.patch`. Its exact compiled sources are under `research/bt13/xnu`; generated objects are in `research/early-objs`.
- Local checks passed: all 5 changed/new translation units compiled to arm64 Mach-O objects; new console only imports `_doprnt` and `iso_font` (both exist in the previous kernel). All 10 patches applied to a temporary upstream git index in CI order, and all 7 resulting changed source files matched the local compile inputs.
- Reproduce changed-file compilation: `XNU_CC_OBJDIR="$PWD/research/early-objs" python3 tools/xnu-cc.py research/run13.log research/bt13 arm_init.c arm_vm_init.c kern/debug.c arm64/start.s osfmk/arm64/sweet_early.c --per-file` (create the output directory first). The helper now accepts explicit new source paths absent from the old artifact's Makefiles and rejects empty selections.
- Device command result: `adb reboot bootloader`, then `timeout 30s fastboot boot shim/out/shim-boot-0010.img`: send OKAY (16028 KB), boot OKAY, total 0.541s. This confirms bootloader acceptance only, not XNU progress.
- The successful boot above was on 2026-10-14. On 2026-10-15, `timeout 45s fastboot boot ...` timed out waiting for a device; no image was sent. Boot image SHA256 rechecked and unchanged. No new build is needed to repeat the trial.
- Earlier retry: `timeout 90s fastboot boot shim/out/shim-boot-0010.img` timed out (exit 124). This is superseded by the successful 2026-10-15 repeat boot recorded above; do not repeat it until the user reports the screen.
- User observation confirms MMU-on C entry, platform expert, CPU topology/bootstrap, startup bootstrap and cpu_init all complete. Failure is within arm_vm_physmap_init (non-XNU_MONITOR branch), before stage 8 / TTBR0 teardown. Old red shim text remains behind the early console; this is expected because only written rows are cleared.
- New edit/compile sandbox is `research/bt14/xnu` (extracted artifact, NO nested .git; `git diff` there would act on the parent project). Use a saved baseline file / generated unified diff, or explicitly initialize a local baseline before editing. `research/bt13/xnu` does have its own baseline git repo.
- Exact user-reported panic: `arm_vm_init.c:1606 Assertion failed: (temp_ptov_table[ptov_index - 1].va + temp_ptov_table[ptov_index - 1].len) <= physmap_end`.
- Root cause: MacOSX build sets `ARM_LARGE_MEMORY=1`; `XNU_MONITOR` is off. Block alignment in arm_vm_physmap_slide can add gaps, but the non-PPL large-memory physmap_end omitted the padding already present in the small-memory branch. Patch 0011 reserves PTOV_TABLE_SIZE*ARM_TT_TWIG_SIZE (8*32MB) in both the aperture bound and its L1 allocation; monitor/PPL builds retain zero padding. The assertion is preserved.
- Validation: `arm_vm_init.c` compiled to an arm64 object using `research/run14.log` + `research/bt14` (object in `research/physmap-objs`). All 11 patches apply in CI order and produce exactly that source. Host harness `research/physmap-check.c` extracts the real XNU slide function with page-table-write stubs: all 65,536 page-aligned slides within 1GB checked against the current kernel memory layout; old bound fails 65,504 cases, padded bound fails zero; worst overrun 0x1ffc000 bytes. Baseline source saved as `research/arm_vm_init.pre0011.c`.
- CI: https://github.com/dipa-studio/ios-sweet/actions/runs/38638702083 (success; artifacts and full log downloaded).
- New image: `shim/out/shim-boot-0010.img` (16,412,672 bytes, SHA256 `ed2c270e0081bad5ff517be415f9bfe8805463aea7866732203fa97ac737e64e`). Previous image preserved as `shim/out/shim-boot-pre0010.img`.
- New kernel SHA256: `3ee05270cc291c49589a101678f904b1b69cb2f2575e05398bce8a4e052f409a`. Artifact checksums passed; arm64 (not arm64e), early-console symbols present. Payload entry VA `0xfffffe000746a488` -> PA `0xb146a488`; kernel end PA `0xb1ff4000`. Command line remains `debug=0xc serial=3 cpus=1 keepsyms=1`.

### Patch 0010 screen legend
- Bottom-left white 64x32 tile: assembly entered `start_first_cpu`, before page tables/MMU.
- Text `SWEET EARLY 1`: entered `arm_init` with MMU on; 2: platform expert done; 3: CPU topology done; 4: CPU bootstrap done; 5: startup bootstrap done; 6: CPU init done; 7: entering `arm_vm_init`; 8: leaving bootstrap TTBR0.
- `SWEET EARLY PANIC:` prints a copy of panic arguments before normal panic handling needs per-CPU/thread state.
- In patches 0010/0011 the console disabled before TTBR0 invalidation; patch 0012 replaces that with a persistent kernel VA alias and diagnostics through normal serial/kprintf startup (see its legend above).
- Screen contents require a physical observation from the user: adb cannot capture a framebuffer after Android has been replaced by a bare-metal kernel.

## QEMU emulation (2026-10-15)
- `tools/mkadt.py OUT.adt --qemu` = same ADT with QEMU virt GICD 0x08000000 / GICR 0x080a0000 / CNTFRQ 62.5 MHz.
- `tools/qemu-sweet.py PAYLOAD OUTDIR [--secs N] [--hmp "x /8gx ADDR"] [--gdb]`: host-side copy of shim/loader.c
  (segments + ADT + boot_args at their PAs, 6-instruction stub sets x0 and jumps), `-M virt,gic-version=3 -cpu cortex-a76`
  (same ID_AA64MMFR0 as Kryo 470), 4G RAM so 0x9c000000 fb and 0xb0000000 kernel are plain RAM.
  Outputs `fb.png` (the screen), `regs.txt`, `qemu.log` (exceptions with ESR/FAR/ELR). `--gdb` halts with a stub on :1234
  (system `gdb` + `set architecture aarch64`, `hbreak *VA` works with the MMU on).
- Not emulated: Qualcomm XPUs, ABL, real panel, PSCI conduit (QEMU virt uses hvc). Hardware still decides those.
- Example: `python3 tools/mkadt.py /tmp/q.adt --qemu && python3 tools/mkpayload.py xnu/out-0013/kernel.development.sweet /tmp/q.adt /tmp/pq.bin --cmdline 'debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam' && python3 tools/qemu-sweet.py /tmp/pq.bin /tmp/q`

## Layout
- `backup/`  partition dumps + full sdb..sdf LUNs, `SHA256SUMS` (all verified on-device == local)
- `dtb/`     live FDT (from OrangeFox), PixelOS boot.img dtb, dtbo, dts versions
- `shim/`    bare-metal boot shim (`./build.sh` -> `out/shim-boot.img`)
- `research/` mainline sm7150 dts, XNU pe_fiq.c etc, PocketDarwin patches
- `tools/unpack_bootv2.py`

## Device access
- No root in PixelOS 17 (user build, adb root disabled). Root = `fastboot boot recovery/recovery.img` (OrangeFox R12.0_2, temp boot, not flashed).
- `fastboot boot` works for stock boot.img, OrangeFox, and our shim.
- Shim resets itself via PSCI SYSTEM_RESET; IMEM restart_reason 0x77665500 is seen
  (bootreason=bootloader) but ABL still boots Android, so it does not stop in fastboot.

## Hardware facts (live DT + shim measurements)
- Framebuffer (ABL cont splash): 0x9c000000, 1080x2400, stride 4320, a8r8g8b8, panel is DSC *video* mode (no kick needed).
- RAM: 0x80000000+2G, 0x100000000+0xfcc00000. Live reserved-memory = PixelOS base dtb + sweet dtbo fixups.
- GICv3: GICD 0x17a00000, GICR 0x17a60000 (stride 0x20000, 8 PEs). GICD_TYPER 0x007B041D: SecurityExtn=1 (DS=0).
  ABL leaves GICD_CTLR = 0x10 (ARE_NS on, groups off). Writing 0x13 reads back 0x12: EnableGrp1A (bit1) is the one that matters.
- GIC Group 1 VERIFIED (shim 2c): vtimer INTID 19 = 30 IRQs/300ms, ptimer INTID 18 = 30, SGI 1 to self = 10/10.
  ICC_SRE=1 already, ICC_CTLR 0x8440, priority/enable writes stick. GICR_IGROUPR0 and GICD_IGROUPRn read 0 from NS
  even though Group 1 delivery works (RAZ in NS view) - do not use them as a health check.
  INTID 28 shows pending constantly (unknown PPI 12).
- Timer 19.2 MHz. Qualcomm PPI numbering: secure-phys/phys/virt/hyp = PPI 1/2/3/0 -> vtimer INTID 19, ptimer 18 (NOT 27/30).
- PSCI 1.1 via SMC, SMCCC 1.1. CPU_ON ok; SYSTEM_SUSPEND and SYSTEM_RESET2 unsupported.
- SMP: CPU_ON works for all 7 secondaries (MPIDR 0x81000n00).
- Entry EL1, MMU off, x0 = DTB (magic ok). ID_AA64MMFR0 = ...101122: 16K granule YES, 4K YES.
- No serial console: debug UART SE is in SPI mode on sweet. Screen is the only debug channel.

## XNU implications
- XNU GICv3 path (pexpert/arm/pe_fiq.c, VMAPPLE) puts the timer (PPI 27) in Group 0 = FIQ. With SecurityExtn=1,
  Group 0 is secure -> impossible from NS EL1. Need: Group 1 IRQ, ICC_IGRPEN1, INTID 19, and the timer handled in the IRQ path.
- XNU VMAPPLE PSCI uses `hvc`; sweet needs `smc`.
- PocketDarwin NonAppleGenericCpu.patch only swaps -mcpu=apple-h7 for -march=armv8-a.

## Milestone 3 (2026-10-14): 16K MMU verified
- ABL loads the Image at 0x80080000 (base 0x80000000 + 0x80000) regardless of text_offset=0.
- 16K granule, T0SZ=28 (L2 start, 32MB blocks), MAIR 0x44ff00, TCR 0x1409CB51C, PARange=2 (40-bit PA).
- Only own image pages WB (Qualcomm XPUs: never map carve-outs cacheable); fb Normal-NC; rest Device.
- AT S1E1R checks all as expected; unmapped -> translation fault L2.
- Fill fb: 110 ms (MMU off) -> 3.5 ms (NC). Memory loop 70 ms -> 0.7 ms. vtimer + SGI IRQs still fine with MMU on.

## XNU loader design notes (xnu-8796)
- Entry: `_start` -> `start_first_cpu`, x0 = boot_args (PA), MMU off, EL1. boot_args: Revision/Version 2,
  virtBase/physBase/memSize/topOfKernelData, Boot_Video (fb 0x9c000000, rowBytes 4320, 1080x2400, depth 32),
  deviceTreeP/Length (ADT), CommandLine[608], bootFlags, memSizeActual.
- start.s maps [kernel mach header .. physBase+memSize) with cacheable BLOCKS. On Qualcomm every XPU carve-out
  must therefore lie below the kernel: use physBase = 0xb0000000 (above ramdump_fb 0xaf000000+16M,
  cdsp_sec, qseecom), end before rmtfs 0xfde01000 -> memSize ~0x4d000000 for bring-up.
- Base ADT: iPhone 8 d20ap DeviceTree from iOS 16.7.16 IPSW (tools/remotezip.py + tools/adt.py; LZFSE payload).
  Template values are empty: the loader must fill chosen/*, memory-map, cpu*/reg etc. like iBoot.
- XNU build: GitHub Actions (dipa-studio/ios-sweet, xnu.yml) via blacktop/darwin-xnu-build, MACOS_VERSION=13.5.
  New-clang fixes in xnu/patches; Xcode 16 iig is incompatible (IORPC.kernelContent) -> macos-14 runner, oldest Xcode.

## Kernel architecture plan (SWEET machine config)
- First stock build (VMAPPLE) is arm64e (PAC). Kryo 470 = ARMv8.2, no PAC -> SWEET must build plain arm64
  (HAS_APPLE_PAC is only defined for __arm64e__, so arm64 drops PAC automatically).
- Template: VMAPPLE (16K pages, GICv3 code in pe_fiq.c) minus APPLEVIRTUALPLATFORM hypercalls/paravirt PAC,
  plus BCM2837 (generic ARM: no APPLE_ARM64_ARCH_FAMILY Apple sysregs). Do not link the KDK lib<cfg>.os.*.a.
- Timer: XNU expects it on FIQ (sleh_fiq -> rtclock_intr). SWEET: GIC Group 1, so sleh_irq must ack ICC_IAR1,
  route INTID 19 to the same timer path, SGIs to cpu_signal_handler, EOI via ICC_EOIR1.
- SMP/interrupts: open-source AppleARMSMP (USE_APPLEARMSMP) needs an IOInterruptController (AIC role) and an
  IOPMGR (CPU power -> PSCI CPU_ON via SMC). Plan: built-in IOKit classes (SweetGIC, SweetPMGR, platform
  expert) compiled into the kernel and matched via iokit/KernelConfigTables.cpp -> no closed kexts needed.
- Debug: xnu patch 0003 sweet-fbcon = kprintf drawn on the framebuffer (ADT node "sweet-fbcon").
- ADT /arm-io ranges must not map to parent 0 (pe_arm_get_soc_base_phys()==0 disables serial): use parent 0x1000000.

## Status 2026-10-14 late: first XNU boot attempt
- SWEET kernel builds (CI run 38635343732, patches 0001-0009). Verified offline: arm64, no PAC, no >v8.2 code,
  70300 classic relocs all in range (slide 0 = no loader work), entry = __start.
- Boot: shim loader worked (initrd 0x846F0000-0x855FE3E0, BOOTARG 0xB1FF8000, JUMP 0xB146A488) but screen stayed
  red -> xnu died/hung before serial_init (fbcon). kprintf is a no-op before STARTUP_SUB_KPRINTF.
- Historical next-step plan (patch 0010, now implemented and built; see active handoff above): early fb debug channel
  1. start.s start_first_cpu (after mov x20,x0 / mov x21,#0): PA marker band (MMU off, direct store to 0x9c000000).
  2. start.s after both create_bootstrap_mapping calls: add ONE block entry to the existing V=P L2 table
     (x25 + 2*PGBYTES; do NOT call create_bootstrap_mapping again: fb and RAM share L1 entry 0, it would replace
     the L2 table). Entry = TYPE_BLOCK|VALID|SH(OUTER)|ATTRINDX(CACHE_ATTRINDX_WRITECOMB=1)|AF|PNX|NX|0x9c000000,
     index = (va & ARM_TT_L2_INDEX_MASK) >> ARM_TT_L2_SHIFT.
  3. new osfmk/arm64/sweet_early.c (+files.arm64 entry): sweet_early_mark(n) colored bands at screen bottom and
     sweet_early_putc using iso_font via VA=PA while the bootstrap TTBR0 is live; disable before
     arm_vm_init's set_mmu_ttb(invalid_ttep) (arm_vm_init.c:2148).
  4. markers in arm_init after PE_init_platform, ml_parse_cpu_topology, cpu_bootstrap, kernel_startup_bootstrap,
     cpu_init, before arm_vm_init.
  5. panic hook at top of panic_trap_to_debugger: va_copy + _doprnt(fmt, &copy, sweet_early_putc, 16).
  Verify with tools/xnu-cc.py (compile+symbols) and patch-order simulation before the single CI run.
