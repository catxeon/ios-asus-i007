/* SPDX-License-Identifier: GPL-3.0-only
 * Small host utility linked with planetbeing/libdmg-hfsplus (GPL-3.0).
 * Changes ownership inside a local image, never on a device.
 */
#include <hfs/hfsplus.h>
#include <string.h>

char endianness;

int main(int argc, char **argv)
{
    int one = 1;
    io_func *io;
    Volume *volume;
    int ok;
    if (argc != 5 && (argc != 6 || strcmp(argv[5], "--restricted") != 0)) return 2;
    endianness = *(char *)&one ? IS_LITTLE_ENDIAN : IS_BIG_ENDIAN;
    io = openFlatFile(argv[1]);
    if (!io) return 1;
    volume = openVolume(io);
    if (!volume) { CLOSE(io); return 1; }
    if (argc == 6) {
        HFSPlusCatalogRecord *record = getRecordFromPath(argv[2], volume, NULL, NULL);
        ok = record && record->recordType == kHFSPlusFileRecord;
        if (ok) {
            HFSPlusBSDInfo *bsd = &((HFSPlusCatalogFile *)record)->permissions;
            bsd->ownerID = (uint32_t)strtoul(argv[3], NULL, 10);
            bsd->groupID = (uint32_t)strtoul(argv[4], NULL, 10);
            bsd->adminFlags |= 0x08; /* SF_RESTRICTED (0x00080000) */
            ok = updateCatalog(volume, record);
        }
        free(record);
    } else {
        ok = chownFile(argv[2], (uint32_t)strtoul(argv[3], NULL, 10),
                       (uint32_t)strtoul(argv[4], NULL, 10), volume);
    }
    closeVolume(volume);
    CLOSE(io);
    return ok ? 0 : 1;
}
