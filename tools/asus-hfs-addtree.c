/* SPDX-License-Identifier: GPL-3.0-only
 * Small host utility linked with planetbeing/libdmg-hfsplus (GPL-3.0).
 * Copies a host directory tree into a local HFS+ image file, never a device:
 *   asus-hfs-addtree IMAGE SRCDIR DEST [--exclude /PATH ...]
 * Directories, regular files and symbolic links keep their mode, uid and gid
 * (lstat; links are not followed). Entries that already exist in the image
 * are left untouched, so files placed earlier win. Other file types are
 * skipped. Failures are reported per path and counted; the exit status is 0
 * only when every entry was copied or deliberately skipped.
 */
#include <hfs/hfsplus.h>
#include <hfs/hfslib.h>
#include <dirent.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

char endianness;

static Volume *volume;
static char **excludes;
static int nexcludes;
static unsigned long added_dirs, added_files, added_links, kept, skipped, failed;

static int excluded(const char *dest)
{
    for (int i = 0; i < nexcludes; i++) {
        size_t n = strlen(excludes[i]);
        if (strncmp(dest, excludes[i], n) == 0 && (dest[n] == '\0' || dest[n] == '/'))
            return 1;
    }
    return 0;
}

static int exists(const char *dest)
{
    HFSPlusCatalogRecord *record = getRecordFromPath3(dest, volume, NULL, NULL, FALSE, FALSE, kHFSRootFolderID);
    if (record == NULL)
        return 0;
    free(record);
    return 1;
}

static void set_owner_mode(const char *dest, const struct stat *st)
{
    if (!chmodFile(dest, st->st_mode & 07777, volume) ||
        !chownFile(dest, (uint32_t)st->st_uid, (uint32_t)st->st_gid, volume)) {
        fprintf(stderr, "attr failed: %s\n", dest);
        failed++;
    }
}

static void copy_file(const char *src, const char *dest, const struct stat *st)
{
    HFSCatalogNodeID cnid = newFile(dest, volume);
    if (cnid == 0) {
        fprintf(stderr, "create failed: %s\n", dest);
        failed++;
        return;
    }
    FILE *in = fopen(src, "rb");
    AbstractFile *file = in ? createAbstractFileFromFile(in) : NULL;
    HFSPlusCatalogFile *record = (HFSPlusCatalogFile *)getRecordByCNID(cnid, volume);
    if (file == NULL || record == NULL) {
        fprintf(stderr, "write failed: %s\n", dest);
        failed++;
        if (file) file->close(file);
        else if (in) fclose(in);
        free(record);
        return;
    }
    writeToHFSFile(record, file, volume);
    file->close(file);
    free(record);
    set_owner_mode(dest, st);
    added_files++;
}

static void walk(const char *src, const char *dest)
{
    DIR *dir = opendir(src);
    struct dirent *ent;
    if (dir == NULL) {
        fprintf(stderr, "opendir failed: %s\n", src);
        failed++;
        return;
    }
    while ((ent = readdir(dir)) != NULL) {
        char s[PATH_MAX], d[PATH_MAX], target[PATH_MAX];
        struct stat st;
        if (!strcmp(ent->d_name, ".") || !strcmp(ent->d_name, ".."))
            continue;
        snprintf(s, sizeof(s), "%s/%s", src, ent->d_name);
        snprintf(d, sizeof(d), "%s/%s", strcmp(dest, "/") ? dest : "", ent->d_name);
        if (excluded(d)) {
            skipped++;
            continue;
        }
        if (lstat(s, &st) != 0) {
            fprintf(stderr, "lstat failed: %s\n", s);
            failed++;
            continue;
        }
        if (S_ISDIR(st.st_mode)) {
            if (!exists(d)) {
                if (newFolder(d, volume) == 0) {
                    fprintf(stderr, "mkdir failed: %s\n", d);
                    failed++;
                    continue;
                }
                set_owner_mode(d, &st);
                added_dirs++;
            }
            walk(s, d);
        } else if (exists(d)) {
            kept++;
        } else if (S_ISREG(st.st_mode)) {
            copy_file(s, d, &st);
        } else if (S_ISLNK(st.st_mode)) {
            ssize_t n = readlink(s, target, sizeof(target) - 1);
            if (n < 0 || !makeSymlink(d, (target[n] = '\0', target), volume)) {
                fprintf(stderr, "symlink failed: %s\n", d);
                failed++;
                continue;
            }
            added_links++;
        } else {
            skipped++;
        }
    }
    closedir(dir);
}

int main(int argc, char **argv)
{
    int one = 1;
    io_func *io;
    if (argc < 4) {
        fprintf(stderr, "usage: %s IMAGE SRCDIR DEST [--exclude /PATH ...]\n", argv[0]);
        return 2;
    }
    excludes = calloc((size_t)argc, sizeof(char *));
    for (int i = 4; i < argc; i++) {
        if (strcmp(argv[i], "--exclude") == 0 && i + 1 < argc)
            excludes[nexcludes++] = argv[++i];
        else
            return 2;
    }
    endianness = *(char *)&one ? IS_LITTLE_ENDIAN : IS_BIG_ENDIAN;
    io = openFlatFile(argv[1]);
    if (!io) return 1;
    volume = openVolume(io);
    if (!volume) { CLOSE(io); return 1; }
    walk(argv[2], argv[3]);
    closeVolume(volume);
    CLOSE(io);
    printf("added %lu dirs, %lu files, %lu links; kept %lu existing; skipped %lu; failed %lu\n",
           added_dirs, added_files, added_links, kept, skipped, failed);
    return failed ? 3 : 0;
}
