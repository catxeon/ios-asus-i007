/*
 * asus-f2fs-shrink DEVICE MOUNTPOINT OLD_BLOCKS NEW_BLOCKS [--go]
 *
 * Online shrink of the mounted /data f2fs on the ASUS I007 (Linux 5.4 has
 * F2FS_IOC_RESIZE_FS). Refuses unless the on-disk superblock of DEVICE has the
 * f2fs magic and exactly OLD_BLOCKS, NEW_BLOCKS is smaller and a multiple of
 * the section size, and DEVICE is what is mounted at MOUNTPOINT. Without --go
 * it only prints the checks (dry run). After the ioctl it re-reads the
 * superblock and prints the new block count. Run as root.
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/types.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>

#define F2FS_IOCTL_MAGIC   0xf5
#define F2FS_IOC_RESIZE_FS _IOW(F2FS_IOCTL_MAGIC, 16, __u64)
#define F2FS_MAGIC         0xF2F52010u

struct sb {
	uint32_t magic, log_blocks_per_seg, segs_per_sec;
	uint64_t block_count;
};

static int read_sb(const char *dev, struct sb *s)
{
	unsigned char b[4096];
	int fd = open(dev, O_RDONLY);
	if (fd < 0 || pread(fd, b, sizeof(b), 0) != (ssize_t)sizeof(b)) {
		perror(dev);
		if (fd >= 0)
			close(fd);
		return -1;
	}
	close(fd);
	const unsigned char *p = b + 1024;
	memcpy(&s->magic, p, 4);
	memcpy(&s->log_blocks_per_seg, p + 20, 4);
	memcpy(&s->segs_per_sec, p + 24, 4);
	memcpy(&s->block_count, p + 36, 8);
	return 0;
}

static int mounted_on(const char *dev, const char *mnt)
{
	char line[1024], d[512], m[512], t[64];
	FILE *f = fopen("/proc/1/mounts", "r");
	int found = 0;
	if (!f)
		return 0;
	while (fgets(line, sizeof(line), f))
		if (sscanf(line, "%511s %511s %63s", d, m, t) == 3 &&
		    !strcmp(d, dev) && !strcmp(m, mnt) && !strcmp(t, "f2fs"))
			found = 1;
	fclose(f);
	return found;
}

int main(int argc, char **argv)
{
	if (argc < 5) {
		fprintf(stderr, "usage: %s DEVICE MOUNTPOINT OLD_BLOCKS NEW_BLOCKS [--go]\n", argv[0]);
		return 2;
	}
	const char *dev = argv[1], *mnt = argv[2];
	uint64_t old = strtoull(argv[3], NULL, 10), want = strtoull(argv[4], NULL, 10);
	int go = argc > 5 && !strcmp(argv[5], "--go");
	struct sb s;

	if (read_sb(dev, &s))
		return 1;
	uint64_t sec = (uint64_t)s.segs_per_sec << s.log_blocks_per_seg;
	printf("superblock: magic 0x%08x block_count %llu section %llu blocks\n", s.magic,
	    (unsigned long long)s.block_count, (unsigned long long)sec);
	if (s.magic != F2FS_MAGIC) {
		fprintf(stderr, "not f2fs\n");
		return 1;
	}
	if (s.block_count != old) {
		fprintf(stderr, "block_count %llu != expected %llu\n",
		    (unsigned long long)s.block_count, (unsigned long long)old);
		return 1;
	}
	if (!sec || want >= old || want % sec) {
		fprintf(stderr, "new size %llu must be smaller and a multiple of %llu\n",
		    (unsigned long long)want, (unsigned long long)sec);
		return 1;
	}
	if (!mounted_on(dev, mnt)) {
		fprintf(stderr, "%s is not the f2fs mounted at %s\n", dev, mnt);
		return 1;
	}
	printf("checks ok: shrink %s from %llu to %llu blocks (-%llu MiB)\n", mnt,
	    (unsigned long long)old, (unsigned long long)want,
	    (unsigned long long)((old - want) * 4096 >> 20));
	if (!go) {
		printf("dry run, nothing changed (add --go)\n");
		return 0;
	}
	sync();
	int fd = open(mnt, O_RDONLY | O_DIRECTORY);
	if (fd < 0) {
		perror(mnt);
		return 1;
	}
	__u64 n = want;
	if (ioctl(fd, F2FS_IOC_RESIZE_FS, &n) < 0) {
		int e = errno;
		fprintf(stderr, "F2FS_IOC_RESIZE_FS failed: %s (%d)\n", strerror(e), e);
		close(fd);
		return 1;
	}
	close(fd);
	sync();
	if (read_sb(dev, &s))
		return 1;
	printf("done: superblock block_count now %llu\n", (unsigned long long)s.block_count);
	return s.block_count == want ? 0 : 1;
}
