#!/bin/bash
# Link the diagnostic job against libraries in a private extracted restore root.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
root=$(realpath "${1:?Expected extracted restore root}")
out=$(realpath -m "${2:?Expected output executable}")
mkdir -p "$(dirname "$out")"
clang -target arm64-apple-ios16.7 -ffreestanding -fno-stack-protector -O2 -Wall -Wextra -Werror \
    -c "$repo/tools/asus-display-probe.c" -o "$out.o"
"${LD64_LLD:-ld64.lld-21}" -arch arm64 -platform_version ios 16.7 16.7 -syslibroot "$root" -e _main \
    -adhoc_codesign -o "$out" "$out.o" "$root/usr/lib/libSystem.B.dylib" \
    "$root/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation" \
    "$root/System/Library/Frameworks/IOSurface.framework/IOSurface" \
    "$root/System/Library/Frameworks/IOKit.framework/Versions/A/IOKit"
