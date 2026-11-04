#!/bin/bash
# Pinned, portable constant-time AES for the SWEET legacy CBC/ECB API.
set -euo pipefail
[[ "${MACHINE_CONFIG:-}" == SWEET ]] || exit 0
[[ -f libkern/crypto/corecrypto_aes.c ]] || exit 1
bearssl_sha=7bea48e5e850ab4cafbe68d3765cdaba13a86d6f
bearssl_tmp=$(mktemp -d)
trap 'rm -rf "$bearssl_tmp"' EXIT
git init -q "$bearssl_tmp"
fetched=
for attempt in 1 2 3; do
    if git -C "$bearssl_tmp" fetch -q --depth 1 \
        https://www.bearssl.org/git/BearSSL "$bearssl_sha"; then
        fetched=1
        break
    fi
    [[ $attempt == 3 ]] || sleep $((attempt * 10))
done
if [[ -n "$fetched" ]]; then
    bearssl_src="$bearssl_tmp"
    git -C "$bearssl_tmp" checkout -q FETCH_HEAD
    [[ "$(git -C "$bearssl_tmp" rev-parse HEAD)" == "$bearssl_sha" ]]
else
    # bearssl.org's git endpoint has returned 502 (2026-11-04). Fall back to the
    # subset of the same commit kept in this repository; every file must match
    # its git blob hash from the upstream tree (recorded in BLOBS).
    vendor="${BEARSSL_VENDOR:-$(cd "$(dirname "$0")/../.." && pwd)/xnu/vendor/bearssl-$bearssl_sha}"
    [[ -f "$vendor/BLOBS" ]] || { echo "BearSSL fetch failed and no vendored copy at $vendor" >&2; exit 1; }
    while read -r blob file; do
        [[ "$(git hash-object --no-filters "$vendor/$file")" == "$blob" ]] ||
            { echo "Vendored BearSSL mismatch: $file" >&2; exit 1; }
    done < "$vendor/BLOBS"
    echo "bearssl.org unreachable: using verified vendored BearSSL $bearssl_sha"
    bearssl_src="$vendor"
fi
python3 - "$bearssl_src" <<'PY'
from pathlib import Path
import shutil
import sys

source = Path(sys.argv[1])
target = Path('libkern/crypto/sweet_bearssl')
if target.exists():
    raise SystemExit(f'refusing to overwrite existing AES source: {target}')
shutil.copytree(source / 'inc', target / 'inc')
(target / 'src/symcipher').mkdir(parents=True)
for file in ('inner.h', 'config.h'):
    shutil.copy2(source / 'src' / file, target / 'src' / file)
# XNU exports machine/limits.h instead of the userspace limits.h.
header = target / 'src/inner.h'
text = header.read_text()
assert text.count('#include <limits.h>') == 1
header.write_text(text.replace('#include <limits.h>',
    '#if defined(KERNEL)\n#include <machine/limits.h>\n'
    '#else\n#include <limits.h>\n#endif'))
for name in ('aes_ct', 'aes_ct_enc', 'aes_ct_dec', 'aes_ct_cbcenc', 'aes_ct_cbcdec'):
    shutil.copy2(source / 'src/symcipher' / (name + '.c'),
                 target / 'src/symcipher' / (name + '.c'))
# MD5, SHA-1 and SHA-384/512 for the legacy hash APIs, with their codecs.
for name in ('hash/md5.c', 'codec/dec32le.c', 'codec/enc32le.c',
             'hash/sha1.c', 'codec/dec32be.c', 'codec/enc32be.c',
             'hash/sha2big.c', 'codec/dec64be.c', 'codec/enc64be.c'):
    (target / 'src' / name).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / 'src' / name, target / 'src' / name)
shutil.copy2(source / 'LICENSE.txt', target / 'LICENSE.txt')
PY
echo "Staged BearSSL portable AES, MD5, SHA-1 and SHA-384/512 ($bearssl_sha) for SWEET"
