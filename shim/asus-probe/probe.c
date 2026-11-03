/* ASUS I007 (SM8350) diagnostic only: no XNU payload or storage driver.
 * Addresses are from this device's live DT, captured 2026-11-03.
 * Inherit ABL's display configuration; never change clocks, rails or timings.
 */
#include <stdint.h>
#include "font.h"
#include "mmu.h"

#ifndef PROBE_VERSION
#define PROBE_VERSION "0007"
#endif

#define MDP 0x0ae00000UL
#define SPLASH 0xe5000000UL
#define SPLASH_END (SPLASH + 0x2300000UL)
#define WIDTH 1080U
#define HEIGHT 2448U
/* Live /soc/timer: virtual timer is GIC_PPI 11, hence INTID 16 + 11. */
#define VTIMER_INTID 27U
#define R(r) ({ uint64_t v; __asm__ volatile("mrs %0, " r : "=r"(v)); v; })
#define W(r, v) __asm__ volatile("msr " r ", %0; isb" : : "r"((uint64_t)(v)) : "memory")
static uintptr_t framebuffer;
static unsigned stride, bytes_per_pixel, row;
static uint32_t ctl_top;
static volatile unsigned faulting;
static volatile unsigned gic_active, timer_irqs, sgi_irqs, other_irqs;
extern char _head[];

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
            base + (uint64_t)pitch * HEIGHT > SPLASH_END ||
            (bpp == 4 && ((base | pitch) & 3))) continue;
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
    /* Leave ABL's autonomous refresh running. Starting CTL directly without
     * the full DSC/DSI sequencing may disrupt the inherited display pipeline.
     * This probe writes pixel RAM only, never display controller registers.
     */
}

