/* Static PID 1 bootstrap: attach stdio to console, then exec Apple launchd.
 * It stays alive on exec failure so the error can be observed.
 * Wrapped by mkinit.py; no external relocations or writable static data.
 */
    .text
    .globl _start
    .p2align 2
_start:
    mov x9, sp
    and x9, x9, #~15
    mov sp, x9
    adr x0, console
    mov x1, #2
    mov x2, #0
    mov x16, #5
    svc #0x80
    b.cs hang
    mov x19, x0
    mov x20, #0
dup_loop:
    mov x0, x19
    mov x1, x20
    mov x16, #90
    svc #0x80
    add x20, x20, #1
    cmp x20, #3
    b.ne dup_loop
    adr x1, hello
    adr x2, hello_end
    sub x2, x2, x1
    bl write_console
    sub sp, sp, #32
    adr x0, target
    str x0, [sp]
    str xzr, [sp, #8]
    str xzr, [sp, #16]
    mov x1, sp
    add x2, sp, #16
    mov x16, #59
    svc #0x80
    adr x1, failed
    adr x2, failed_end
    sub x2, x2, x1
    bl write_console
hang:
    mov x0, #0
    mov x1, #0
    mov x2, #0
    mov x3, #0
    adr x4, delay
    mov x16, #93
    svc #0x80
    b hang
write_console:
    mov x0, x19
    mov x16, #4
    svc #0x80
    ret
    .p2align 3
delay:
    .quad 5, 0
console:
    .asciz "/dev/console"
target:
    .asciz "/sbin/launchd"
hello:
    .ascii "ASUS BOOTSTRAP: exec Apple launchd as PID 1\n"
hello_end:
failed:
    .ascii "ASUS BOOTSTRAP: exec failed; holding for diagnostics\n"
failed_end:
