$ErrorActionPreference = 'Stop'
$mainRoot = [IO.Path]::GetFullPath('C:\Users\WEIXUANXIE\Desktop\DoxAgent')
$reviewRoot = $PSScriptRoot
$archiveRoot = [IO.Path]::GetFullPath('D:\DoxAgentArchives\worktree-retirement-20261111')
$trees = @(Get-Content -LiteralPath (Join-Path $reviewRoot 'trees.json') -Raw | ConvertFrom-Json)
$preflight = @(Get-Content -LiteralPath (Join-Path $reviewRoot 'retirement-preflight.json') -Raw | ConvertFrom-Json)
$archive = Get-Content -LiteralPath (Join-Path $reviewRoot 'archive-verification.json') -Raw | ConvertFrom-Json
if ($trees.Count -ne 18 -or $preflight.Count -ne 18 -or @($preflight | Where-Object {-not $_.ready}).Count) { throw 'Unapproved worktree scope or failed preflight' }
if ($archive.files -ne 484) { throw 'Incomplete evidence archive' }
[xml]$tests = Get-Content -LiteralPath (Join-Path $reviewRoot 'migration-tests.xml') -Raw
$failures = @($tests.SelectNodes('//testsuite') | Where-Object {[int]$_.failures -gt 0 -or [int]$_.errors -gt 0})
if ($failures.Count) { throw 'Migration regressions did not pass' }
if (-not (Test-Path -LiteralPath (Join-Path $mainRoot 'docs/frontend-bus-source-and-runtime-latency-audit-plan-20261111.md'))) { throw 'Missing A document' }
if (-not (Select-String -LiteralPath (Join-Path $mainRoot 'tests/test_bus_dashboard.py') -SimpleMatch 'def test_recent_business_one_message_keeps_a_real_zero_body_ratio')) { throw 'Missing A test' }

function Invoke-CheckedGit([string]$worktree, [string[]]$gitArgs) {
    $output = & git --no-optional-locks -c core.longpaths=true -C $worktree @gitArgs 2>> (Join-Path $reviewRoot 'retirement-git-stderr.log')
    if ($LASTEXITCODE -ne 0) { throw "Git command failed in $worktree : $gitArgs" }
    return $output
}

if ((Invoke-CheckedGit $mainRoot @('branch','--show-current')) -ne 'main') { throw 'Main directory branch changed' }
$beforeRefs = @(Invoke-CheckedGit $mainRoot @('for-each-ref','--format=%(refname) %(objectname)','refs/heads'))
$beforeRefs | Set-Content -LiteralPath (Join-Path $reviewRoot 'retirement-branches-before.txt') -Encoding utf8
$reviewZip = [IO.Compression.ZipFile]::OpenRead((Join-Path $reviewRoot 'review-evidence.zip'))
try {
    $entry = $reviewZip.GetEntry('file-decisions.csv')
    $reader = [IO.StreamReader]::new($entry.Open(), [Text.Encoding]::UTF8, $true)
    try { $decisions = @($reader.ReadToEnd() | ConvertFrom-Csv) } finally { $reader.Dispose() }
    $manifest = @{}
    foreach ($row in $decisions) {
        $root = [IO.Path]::GetFullPath($row.worktree)
        if (-not $manifest.ContainsKey($root)) { $manifest[$root] = @{} }
        $manifest[$root][$row.relative_path] = $row
    }
    $decisions = $null
} finally { $reviewZip.Dispose() }

# Recheck every archived destination before deleting any source evidence.
$archiveBySource = @{}
foreach ($row in $archive.verified) {
    $target = [IO.Path]::GetFullPath($row.target)
    if (-not $target.StartsWith($archiveRoot+'\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid archive path' }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw "Archive changed: $target" }
    $archiveBySource[[IO.Path]::GetFullPath($row.source)] = $row
}

