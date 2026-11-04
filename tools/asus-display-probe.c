/* Exercise IOSurface and the ASUS IOMobileFramebuffer swap path from launchd.
 * The Darwin ABI declarations keep this probe buildable without an Apple SDK.
 */
#include <stddef.h>
#include <stdint.h>

typedef const void *CFRef;
typedef struct __CFDictionary *CFMutableDictionaryRef;
typedef struct __IOSurface *IOSurfaceRef;
typedef uint32_t io_object_t;
typedef int32_t kern_return_t;

extern CFMutableDictionaryRef CFDictionaryCreateMutable(CFRef, long, const void *, const void *);
extern void CFDictionarySetValue(CFMutableDictionaryRef, CFRef, CFRef);
extern CFRef CFNumberCreate(CFRef, long, const void *);
extern void CFRelease(CFRef);
extern const CFRef kIOSurfaceWidth, kIOSurfaceHeight, kIOSurfaceBytesPerElement, kIOSurfacePixelFormat;
extern IOSurfaceRef IOSurfaceCreate(CFRef);
extern kern_return_t IOSurfaceLock(IOSurfaceRef, uint32_t, uint32_t *);
extern kern_return_t IOSurfaceUnlock(IOSurfaceRef, uint32_t, uint32_t *);
extern void *IOSurfaceGetBaseAddress(IOSurfaceRef);
extern size_t IOSurfaceGetBytesPerRow(IOSurfaceRef);
extern size_t IOSurfaceGetAllocSize(IOSurfaceRef);
extern uint32_t IOSurfaceGetID(IOSurfaceRef);

extern CFMutableDictionaryRef IOServiceMatching(const char *);
extern io_object_t IOServiceGetMatchingService(uint32_t, CFRef);
extern kern_return_t IOServiceOpen(io_object_t, uint32_t, uint32_t, io_object_t *);
extern kern_return_t IOServiceClose(io_object_t);
extern kern_return_t IOObjectRelease(io_object_t);
extern kern_return_t IOConnectCallMethod(io_object_t, uint32_t,
    const uint64_t *, uint32_t, const void *, size_t, uint64_t *, uint32_t *, void *, size_t *);
extern uint32_t mach_task_self_;
extern int dprintf(int, const char *, ...);
extern int open(const char *, int, ...);
extern int getpid(void), getppid(void);
extern unsigned sleep(unsigned);

enum {
    WIDTH = 768, HEIGHT = 384, PIXEL_BGRA = 0x42475241, CF_NUMBER_SINT32 = 3,
    GET_DISPLAY_SIZE = 8, SWAP_BEGIN = 4, SWAP_END = 5, SWAP_BYTES = 0x46c,
};

static IOSurfaceRef make_surface(void)
{
    CFRef keys[] = {kIOSurfaceWidth, kIOSurfaceHeight, kIOSurfaceBytesPerElement, kIOSurfacePixelFormat};
    const int32_t values[] = {WIDTH, HEIGHT, 4, PIXEL_BGRA};
    CFRef numbers[4] = {0};
    IOSurfaceRef surface = NULL;
    CFMutableDictionaryRef dict = CFDictionaryCreateMutable(NULL, 4, NULL, NULL);
    if (!dict) return NULL;
    for (size_t i = 0; i < 4; ++i) {
        numbers[i] = CFNumberCreate(NULL, CF_NUMBER_SINT32, &values[i]);
        if (!numbers[i]) goto done;
        CFDictionarySetValue(dict, keys[i], numbers[i]);
    }
    surface = IOSurfaceCreate(dict);
done:
    CFRelease(dict);
    for (size_t i = 0; i < 4; ++i) {
        if (numbers[i]) CFRelease(numbers[i]);
    }
    return surface;
}

static int fail(int fd, const char *stage, kern_return_t result)
{
    dprintf(fd, "ASUS DISPLAY: %s failed (0x%08x)\n", stage, (unsigned)result);
    return 1;
}

