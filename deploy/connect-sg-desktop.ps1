# Keep this window open while using the Singapore desktop.
$ErrorActionPreference = 'Stop'
$rdpPort = 14389
if (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $rdpPort -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port $rdpPort is already listening. Use mstsc /v:127.0.0.1:$rdpPort if it is the existing SG tunnel."
    exit 1
}
Write-Host "Connecting to doxagent-sg. Open mstsc /v:127.0.0.1:$rdpPort after SSH connects. Ctrl+C stops the tunnel."
while ($true) {
    & ssh -N -o BatchMode=yes -o ConnectTimeout=15 -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L "127.0.0.1:${rdpPort}:127.0.0.1:3389" doxagent-sg
    Write-Host "SSH tunnel exited ($LASTEXITCODE); retrying in 5 seconds. Check Clash if this repeats."
    Start-Sleep -Seconds 5
}
