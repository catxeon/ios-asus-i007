/* ASUS I007 (SM8350) diagnostic only: no XNU payload or storage driver.
 * Addresses are from this device's live DT, captured 2026-11-03.
 * Inherit ABL's display configuration; never change clocks, rails or timings.
 */
#include <stdint.h>
#include "font.h"

#define MDP 0x0ae00000UL
#define SPLASH 0xe5000000UL
#define SPLASH_END (SPLASH + 0x2300000UL)
#define WIDTH 1080U
#define HEIGHT 2448U
#define R(r) ({ uint64_t v; __asm__ volatile("mrs %0, " r : "=r"(v)); v; })
static uintptr_t framebuffer;
static unsigned stride, bytes_per_pixel, row;
static uint32_t ctl_top;
static volatile unsigned faulting;

static uint32_t read32(uintptr_t address) { return *(volatile uint32_t *)address; }
static void write32(uintptr_t address, uint32_t value) { *(volatile uint32_t *)address = value; }
static uint32_t be32(uintptr_t address) { return __builtin_bswap32(read32(address)); }

static void delay(unsigned seconds)
{
    uint64_t until = R("cntpct_el0") + R("cntfrq_el0") * seconds;
    while (R("cntpct_el0") < until) __asm__ volatile("yield");
}

static void __attribute__((noreturn)) reset(void)
{
    register uint64_t x0 __asm__("x0") = 0x84000009; /* PSCI SYSTEM_RESET */
    __asm__ volatile("dsb sy; smc #0" : "+r"(x0) : : "x1", "x2", "x3", "memory");
    for (;;) __asm__ volatile("wfe");
}

/* Require the actual PICASSO model before accessing any device-specific MMIO. */
static int is_picasso(uintptr_t fdt)
{
    if (!fdt || (fdt & 3) || be32(fdt) != 0xd00dfeed) return 0;
    uint32_t size = be32(fdt + 4), off = be32(fdt + 8), strings = be32(fdt + 12);
    if (size < 40 || size > 0x200000 || off >= size || strings >= size) return 0;
    uintptr_t p = fdt + off, end = fdt + size;
    if (p + 8 > end || be32(p) != 1 || *(const char *)(p + 4)) return 0;
    p += 8; /* root BEGIN_NODE and its empty, padded name */
    while (p + 12 <= end) {
        uint32_t token = be32(p);
        p += 4;
        if (token == 4) continue;
        if (token != 3) return 0;
        uint32_t len = be32(p), name = be32(p + 4);
        p += 8;
        if (p + len > end || name >= size - strings) return 0;
        const char *key = (const char *)(fdt + strings + name);
        if (size - strings - name >= 6 && key[0] == 'm' && key[1] == 'o' &&
            key[2] == 'd' && key[3] == 'e' && key[4] == 'l' && key[5] == 0) {
            const char *model = (const char *)p;
            for (unsigned i = 0; i + 7 <= len; i++) {
                if (model[i] == 'P' && model[i+1] == 'I' && model[i+2] == 'C' &&
                    model[i+3] == 'A' && model[i+4] == 'S' && model[i+5] == 'S' &&
                    model[i+6] == 'O') return 1;
            }
            return 0;
        }
        p = (p + len + 3) & ~3UL;
    }
    return 0;
}

static int find_framebuffer(void)
{
    /* qcom,sde-sspp-off from the live DT. Offsets within SSPP are documented
     * in ASUS's published sde_hw_sspp.c: ADDR=0x14, STRIDE=0x24, FORMAT=0x30.
     */
    const unsigned offsets[] = {0x5000, 0x7000, 0x9000, 0xb000, 0x25000, 0x27000, 0x29000, 0x2b000};
    for (unsigned i = 0; i < sizeof(offsets)/sizeof(offsets[0]); i++) {
        uintptr_t pipe = MDP + offsets[i];
        uintptr_t base = read32(pipe + 0x14);
        unsigned pitch = read32(pipe + 0x24) & 0xffff;
        uint32_t format = read32(pipe + 0x30);
        unsigned bpp = ((format >> 9) & 3) + 1;
        unsigned size = read32(pipe);
        if (base < SPLASH || base >= SPLASH_END || (bpp != 3 && bpp != 4) ||
            (format >> 30) || ((format >> 19) & 3) ||
            (size >> 16) != HEIGHT || pitch < WIDTH * bpp || pitch > 8192 ||
            base + (uint64_t)pitch * HEIGHT > SPLASH_END) continue;
        if (!framebuffer || base < framebuffer) {
            framebuffer = base;
            stride = pitch;
            bytes_per_pixel = bpp;
        }
    }
    return framebuffer != 0;
}

