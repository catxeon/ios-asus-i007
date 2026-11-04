/* SPDX-License-Identifier: GPL-3.0-only
 * Small host utility linked with planetbeing/libdmg-hfsplus (GPL-3.0).
 * Copies every folder, file and symlink of one local HFS+ image into another
 * (fresh, larger) image, never a device:  asus-hfs-copyvol SRC.hfs DST.hfs
 * Keeps mode, owner, group, ownerFlags and adminFlags (e.g. SF_RESTRICTED on
 * the dyld cache). HFS+ private metadata folders are not copied. Entries that
 * already exist in DST are left untouched. Exit status 0 only if nothing failed.
 */
#include <hfs/hfsplus.h>
#include <hfs/hfslib.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

char endianness;

static Volume *src, *dst;
static unsigned long dirs, files, links, kept, failed;

static HFSPlusCatalogRecord *lookup(const char *path)
{
    return getRecordFromPath3(path, dst, NULL, NULL, FALSE, FALSE, kHFSRootFolderID);
}

static void copy_attrs(const char *path, const HFSPlusBSDInfo *from)
{
    HFSPlusCatalogRecord *record = getRecordFromPath3(path, dst, NULL, NULL, FALSE, FALSE, kHFSRootFolderID);
    HFSPlusBSDInfo *to;
    if (record == NULL) {
        fprintf(stderr, "attr lookup failed: %s\n", path);
        failed++;
        return;
    }
    to = record->recordType == kHFSPlusFolderRecord ? &((HFSPlusCatalogFolder *)record)->permissions
                                                    : &((HFSPlusCatalogFile *)record)->permissions;
    to->ownerID = from->ownerID;
    to->groupID = from->groupID;
    to->fileMode = (uint16_t)((to->fileMode & 0170000) | (from->fileMode & 07777));
    to->adminFlags = from->adminFlags;
    to->ownerFlags = (uint8_t)(from->ownerFlags & ~UF_COMPRESSED);
    if (!updateCatalog(dst, record)) {
        fprintf(stderr, "attr update failed: %s\n", path);
        failed++;
    }
    free(record);
}

static void walk(HFSCatalogNodeID folder, const char *path)
{
    CatalogRecordList *list = getFolderContents(folder, src), *item;
    for (item = list; item != NULL; item = item->next) {
        char *name = unicodeToAscii(&item->name);
        char child[PATH_MAX];
        HFSPlusCatalogRecord *existing;
        if (name[0] == '\0' || strcmp(name, ".HFS+ Private Directory Data\r") == 0 ||
            strcmp(name, ".fseventsd") == 0 || strcmp(name, ".journal") == 0 ||
            strcmp(name, ".journal_info_block") == 0) {
            free(name);
            continue;
        }
        snprintf(child, sizeof(child), "%s/%s", strcmp(path, "/") ? path : "", name);
        free(name);
        existing = lookup(child);
        if (item->record->recordType == kHFSPlusFolderRecord) {
            HFSPlusCatalogFolder *f = (HFSPlusCatalogFolder *)item->record;
            if (existing == NULL) {
                if (newFolder(child, dst) == 0) {
                    fprintf(stderr, "mkdir failed: %s\n", child);
                    failed++;
                    continue;
                }
                copy_attrs(child, &f->permissions);
                dirs++;
            }
            free(existing);
            walk(f->folderID, child);
        } else if (item->record->recordType == kHFSPlusFileRecord) {
            HFSPlusCatalogFile *f = (HFSPlusCatalogFile *)item->record;
            void *buffer = NULL;
            size_t size = 0;
            AbstractFile *mem;
            if (existing != NULL) {
                free(existing);
                kept++;
                continue;
            }
            mem = createAbstractFileFromMemoryFile(&buffer, &size);
            writeToFile(f, mem, src);
            if ((f->permissions.fileMode & 0170000) == S_IFLNK) {
                char target[PATH_MAX];
                size_t n = size < sizeof(target) - 1 ? size : sizeof(target) - 1;
                memcpy(target, buffer, n);
                target[n] = '\0';
                mem->close(mem);
                if (!makeSymlink(child, target, dst)) {
                    fprintf(stderr, "symlink failed: %s\n", child);
                    failed++;
                    continue;
                }
                links++;
            } else {
                HFSCatalogNodeID cnid = newFile(child, dst);
                HFSPlusCatalogFile *out = cnid ? (HFSPlusCatalogFile *)getRecordByCNID(cnid, dst) : NULL;
                if (out == NULL) {
                    fprintf(stderr, "create failed: %s\n", child);
                    failed++;
                    mem->close(mem);
                    continue;
                }
                mem->seek(mem, 0);
                writeToHFSFile(out, mem, dst);
                mem->close(mem);
                free(out);
                files++;
            }
            copy_attrs(child, &f->permissions);
        }
    }
    releaseCatalogRecordList(list);
}

int main(int argc, char **argv)
{
    int one = 1;
    io_func *sio, *dio;
    if (argc != 3) {
        fprintf(stderr, "usage: %s SRC.hfs DST.hfs\n", argv[0]);
        return 2;
    }
    endianness = *(char *)&one ? IS_LITTLE_ENDIAN : IS_BIG_ENDIAN;
    sio = openFlatFile(argv[1]);
    dio = openFlatFile(argv[2]);
    if (!sio || !dio) return 1;
    src = openVolume(sio);
    dst = openVolume(dio);
    if (!src || !dst) return 1;
    walk(kHFSRootFolderID, "/");
    closeVolume(dst);
    CLOSE(dio);
    closeVolume(src);
    CLOSE(sio);
    printf("copied %lu dirs, %lu files, %lu links; kept %lu existing; failed %lu\n",
           dirs, files, links, kept, failed);
    return failed ? 3 : 0;
}
