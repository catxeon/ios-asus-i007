#!/bin/bash
# Link only our diagnostic program; Apple libraries remain in the private root.
# Usage: build-asus-dyld-init.sh EXTRACTED_RESTORE_ROOT OUTPUT
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
root=$(realpath "${1:?Expected extracted restore root}")
out=$(realpath -m "${2:?Expected output executable}")
test -f "$root/usr/lib/dyld"
test -f "$root/usr/lib/libSystem.B.dylib"
mkdir -p "$(dirname "$out")"
clang -target arm64-apple-ios16.7 -ffreestanding -fno-stack-protector -O2 -Wall -Wextra -Werror -c "$repo/tools/asus-dyld-init.c" -o "$out.o"
"${LD64_LLD:-ld64.lld-21}" -arch arm64 -platform_version ios 16.7 16.7 -syslibroot "$root" -e _main -adhoc_codesign -o "$out" "$out.o" "$root/usr/lib/libSystem.B.dylib"