static void present(void)
{
    __asm__ volatile("dsb sy" ::: "memory");
    /* CTL0 is the primary controller in the live DT. No flush needed: only
     * pixel memory changed. Kick inherited command-mode scanout if configured.
     */
    if (ctl_top & (1U << 17)) write32(MDP + 0x16000 + 0x1c, 1);
}

static void pixel(unsigned x, unsigned y, unsigned on)
{
    volatile unsigned char *p = (void *)(framebuffer + y * stride + x * bytes_per_pixel);
    for (unsigned c = 0; c < bytes_per_pixel; c++) p[c] = on ? 0xff : 0;
}

static void text(const char *label, const char *value)
{
    unsigned column = 0;
    for (unsigned part = 0; part < 2; part++) {
        const char *s = part ? value : label;
        if (part) column = 10;
        while (*s && column < 42) {
            unsigned ch = (unsigned char)*s++;
            if (ch < 32 || ch > 126) ch = '?';
            for (unsigned y = 0; y < 16; y++)
                for (unsigned x = 0; x < 8; x++)
                    for (unsigned sy = 0; sy < 3; sy++)
                        for (unsigned sx = 0; sx < 3; sx++)
                            pixel(24 + column * 24 + x * 3 + sx,
                                  24 + row * 48 + y * 3 + sy,
                                  font8x16[ch - 32][y] & (0x80 >> x));
            column++;
        }
    }
    row++;
}

static void hex(const char *label, uint64_t value)
{
    char buffer[17];
    const char *digits = "0123456789ABCDEF";
    for (unsigned i = 0; i < 16; i++) buffer[i] = digits[(value >> (60 - i * 4)) & 15];
    buffer[16] = 0;
    text(label, buffer);
}

void __attribute__((noreturn)) exc_handler(uint64_t slot, uint64_t *registers)
{
    (void)registers;
    if (!faulting++ && framebuffer) {
        hex("EXCEPTION", slot);
        hex("ESR_EL1", R("esr_el1"));
        hex("ELR_EL1", R("elr_el1"));
        hex("FAR_EL1", R("far_el1"));
        present();
        delay(30);
    }
    reset();
}

void __attribute__((noreturn)) secondary_main(uint64_t index)
{
    (void)index;
    for (;;) __asm__ volatile("wfe");
}

void __attribute__((noreturn)) shim_main(uintptr_t fdt)
{
    if (R("CurrentEL") != 4 || !is_picasso(fdt)) { delay(5); reset(); }
    ctl_top = read32(MDP + 0x16000 + 0x14);
    if (!find_framebuffer()) { delay(10); reset(); }
    /* Only touch the verified inherited linear buffer in reserved splash RAM. */
    for (unsigned y = 0; y < HEIGHT; y++)
        for (unsigned x = 0; x < WIDTH; x++) pixel(x, y, 0);
    text("ASUS", "I007 RAM PROBE 0001");
    text("STORAGE", "NO DRIVERS / NO WRITES");
    hex("EL", R("CurrentEL"));
    hex("MIDR", R("midr_el1"));
    hex("MPIDR", R("mpidr_el1"));
    hex("MMFR0", R("id_aa64mmfr0_el1"));
    hex("MMFR1", R("id_aa64mmfr1_el1"));
    hex("PFR0", R("id_aa64pfr0_el1"));
    hex("ISAR0", R("id_aa64isar0_el1"));
    hex("ISAR1", R("id_aa64isar1_el1"));
    hex("CNTFRQ", R("cntfrq_el0"));
    hex("DTB", fdt);
    hex("FB", framebuffer);
    hex("STRIDE", stride);
    hex("BPP", bytes_per_pixel);
    hex("CTL_TOP", ctl_top);
    text("RESET", "ANDROID IN 45 SECONDS");
    present();
    delay(45);
    reset();
}
