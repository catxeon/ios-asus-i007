/* A controlled PID 1 for testing Apple's dyld + standalone libSystem.
 * No restore programs, launch jobs or device access beyond /dev/console.
 * Declared Darwin ABI types keep the source buildable without an Apple SDK.
 */
typedef unsigned long size_t;
typedef long ssize_t;
struct timeval { long tv_sec; int tv_usec; };
extern int open(const char *, int, ...);
extern ssize_t write(int, const void *, size_t);
extern int getpid(void);
extern int select(int, void *, void *, void *, struct timeval *);

int main(void)
{
    int fd = open("/dev/console", 2);
    const char hello[] = "ASUS DYLD: Apple dyld + libSystem entered main; PID ";
    const char alive[] = "ASUS DYLD: libSystem heartbeat\n";
    char pid = (char)('0' + getpid());
    if (fd >= 0) {
        write(fd, hello, sizeof(hello) - 1);
        write(fd, &pid, 1);
        write(fd, "\n", 1);
    }
    for (;;) {
        struct timeval delay = { 5, 0 };
        select(0, (void *)0, (void *)0, (void *)0, &delay);
        if (fd >= 0) write(fd, alive, sizeof(alive) - 1);
    }
}
