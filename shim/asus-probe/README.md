# ASUS I007 diagnostic shim

CPU/display bring-up for the ASUS Smartphone for Snapdragon Insiders (SM8350).
This is not an XNU or iOS boot image. The topaz boot path remains separate.

Build on Linux/WSL with `gcc-aarch64-linux-gnu`, `binutils-aarch64-linux-gnu`
and Python 3. The build script expects AOSP system/tools/mkbootimg checked out
next to this repository, or `MKBOOTIMG` pointing to its `mkbootimg.py`.

```sh
bash shim/asus-probe/build.sh
# Only on the verified, unlocked ASUS I007:
fastboot boot shim/asus-probe/out/asus-i007-probe-0001.img
```

The probe checks the incoming DT's PICASSO model, reads CPU feature registers,
finds a linear inherited SSPP buffer inside this device's reserved splash RAM,
and renders the readings. It can kick the inherited primary command-mode CTL.
It does not initialize clocks, regulators, a storage controller or secondary
CPUs. It requests a PSCI reset 45 seconds after completing the display output.

Addresses and panel dimensions came from the live DT of the tested ASUS.
Display-register offsets were checked against the published ASUS SM8350
`sde_hw_ctl.c` and `sde_hw_sspp.c` (bluehomewu/android_kernel_asus_sm8350,
commit a13732f3b6d00d3546e5c2e02008cd6b2f5aa7b3). It changes pixel RAM and
CTL_START only; it does not alter display timing, format, mappings or rails.

Probe 0001 was tested on hardware on 2026-11-03: readable CPU/display output
was photographed, Android returned, and boot/vendor_boot/dtbo checksums were
unchanged. See the ASUS section in NOTES.md for evidence and unfinished work.
