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

#if defined(ASUS_CHILD_PROBE)
extern int getppid(void);
static void number(int fd, unsigned int value)
{
    char buffer[11];
    unsigned int start = sizeof(buffer);
    do {
        buffer[--start] = (char)('0' + value % 10);
        value /= 10;
    } while (value);
    write(fd, buffer + start, sizeof(buffer) - start);
}
#endif

int main(void)
{
    int fd = open("/dev/console", 2);
#if defined(ASUS_CHILD_PROBE)
    const char hello[] = "ASUS LAUNCHD CHILD: PID ";
    const char parent[] = " parent ";
    const char alive[] = "ASUS LAUNCHD CHILD: heartbeat\n";
    if (fd >= 0) {
        write(fd, hello, sizeof(hello) - 1);
        number(fd, (unsigned int)getpid());
        write(fd, parent, sizeof(parent) - 1);
        number(fd, (unsigned int)getppid());
        write(fd, "\n", 1);
    }
#else
    const char hello[] = "ASUS DYLD: Apple dyld + libSystem entered main; PID ";
    const char alive[] = "ASUS DYLD: libSystem heartbeat\n";
    char pid = (char)('0' + getpid());
    if (fd >= 0) {
        write(fd, hello, sizeof(hello) - 1);
        write(fd, &pid, 1);
        write(fd, "\n", 1);
    }
#endif
    for (;;) {
        struct timeval delay = { 5, 0 };
        select(0, (void *)0, (void *)0, (void *)0, &delay);
        if (fd >= 0) write(fd, alive, sizeof(alive) - 1);
    }
}
