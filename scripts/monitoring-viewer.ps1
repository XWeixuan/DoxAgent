param(
    [int] $Port = 8766
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $root
try {
    uv run python -m doxagent.monitoring_viewer --port $Port
}
finally {
    Pop-Location
}
