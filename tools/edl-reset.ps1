# Reset a Qualcomm phone out of EDL (USB 05c6:9008, "QDLoader 9008" COM port) with the
# Sahara protocol: read the device HELLO, answer HELLO_RESP in COMMAND mode, wait for
# CMD_READY, send RESET_REQ. Nothing is read from or written to the phone's storage.
#   powershell -ExecutionPolicy Bypass -File tools\edl-reset.ps1 [-Port COM9]
param([string]$Port = '')
$ErrorActionPreference = 'Stop'
if (-not $Port) {
    $dev = Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match 'QDLoader 9008 \((COM\d+)\)' } | Select-Object -First 1
    if (-not $dev) { Write-Output 'no QDLoader 9008 port'; exit 1 }
    $Port = [regex]::Match($dev.Name, 'COM\d+').Value
}
function U32($b, $o) { [BitConverter]::ToUInt32($b, $o) }
function Packet([uint32[]]$words) {
    $b = New-Object byte[] ($words.Length * 4)
    for ($i = 0; $i -lt $words.Length; $i++) { [BitConverter]::GetBytes($words[$i]).CopyTo($b, 4 * $i) }
    return ,$b
}
function ReadPacket($sp) {
    $head = New-Object byte[] 8
    $n = 0
    while ($n -lt 8) { $n += $sp.Read($head, $n, 8 - $n) }
    $len = U32 $head 4
    $rest = New-Object byte[] ([Math]::Max(0, $len - 8))
    $n = 0
    while ($n -lt $rest.Length) { $n += $sp.Read($rest, $n, $rest.Length - $n) }
    return @{ cmd = (U32 $head 0); len = $len; body = $rest }
}
$sp = New-Object System.IO.Ports.SerialPort $Port, 115200
$sp.ReadTimeout = 3000
$sp.WriteTimeout = 3000
$sp.Open()
Write-Output "opened $Port"
try {
    $hello = ReadPacket $sp
    Write-Output ("device: cmd {0} len {1} version {2} mode {3}" -f $hello.cmd, $hello.len, (U32 $hello.body 0), (U32 $hello.body 12))
    if ($hello.cmd -eq 1) {
        # HELLO_RESP: cmd 2, len 0x30, version 2, min version 1, status 0, mode 3 (command)
        $resp = Packet @(2, 0x30, 2, 1, 0, 3, 0, 0, 0, 0, 0, 0)
        $sp.Write($resp, 0, $resp.Length)
        $ready = ReadPacket $sp
        Write-Output ("device: cmd {0} len {1}" -f $ready.cmd, $ready.len)
    }
} catch {
    Write-Output "no HELLO ($($_.Exception.Message)); sending RESET anyway"
}
$reset = Packet @(7, 8)
$sp.Write($reset, 0, $reset.Length)
try {
    $ack = ReadPacket $sp
    Write-Output ("device: cmd {0} len {1} (8 = RESET_RESP)" -f $ack.cmd, $ack.len)
} catch {
    Write-Output "no RESET_RESP ($($_.Exception.Message))"
}
$sp.Close()
