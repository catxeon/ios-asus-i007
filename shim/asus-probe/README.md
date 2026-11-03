# ASUS I007 diagnostic shim

CPU/display bring-up for the ASUS Smartphone for Snapdragon Insiders (SM8350).
This is not an XNU or iOS boot image. The topaz boot path remains separate.

Build on Linux/WSL with `gcc-aarch64-linux-gnu`, `binutils-aarch64-linux-gnu`
and Python 3. The build script expects AOSP system/tools/mkbootimg checked out
next to this repository, or `MKBOOTIMG` pointing to its `mkbootimg.py`.

```sh
bash shim/asus-probe/build.sh
# Only on the verified, unlocked ASUS I007:
fastboot boot shim/asus-probe/out/asus-i007-probe-0005.img
```

The probe checks the incoming DT's PICASSO model, reads CPU feature registers,
finds a linear inherited SSPP buffer inside this device's reserved splash RAM,
and renders the readings using ABL's autonomous refresh. It never writes
display-controller registers: directly starting CTL disrupted this panel.
It does not initialize clocks, regulators, a storage controller or secondary
CPUs. Current probe 0005 enables the existing 16K MMU implementation, maps
only its own image as write-back memory, and retains Device-nGnRnE access to
the inherited framebuffer. The topaz MMU defaults are unchanged. It checks
the translated addresses and memory attributes of code and framebuffer, plus
an intentionally unmapped address. It requests a PSCI reset 120 seconds after
completing the display output.

Addresses and panel dimensions came from the live DT of the tested ASUS.
Display-register offsets were checked against the published ASUS SM8350
`sde_hw_ctl.c` and `sde_hw_sspp.c` (bluehomewu/android_kernel_asus_sm8350,
commit a13732f3b6d00d3546e5c2e02008cd6b2f5aa7b3). It changes pixel RAM only;
it does not alter display timing, format, DMA mappings or rails.

Probe 0001 was tested on hardware on 2026-11-03: readable CPU/display output
was photographed, Android returned, and boot/vendor_boot/dtbo checksums were
unchanged. See the ASUS section in NOTES.md for evidence and unfinished work.
Probe 0002's photo captured only partial output; it does not establish MMU
success. Probe 0003 produced screen artifacts according to the user, without
a captured result. Probe 0004 restored byte pixel stores and Device memory
but still produced full-screen artifacts; the phone was manually rebooted.
Probe 0005 changed only the direct CTL_START write, removing it. The user
confirmed a readable screen and `16K CHECK PASS` on hardware on 2026-11-03.
This validates the boot CPU's 16K translation, code/FB physical addresses and
memory attributes, and an intentionally unmapped address. GIC, SMP, UFS and
XNU remain untested here.
