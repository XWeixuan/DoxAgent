param(
    [int]$Port = 8765,
    [string]$HostName = "127.0.0.1",
    [ValidateSet("postgres", "memory")]
    [string]$StorageMode = "postgres",
    [switch]$Help
)

if ($Help) {
    Write-Host "Usage: .\scripts\debug-viewer.ps1 [-Port 8765] [-HostName 127.0.0.1] [-StorageMode postgres]"
    Write-Host "Shortcut: .\scripts\debug-viewer.ps1 8765"
    exit 0
}

function Test-PortInUse {
    param([int]$CandidatePort)

    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    return [bool]($listeners | Where-Object { $_.Port -eq $CandidatePort } | Select-Object -First 1)
}

function Get-AvailablePort {
    param([int]$StartingPort)

    for ($candidate = $StartingPort; $candidate -lt ($StartingPort + 50); $candidate++) {
        if (-not (Test-PortInUse -CandidatePort $candidate)) {
            return $candidate
        }
    }
    throw "No free port found between $StartingPort and $($StartingPort + 49)."
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$uvRoot = Join-Path $repoRoot ".tmp-uv"
$tmpDir = Join-Path $uvRoot "tmp"
$cacheDir = Join-Path $uvRoot "cache"
$pythonDir = Join-Path $uvRoot "python"

New-Item -ItemType Directory -Force -Path $tmpDir, $cacheDir, $pythonDir | Out-Null

$env:DOXAGENT_STORAGE_MODE = $StorageMode
$env:TMP = $tmpDir
$env:TEMP = $tmpDir
$env:UV_CACHE_DIR = $cacheDir
$env:UV_PYTHON_INSTALL_DIR = $pythonDir

$requestedPort = $Port
$Port = Get-AvailablePort -StartingPort $Port
if ($Port -ne $requestedPort) {
    Write-Warning "Port $requestedPort is already in use. Starting the viewer on port $Port instead."
}

Write-Host "Starting DoxAgent Brief State Viewer on http://$HostName`:$Port"
Set-Location $repoRoot
uv run python -m doxagent.debug_viewer --host $HostName --port $Port
