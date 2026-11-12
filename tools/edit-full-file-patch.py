#!/usr/bin/env python3
"""Edit a file that a patch creates in full (a hunk "@@ -0,0 +1,N @@"), keeping N right.

  edit-full-file-patch.py PATCH FILE EDITS.py

EDITS.py is run with `text` (the file's text) and `once(old, new)` (replace exactly one
occurrence, else stop); whatever `text` holds afterwards goes back into PATCH, with the hunk
header counted again. Hand edits of these patches that left N alone cut the file short in CI
(kernel CI 42949072293: "unterminated conditional directive").
"""
import re
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    path, name, edits = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    data = path.read_bytes()
    crlf = b'\r\n' in data
    lines = data.replace(b'\r\n', b'\n').split(b'\n')
    at = lines.index(b'+++ b/' + name.encode())
    m = re.fullmatch(rb'@@ -0,0 \+1,(\d+) @@', lines[at + 1])
    if not m:
        sys.exit(f'{name}: not created in full by {path}')
    n = int(m.group(1))
    body = lines[at + 2:at + 2 + n]
    if not all(l.startswith(b'+') for l in body):
        sys.exit(f'{name}: hunk of {n} lines does not hold {n} added lines')
    scope = {'text': b'\n'.join(l[1:] for l in body).decode('latin-1') + '\n'}

    def once(old, new):
        count = scope['text'].count(old)
        if count != 1:
            sys.exit(f'{name}: {count} occurrences of {old[:80]!r}')
        scope['text'] = scope['text'].replace(old, new)

    scope['once'] = once
    exec(compile(edits.read_text(encoding='utf-8'), str(edits), 'exec'), scope)
    new = scope['text'].encode('latin-1').split(b'\n')
    if new[-1] != b'':
        sys.exit(f'{name}: the edited text must end with a newline')
    new = [b'+' + l for l in new[:-1]]
    lines[at + 1] = b'@@ -0,0 +1,%d @@' % len(new)
    lines[at + 2:at + 2 + n] = new
    out = b'\n'.join(lines)
    path.write_bytes(out.replace(b'\n', b'\r\n') if crlf else out)
    print(f'{path.name}: {name}: {n} -> {len(new)} lines')


if __name__ == '__main__':
    main()