$results = @()
if (Test-Path -LiteralPath (Join-Path $reviewRoot 'retirement-results.json')) {
    $results = @(Get-Content -LiteralPath (Join-Path $reviewRoot 'retirement-results.json') -Raw | ConvertFrom-Json)
}
$registeredPaths = @(Invoke-CheckedGit $mainRoot @('worktree','list','--porcelain') | Where-Object { $_ -like 'worktree *' } | ForEach-Object {[IO.Path]::GetFullPath($_.Substring(9))})
foreach ($tree in $trees) {
    $target = [IO.Path]::GetFullPath($tree.path)
    if ($target -notin $registeredPaths) {
        Write-Output "Already unregistered; separately verify any filesystem residue: $target"
        continue
    }
    $guard = @($preflight | Where-Object { [IO.Path]::GetFullPath($_.path) -eq $target })
    if ($guard.Count -ne 1 -or $target -eq $mainRoot -or -not (Test-Path -LiteralPath $target -PathType Container)) { throw "Invalid target: $target" }
    $resolved = (Resolve-Path -LiteralPath $target).Path
    if ($resolved -ne $target) { throw "Target resolution changed: $target" }
    if ((Invoke-CheckedGit $target @('rev-parse','HEAD')) -ne $tree.head) { throw "Target HEAD changed: $target" }
    if (@(Invoke-CheckedGit $target @('rev-list','origin/main..HEAD')).Count) { throw "Unique commits appeared: $target" }
    if (@(Invoke-CheckedGit $target @('diff','--cached','--name-only')).Count) { throw "Staged changes appeared: $target" }
    $processes = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pytest|uv|node|git)' })
    foreach ($process in $processes) {
        if ($process.CommandLine -and $process.CommandLine.Replace('/', '\').IndexOf($target, [StringComparison]::OrdinalIgnoreCase) -ge 0) { throw "Active process references $target" }
    }

    $expected = @{}
    if ($manifest.ContainsKey($target)) { $expected = $manifest[$target] }
    $untracked = @(Invoke-CheckedGit $target @('-c','core.quotePath=false','ls-files','--others','--exclude-standard'))
    $ignored = @(Invoke-CheckedGit $target @('-c','core.quotePath=false','ls-files','--others','--ignored','--exclude-standard'))
    $current = @($untracked + $ignored)
    if ($current.Count -ne $expected.Count) {
        $currentSet = @{}
        foreach ($relative in $current) { $currentSet[$relative] = $true }
        $missing = @($expected.Keys | Where-Object { -not $currentSet.ContainsKey($_) })
        foreach ($relative in $missing) {
            $disposition = $expected[$relative].class
            if ($disposition -eq 'B' -and -not $archiveBySource.ContainsKey([IO.Path]::GetFullPath((Join-Path $target $relative)))) { throw 'Missing B source has no verified archive' }
            if ($disposition -notin @('A','B','C','D')) { throw 'Unreviewed file disappeared' }
        }
        Write-Output "$($tree.key): $($missing.Count) already-disposed reviewed files; A migration/B archives reverified, no new files permitted."
    }
    # Check all known non-cache file hashes again immediately before removal.
    foreach ($relative in $current) {
        if (-not $expected.ContainsKey($relative)) { throw "New unreviewed file in $target : $relative" }
        $file = [IO.Path]::GetFullPath((Join-Path $target $relative))
        if (-not $file.StartsWith($target+'\', [StringComparison]::OrdinalIgnoreCase)) { throw 'File escaped worktree' }
        $entry = $expected[$relative]
        if ($entry.sha256 -and (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) { throw "Content changed: $file" }
    }

    $dirty = @(Invoke-CheckedGit $target @('diff','--name-only'))
    if ($tree.key -eq 'DoxAgent-w1-index-20261106') {
        $approvedChanges = @($tree.changes | ForEach-Object {$_.path})
        if (@(Compare-Object $dirty $approvedChanges).Count) { throw 'W1 change list drifted' }
        # Exact 11 previously reviewed changes only. No hard reset or clean.
        Invoke-CheckedGit $target (@('restore','--source=HEAD','--staged','--worktree','--') + $approvedChanges) | Out-Null
    } elseif ($dirty.Count) { throw "Unreviewed tracked changes: $target" }

    # Remove only the enumerated, approved extra files. Ignored files are left
    # for Git's normal worktree removal; no direct recursive .codex deletion.
    $done = 0
    foreach ($relative in $untracked) {
        $file = [IO.Path]::GetFullPath((Join-Path $target $relative))
        if (-not $file.StartsWith($target+'\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid deletion path' }
        $decision = $expected[$relative]
        if ($null -eq $decision) { throw "Missing exact approved file decision: $file" }
        if ($decision.class -eq 'B' -and -not $archiveBySource.ContainsKey($file)) { throw "Evidence not archived: $file" }
        if ($decision.class -notin @('A','B','C','D')) { throw "Invalid disposition: $file" }
        $fileInfo = Get-Item -LiteralPath $file -Force
        if (-not ($fileInfo.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            $blockingAttributes = [IO.FileAttributes]::ReadOnly -bor [IO.FileAttributes]::Hidden -bor [IO.FileAttributes]::System
            if ($fileInfo.Attributes -band $blockingAttributes) {
                [IO.File]::SetAttributes($file, ($fileInfo.Attributes -band (-bnot $blockingAttributes)))
            }
        }
        Remove-Item -LiteralPath $file
        $done++
        if ($done % 1000 -eq 0) { Write-Output "$($tree.key): cleared $done reviewed files" }
    }
    if (@(Invoke-CheckedGit $target @('status','--porcelain','--untracked-files=all')).Count) { throw "Worktree is not clean before normal removal: $target" }
    Write-Output "Removing worktree normally: $target"
    Invoke-CheckedGit $mainRoot @('worktree','remove',$target) | Out-Null
    if (Test-Path -LiteralPath $target) { throw "Directory remains after remove: $target" }
    $results += [pscustomobject]@{path=$target; head=$tree.head; untracked_files_cleared=$done; removed=$true; at=[DateTimeOffset]::Now.ToString('o')}
    $results | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $reviewRoot 'retirement-results.json') -Encoding utf8
    Write-Output "Removed: $($tree.key)"
}
$afterRefs = @(Invoke-CheckedGit $mainRoot @('for-each-ref','--format=%(refname) %(objectname)','refs/heads'))
$afterRefs | Set-Content -LiteralPath (Join-Path $reviewRoot 'retirement-branches-after.txt') -Encoding utf8
if (@(Compare-Object $beforeRefs $afterRefs).Count) { throw 'Branch refs changed; inspect before proceeding' }
if ((Invoke-CheckedGit $mainRoot @('branch','--show-current')) -ne 'main') { throw 'Main branch changed' }
Write-Output "All $($results.Count) approved physical worktrees retired; all local branches retained."