static void pixel(unsigned x, unsigned y, unsigned on)
{
    uintptr_t address = framebuffer + y * stride + x * bytes_per_pixel;
    volatile unsigned char *p = (void *)address;
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

static int wait_clear(uintptr_t address, uint32_t mask)
{
    uint64_t until = R("cntpct_el0") + R("cntfrq_el0") / 10;
    while (read32(address) & mask) if (R("cntpct_el0") >= until) return 0;
    return 1;
}

static void test_gic(void)
{
    const uintptr_t distributor = 0x17a00000UL, redistributors = 0x17a60000UL;
    const uint32_t mask = (1U << VTIMER_INTID) | (1U << 1);
    text("GIC", "TESTING TIMER + SGI");
    present();
    W("icc_sre_el1", R("icc_sre_el1") | 1);
    if (!(R("icc_sre_el1") & 1)) { text("GIC", "NO SYSREG INTERFACE"); return; }
    uint64_t mpidr = R("mpidr_el1");
    uint32_t affinity = (mpidr & 0xffffff) | ((mpidr >> 8) & 0xff000000);
    uintptr_t redist = 0;
    for (unsigned offset = 0; offset < 0x100000; offset += 0x20000) {
        uint64_t typer = *(volatile uint64_t *)(redistributors + offset + 8);
        if ((uint32_t)(typer >> 32) == affinity) { redist = redistributors + offset; break; }
        if (typer & 16) break;
    }
    if (!redist) { text("GIC", "NO REDISTRIBUTOR"); return; }
    hex("GICR", redist);
    write32(distributor, read32(distributor) | 0x13);
    if (!wait_clear(distributor, 1U << 31)) { text("GIC", "DISTRIBUTOR TIMEOUT"); return; }
    write32(redist + 0x14, read32(redist + 0x14) & ~2U);
    if (!wait_clear(redist + 0x14, 4)) { text("GIC", "REDISTRIBUTOR ASLEEP"); return; }
    uintptr_t sgi = redist + 0x10000;
    write32(sgi + 0x180, mask); /* disable only the two test sources */
    write32(sgi + 0x280, mask); /* clear their pending state */
    write32(sgi + 0x80, read32(sgi + 0x80) | mask); /* Group 1 */
    write32(sgi + 0xd00, read32(sgi + 0xd00) & ~mask);
    *(volatile unsigned char *)(sgi + 0x400 + VTIMER_INTID) = 0xa0;
    *(volatile unsigned char *)(sgi + 0x400 + 1) = 0xa0;
    write32(sgi + 0x100, mask);
    __asm__ volatile("dsb sy" ::: "memory");
    W("icc_ctlr_el1", R("icc_ctlr_el1") & ~2UL);
    W("icc_pmr_el1", 0xf0);
    W("icc_bpr1_el1", 0);
    W("icc_igrpen1_el1", 1);
    timer_irqs = sgi_irqs = other_irqs = 0;
    gic_active = 1;
    W("cntv_tval_el0", R("cntfrq_el0") / 100);
    W("cntv_ctl_el0", 1);
    uint64_t self_sgi = ((mpidr >> 16) & 0xff) << 32 |
        ((mpidr >> 8) & 0xff) << 16 | ((mpidr >> 32) & 0xff) << 48 |
        (1UL << 24) | (1U << (mpidr & 15));
    uint64_t until = R("cntpct_el0") + R("cntfrq_el0") / 5;
    __asm__ volatile("msr daifclr, #2" ::: "memory");
    W("icc_sgi1r_el1", self_sgi);
    while (R("cntpct_el0") < until) __asm__ volatile("yield");
    __asm__ volatile("msr daifset, #2" ::: "memory");
    W("cntv_ctl_el0", 0);
    gic_active = 0;
    write32(sgi + 0x180, mask);
    text("GIC", timer_irqs >= 5 && sgi_irqs == 1 && other_irqs == 0 ? "CHECK PASS" : "CHECK FAILED");
    hex("VTIMER", timer_irqs);
    hex("SGI", sgi_irqs);
    hex("OTHER_IRQ", other_irqs);
}

void exc_handler(uint64_t slot, uint64_t *registers)
{
    (void)registers;
    if (slot == 5 && gic_active) {
        uint64_t iar = R("icc_iar1_el1");
        unsigned id = iar & 0xffffff;
        if (id >= 1020) return;
        if (id == VTIMER_INTID) {
            timer_irqs++;
            W("cntv_tval_el0", R("cntfrq_el0") / 100);
        } else if (id == 1) sgi_irqs++;
        else other_irqs++;
        W("icc_eoir1_el1", iar);
        return;
    }
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
    /* Avoid rendering the whole report with uncached instruction fetches.
     * Enable I-cache first; only one progress line precedes the MMU test.
     */
    uint64_t control = R("sctlr_el1") | (1UL << 12);
    __asm__ volatile("msr sctlr_el1, %0; isb" : : "r"(control) : "memory");
    text("MMU", "ENTERING 16K TEST");
    present();
    struct mmu_info info = {0};
    int mmu_result = -2;
    if ((R("id_aa64mmfr0_el1") >> 20) & 15) mmu_result = mmu_enable(&info);
    /* Only touch the verified inherited linear buffer in reserved splash RAM. */
    for (unsigned y = 0; y < HEIGHT; y++)
        for (unsigned x = 0; x < WIDTH; x++) pixel(x, y, 0);
    row = 0;
    text("ASUS", "I007 RAM PROBE " PROBE_VERSION);
    int mmu_pass = 0;
    if (mmu_result == -2) {
        text("MMU", "16K NOT SUPPORTED");
    } else {
        if (mmu_result) {
            hex("MMU_ERROR", (uint64_t)mmu_result);
        } else {
            uint64_t code = mmu_probe((uintptr_t)_head);
            uint64_t fb = mmu_probe(framebuffer);
            uint64_t hole = mmu_probe(0x400000000UL);
            const uint64_t pa_mask = 0x0000fffffffff000UL;
            int pass = !(code & 1) && !(fb & 1) && (hole & 1) &&
                (code & pa_mask) == ((uintptr_t)_head & pa_mask) &&
                (fb & pa_mask) == (framebuffer & pa_mask) &&
                (code >> 56) == 0xff && (fb >> 56) == 0 &&
                ((R("tcr_el1") >> 14) & 3) == 2 && (info.sctlr_after & 1);
            text("MMU", pass ? "16K CHECK PASS" : "16K CHECK FAILED");
            mmu_pass = pass;
            hex("TCR", R("tcr_el1"));
            hex("SCTLR", info.sctlr_after);
            hex("PAR_CODE", code);
            hex("PAR_FB", fb);
            hex("PAR_HOLE", hole);
        }
    }
    text("STORAGE", "NO DRIVERS / NO WRITES");
    hex("EL", R("CurrentEL"));
    hex("MIDR", R("midr_el1"));
    hex("MPIDR", R("mpidr_el1"));
    hex("MMFR0", R("id_aa64mmfr0_el1"));
    hex("CNTFRQ", R("cntfrq_el0"));
    hex("DTB", fdt);
    hex("FB", framebuffer);
    hex("STRIDE", stride);
    hex("BPP", bytes_per_pixel);
    hex("CTL_TOP", ctl_top);
    if (mmu_pass) test_gic();
    text("RESET", "ANDROID IN 120 SECONDS");
    present();
    delay(120);
    reset();
}
