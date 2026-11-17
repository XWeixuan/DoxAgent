$ErrorActionPreference = 'Stop'
$reviewRoot = $PSScriptRoot
$archiveRoot = [IO.Path]::GetFullPath('D:\DoxAgentArchives\worktree-retirement-20261111')
$archivePrefix = $archiveRoot.TrimEnd('\') + '\'
$rows = @(Import-Csv -LiteralPath (Join-Path $reviewRoot 'archive-files.csv') -Encoding utf8)
if ($rows.Count -ne 484) { throw 'Unexpected archive scope' }
$preflight = @(Get-Content -LiteralPath (Join-Path $reviewRoot 'retirement-preflight.json') -Raw | ConvertFrom-Json)
if ($preflight.Count -ne 18 -or @($preflight | Where-Object { -not $_.ready }).Count) { throw 'Preflight not ready' }
$processes = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pytest|uv|node|git)' })
$activeReferences = @()
foreach ($tree in $preflight) {
    foreach ($process in $processes) {
        if ($process.CommandLine -and $process.CommandLine.Replace('/', '\').IndexOf($tree.path, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $activeReferences += [pscustomobject]@{path=$tree.path; pid=$process.ProcessId; name=$process.Name}
        }
    }
}
if ($activeReferences.Count) { $activeReferences | ConvertTo-Json | Write-Output; throw 'A target path is referenced by a running process' }
$before = @('C:\','D:\') | ForEach-Object { $drive = [IO.DriveInfo]::new($_); [pscustomobject]@{drive=$_; free=$drive.AvailableFreeSpace; total=$drive.TotalSize} }
$before | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reviewRoot 'disk-before.json') -Encoding utf8
$verified = @()
foreach ($row in $rows) {
    $sourceRoot = [IO.Path]::GetFullPath($row.worktree)
    $source = [IO.Path]::GetFullPath((Join-Path $sourceRoot $row.relative_path))
    $target = [IO.Path]::GetFullPath($row.target)
    if (-not $source.StartsWith($sourceRoot.TrimEnd('\')+'\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Source escaped its worktree' }
    if (-not $target.StartsWith($archivePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Destination escaped archive root' }
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing evidence: $source" }
    if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw "Source evidence changed: $source" }
    if (Test-Path -LiteralPath $target) {
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw "Archive collision: $target" }
    } else {
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($target)) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $target
    }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw "Archive verification failed: $target" }
    if ((Get-Item -LiteralPath $target).Length -ne [long]$row.bytes) { throw "Archive size mismatch: $target" }
    $verified += [pscustomobject]@{source=$source; target=$target; bytes=[long]$row.bytes; sha256=$row.sha256}
}
$receipt = [pscustomobject]@{at=[DateTimeOffset]::Now.ToString('o'); archive_root=$archiveRoot; files=$verified.Count; bytes=($verified | Measure-Object -Property bytes -Sum).Sum; verified=$verified}
$receipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $reviewRoot 'archive-verification.json') -Encoding utf8
$receipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $archiveRoot 'archive-verification.json') -Encoding utf8
Copy-Item -LiteralPath (Join-Path $reviewRoot 'assessment.md') -Destination (Join-Path $archiveRoot 'approved-assessment.md')
Copy-Item -LiteralPath (Join-Path $reviewRoot 'archive-files.csv') -Destination (Join-Path $archiveRoot 'archive-files.csv')
[pscustomobject]@{verified_files=$receipt.files; bytes=$receipt.bytes; archive_root=$archiveRoot; active_target_processes=$activeReferences.Count} | ConvertTo-Json -Compress
