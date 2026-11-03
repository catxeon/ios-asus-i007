/* SPDX-License-Identifier: GPL-3.0-only
 * Small host utility linked with planetbeing/libdmg-hfsplus (GPL-3.0).
 * Changes ownership inside a local image, never on a device.
 */
#include <hfs/hfsplus.h>

char endianness;

int main(int argc, char **argv)
{
    int one = 1;
    io_func *io;
    Volume *volume;
    int ok;
    if (argc != 5) return 2;
    endianness = *(char *)&one ? IS_LITTLE_ENDIAN : IS_BIG_ENDIAN;
    io = openFlatFile(argv[1]);
    if (!io) return 1;
    volume = openVolume(io);
    if (!volume) { CLOSE(io); return 1; }
    ok = chownFile(argv[2], (uint32_t)strtoul(argv[3], NULL, 10),
                   (uint32_t)strtoul(argv[4], NULL, 10), volume);
    closeVolume(volume);
    CLOSE(io);
    return ok ? 0 : 1;
}
