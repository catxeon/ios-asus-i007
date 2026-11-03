/* ASUS I007: prepare the verified GIC and enter a RAM-only XNU payload.
 * No storage/peripheral driver, no secondary CPU, no display-register writes.
 * Enter XNU with MMU/data cache off, IRQs masked, and the ABL display intact.
 */
#include <stdint.h>
#include "console.h"
#include "loader.h"

#define R(r) ({ uint64_t v; __asm__ volatile("mrs %0, " r : "=r"(v)); v; })
#define W(r, v) __asm__ volatile("msr " r ", %0; isb" : : "r"((uint64_t)(v)) : "memory")
static unsigned console_ready, faulting;
static uint32_t read32(uintptr_t p) { return *(volatile uint32_t *)p; }
static void write32(uintptr_t p, uint32_t v) { *(volatile uint32_t *)p = v; }
static uint32_t be32(uintptr_t p) { return __builtin_bswap32(read32(p)); }

static void delay(unsigned seconds)
{
    uint64_t until = R("cntpct_el0") + R("cntfrq_el0") * seconds;
    while (R("cntpct_el0") < until) __asm__ volatile("yield");
}

static void __attribute__((noreturn)) reset(void)
{
    register uint64_t x0 __asm__("x0") = 0x84000009;
    register uint64_t x1 __asm__("x1") = 0;
    register uint64_t x2 __asm__("x2") = 0;
    register uint64_t x3 __asm__("x3") = 0;
    __asm__ volatile("dsb sy; smc #0" : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3) : : "memory");
    for (;;) __asm__ volatile("wfe");
}

static int is_picasso(uintptr_t fdt)
{
    if (!fdt || (fdt & 3) || be32(fdt) != 0xd00dfeed) return 0;
    uint32_t size = be32(fdt + 4), off = be32(fdt + 8), strings = be32(fdt + 12);
    if (size < 40 || size > 0x200000 || off >= size || strings >= size) return 0;
    uintptr_t p = fdt + off, end = fdt + size;
    if (p + 8 > end || be32(p) != 1 || *(const char *)(p + 4)) return 0;
    p += 8;
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
            for (unsigned i = 0; i + 7 <= len; i++)
                if (model[i] == 'P' && model[i+1] == 'I' && model[i+2] == 'C' &&
                    model[i+3] == 'A' && model[i+4] == 'S' && model[i+5] == 'S' &&
                    model[i+6] == 'O') return 1;
            return 0;
        }
        p = (p + len + 3) & ~3UL;
    }
    return 0;
}

static int display_matches(void)
{
    const unsigned pipes[] = {0x5000, 0x7000, 0x9000, 0xb000, 0x25000, 0x27000, 0x29000, 0x2b000};
    for (unsigned i = 0; i < sizeof(pipes)/sizeof(pipes[0]); i++) {
        uintptr_t pipe = 0x0ae00000UL + pipes[i];
        uint32_t format = read32(pipe + 0x30);
        if (read32(pipe + 0x14) == FB_BASE &&
            (read32(pipe + 0x24) & 0xffff) == FB_STRIDE &&
            (read32(pipe) >> 16) == FB_H && !(format >> 30) &&
            !((format >> 19) & 3) && ((format >> 9) & 3) == 3) return 1;
    }
    return 0;
}

static int wait_clear(uintptr_t p, uint32_t mask)
{
    uint64_t until = R("cntpct_el0") + R("cntfrq_el0") / 10;
    while (read32(p) & mask) if (R("cntpct_el0") >= until) return 0;
    return 1;
}

static int prepare_gic(void)
{
    W("icc_sre_el1", R("icc_sre_el1") | 1);
    if (!(R("icc_sre_el1") & 1)) return 0;
    uint64_t mpidr = R("mpidr_el1");
    uint32_t affinity = (mpidr & 0xffffff) | ((mpidr >> 8) & 0xff000000);
    uintptr_t redist = 0;
    for (unsigned off = 0; off < 0x100000; off += 0x20000) {
        uint64_t typer = *(volatile uint64_t *)(0x17a60000UL + off + 8);
        if ((uint32_t)(typer >> 32) == affinity) { redist = 0x17a60000UL + off; break; }
        if (typer & 16) break;
    }
    if (!redist) return 0;
    write32(0x17a00000, read32(0x17a00000) | 0x13);
    if (!wait_clear(0x17a00000, 1U << 31)) return 0;
    write32(redist + 0x14, read32(redist + 0x14) & ~2U);
    if (!wait_clear(redist + 0x14, 4)) return 0;
    uintptr_t sgi = redist + 0x10000;
    uint32_t mask = (1U << 27) | (1U << 1);
    write32(sgi + 0x80, read32(sgi + 0x80) | mask);
    write32(sgi + 0xd00, read32(sgi + 0xd00) & ~mask);
    *(volatile unsigned char *)(sgi + 0x400 + 27) = 0xa0;
    *(volatile unsigned char *)(sgi + 0x400 + 1) = 0xa0;
    write32(sgi + 0x100, mask);
    W("icc_ctlr_el1", R("icc_ctlr_el1") & ~2UL);
    W("icc_pmr_el1", 0xf0);
    W("icc_bpr1_el1", 0);
    W("icc_igrpen1_el1", 1);
    W("cntv_ctl_el0", 0);
    __asm__ volatile("dsb sy" ::: "memory");
    return 1;
}

void __attribute__((noreturn)) exc_handler(uint64_t slot, uint64_t *regs)
{
    (void)regs;
    if (console_ready && !faulting++) {
        con_hex("FAULT", slot);
        con_hex("ESR", R("esr_el1"));
        con_hex("ELR", R("elr_el1"));
        con_hex("FAR", R("far_el1"));
        delay(90);
    }
    reset();
}

void __attribute__((noreturn)) secondary_main(uint64_t id)
{
    (void)id;
    for (;;) __asm__ volatile("wfe");
}

void __attribute__((noreturn)) shim_main(uintptr_t fdt)
{
    if (R("CurrentEL") != 4 || (R("mpidr_el1") & 0xffffff) != 0 ||
        ((R("midr_el1") >> 4) & 0xfff) != 0xd05 || !is_picasso(fdt) || !display_matches()) {
        delay(5);
        reset();
    }
    con_clear(0xff000000);
    console_ready = 1;
    con_str("ASUS", "I007 XNU RAM BOOT 0001");
    con_str("STORAGE", "no storage driver; root in RAM");
    con_hex("MIDR", R("midr_el1"));
    if (!prepare_gic()) {
        con_str("GIC", "prepare failed; reset in 90s");
        delay(90);
        reset();
    }
    con_str("GIC", "ready: virtual timer INTID 27");
    con_str("LOADER", loader_boot(fdt));
    delay(90);
    reset();
}
