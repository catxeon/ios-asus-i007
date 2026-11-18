#!/usr/bin/env python3
"""PC side of the phone's USB network (bsd/dev/asus_usb.c, boot-arg asususb=2), Windows.

  asus-usbnet.py OUT.txt [--seconds N] [--all]

Waits for the phone's NCM adapter (Windows' UsbNcm driver, bound through the phone's
MS OS "WINNCM" descriptor) to get a link-local IPv4 169.254.x.y, pings the phone at
169.254.7.2 from it, then asks the phone for its live console log (UDP 5140: a datagram
from us starts the stream, "all" = the whole 4 MiB ring, otherwise the last 64 KiB)
and appends what arrives to OUT.txt until --seconds pass (default 900) or the phone
is silent for 60 s after it had talked. Everything it sees goes to stdout too, one
line per state change, so it can run in the background next to tools/asus-phone-cycle.sh.
Nothing on the PC is configured: no address, no firewall rule (replies to our own
datagrams pass).
"""
import argparse
import json
import socket
import subprocess
import sys
import time

PHONE = '169.254.7.2'
PORT = 5140


def adapters():
    """[(name, description, status, [ipv4...])] of adapters that look like the phone's."""
    ps = ('Get-NetAdapter -IncludeHidden | Where-Object { $_.InterfaceDescription -match "NCM|ASUS iOS" } | '
          'ForEach-Object { $ips = @(Get-NetIPAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4 '
          '-ErrorAction SilentlyContinue | ForEach-Object { $_.IPAddress }); '
          '[pscustomobject]@{n=$_.Name; d=$_.InterfaceDescription; s=[string]$_.Status; ip=$ips} } | '
          'ConvertTo-Json -Compress')
    r = subprocess.run(['powershell.exe', '-NoProfile', '-Command', ps], capture_output=True, text=True, timeout=60)
    out = r.stdout.strip()
    if not out:
        return []
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]
    res = []
    for a in data:
        ips = a.get('ip') or []
        if isinstance(ips, str):
            ips = [ips]
        res.append((a.get('n'), a.get('d'), a.get('s'), ips))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--seconds', type=int, default=900)
    ap.add_argument('--all', action='store_true', help='ask for the whole 4 MiB ring')
    a = ap.parse_args()
    t0 = time.time()
    seen = None
    local = None
    while time.time() - t0 < a.seconds:
        try:
            found = adapters()
        except Exception as e:          # noqa: BLE001 - keep polling
            found = []
            print('%5.0fs adapter query failed: %s' % (time.time() - t0, e), flush=True)
        state = repr(found)
        if state != seen:
            print('%5.0fs adapters: %s' % (time.time() - t0, found or 'none'), flush=True)
            seen = state
        for name, desc, status, ips in found:
            ll = [ip for ip in ips if ip.startswith('169.254.')]
            if status == 'Up' and ll:
                local = ll[0]
        if local:
            break
        time.sleep(3)
    if not local:
        print('no NCM adapter with a link-local address', flush=True)
        return 1
    print('%5.0fs local address %s' % (time.time() - t0, local), flush=True)
    r = subprocess.run(['ping', '-n', '4', '-S', local, PHONE], capture_output=True, text=True, timeout=60)
    print(r.stdout.strip(), flush=True)

    so = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    so.bind((local, 0))
    so.settimeout(1.0)
    first = b'all' if a.all else b'tail'
    so.sendto(first, (PHONE, PORT))
    last_sent = time.time()
    last_rx = None
    total = 0
    with open(a.out, 'ab') as f:
        while time.time() - t0 < a.seconds:
            try:
                data, _ = so.recvfrom(65536)
            except socket.timeout:
                data = b''
            except OSError as e:
                print('%5.0fs receive error %s' % (time.time() - t0, e), flush=True)
                data = b''
                time.sleep(1)
            if data:
                if last_rx is None:
                    print('%5.0fs first log bytes from the phone' % (time.time() - t0), flush=True)
                f.write(data)
                f.flush()
                total += len(data)
                last_rx = time.time()
            now = time.time()
            if now - last_sent > 3:             # keepalive (and a retry before the first answer)
                try:
                    so.sendto(b'hello' if last_rx else first, (PHONE, PORT))
                except OSError as e:            # the adapter went away: the phone reset itself
                    print('%5.0fs adapter gone (%s)' % (now - t0, e), flush=True)
                    break
                last_sent = now
            if last_rx and now - last_rx > 60:
                print('%5.0fs phone silent for 60 s' % (now - t0), flush=True)
                break
    print('%5.0fs %d log bytes in %s' % (time.time() - t0, total, a.out), flush=True)
    return 0 if total else 2


if __name__ == '__main__':
    sys.exit(main())