static int draw(int fd, io_object_t display, IOSurfaceRef surface)
{
    uint64_t geometry[2] = {0};
    uint32_t count = 2;
    kern_return_t kr = IOConnectCallMethod(display, GET_DISPLAY_SIZE, NULL, 0, NULL, 0,
        geometry, &count, NULL, NULL);
    if (kr || count != 2 || geometry[0] != 1080 || geometry[1] != 2448) {
        return fail(fd, "display geometry", kr);
    }
    const uint32_t colors[] = {0xffe83929, 0xff33c75b, 0xff3164f1};
    size_t stride = IOSurfaceGetBytesPerRow(surface);
    uint32_t id = IOSurfaceGetID(surface);
    if (!id || stride < WIDTH * 4 || stride % 4 || stride > IOSurfaceGetAllocSize(surface) / HEIGHT) {
        return fail(fd, "surface layout", 0);
    }
    dprintf(fd, "ASUS DISPLAY: IOSurface %u, %llu x %llu\n", id,
        (unsigned long long)geometry[0], (unsigned long long)geometry[1]);
    for (uint32_t frame = 0;; ++frame) {
        kr = IOSurfaceLock(surface, 0, NULL);
        if (kr) return fail(fd, "IOSurfaceLock", kr);
        uint8_t *pixels = IOSurfaceGetBaseAddress(surface);
        if (!pixels || (uintptr_t)pixels % 4) {
            IOSurfaceUnlock(surface, 0, NULL);
            return fail(fd, "surface address", 0);
        }
        for (size_t y = 0; y < HEIGHT; ++y) {
            uint32_t *row = (uint32_t *)(pixels + y * stride);
            for (size_t x = 0; x < WIDTH; ++x) row[x] = colors[(x / (WIDTH / 3) + frame % 3) % 3];
        }
        kr = IOSurfaceUnlock(surface, 0, NULL);
        if (kr) return fail(fd, "IOSurfaceUnlock", kr);
        uint64_t swap_id = 0;
        count = 1;
        kr = IOConnectCallMethod(display, SWAP_BEGIN, NULL, 0, NULL, 0, &swap_id, &count, NULL, NULL);
        if (kr || count != 1 || !swap_id) return fail(fd, "SwapBegin", kr);
        uint32_t record[SWAP_BYTES / 4] = {0};
        record[0x50 / 4] = (uint32_t)swap_id;
        record[0x54 / 4] = id;
        record[0x6c / 4] = record[0xcc / 4] = WIDTH;
        record[0x70 / 4] = record[0xd0 / 4] = HEIGHT;
        record[0xc4 / 4] = (1080 - WIDTH) / 2;
        record[0xc8 / 4] = 800;
        record[0x104 / 4] = 1;
        kr = IOConnectCallMethod(display, SWAP_END, NULL, 0, record, sizeof(record), NULL, NULL, NULL, NULL);
        if (kr) return fail(fd, "SwapEnd", kr);
        dprintf(fd, "ASUS DISPLAY: submitted frame %u\n", frame);
        sleep(4);
    }
}

int main(void)
{
    int fd = open("/dev/console", 2);
    if (fd < 0) fd = 2;
    dprintf(fd, "ASUS DISPLAY: PID %d parent %d\n", getpid(), getppid());
    io_object_t service = 0;
    for (int attempt = 0; attempt < 10 && !service; ++attempt) {
        CFMutableDictionaryRef match = IOServiceMatching("IOMobileFramebuffer");
        if (!match) return fail(fd, "IOServiceMatching", 0);
        service = IOServiceGetMatchingService(0, match);
        if (!service) sleep(1);
    }
    if (!service) return fail(fd, "IOMobileFramebuffer lookup", 0);
    io_object_t display = 0;
    kern_return_t kr = IOServiceOpen(service, mach_task_self_, 0, &display);
    IOObjectRelease(service);
    if (kr) return fail(fd, "IOServiceOpen", kr);
    IOSurfaceRef surface = make_surface();
    int result = surface ? draw(fd, display, surface) : fail(fd, "IOSurfaceCreate", 0);
    if (surface) CFRelease(surface);
    IOServiceClose(display);
    return result;
}
