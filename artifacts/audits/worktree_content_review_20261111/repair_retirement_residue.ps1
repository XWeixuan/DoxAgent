$ErrorActionPreference = 'Stop'
$mainRoot = 'C:\Users\WEIXUANXIE\Desktop\DoxAgent'
$reviewRoot = $PSScriptRoot
$targets = @(
    @{key='bus-metrics-reading'; path='C:\Users\WEIXUANXIE\.codex\worktrees\bus-metrics-reading\DoxAgent'; branch='codex/agent-mcp-release'; head='3d8bc755e13a56442a7df7d3e42c496d82b7a6ad'},
    @{key='DoxAgentDialogFix'; path='D:\DoxAgentDialogFix'; branch='codex/hedge-account-form'; head='d05727507a45551c58076523abbc277cd7258fd7'}
)
function Checked-Git([string]$root, [string[]]$gitArgs) {
    & git --no-optional-locks -c core.longpaths=true -C $root @gitArgs 2>> (Join-Path $reviewRoot 'residue-repair.log')
    if ($LASTEXITCODE -ne 0) { throw "Git recovery failed: $gitArgs" }
}
foreach ($target in $targets) {
    $original = [IO.Path]::GetFullPath($target.path)
    if (-not (Test-Path -LiteralPath $original -PathType Container)) { throw 'Unexpected residue state' }
    if (@(Get-ChildItem -LiteralPath $original -Force).Count -eq 0) {
        $registered = @(Checked-Git $mainRoot @('worktree','list','--porcelain') | Where-Object { $_ -like 'worktree *' } | ForEach-Object {[IO.Path]::GetFullPath($_.Substring(9))})
        if ($original -in $registered) { throw 'Empty directory is still registered' }
        $results = @(Get-Content -LiteralPath (Join-Path $reviewRoot 'retirement-results.json') -Raw | ConvertFrom-Json)
        $results += [pscustomobject]@{path=$original; head=$target.head; removed=$false; registered=$false; empty_directory_remaining=$true; remaining_files=0; at=[DateTimeOffset]::Now.ToString('o')}
        $results | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $reviewRoot 'retirement-results.json') -Encoding utf8
        $emptyRecovery = [IO.Path]::GetFullPath((Join-Path $mainRoot ('.tmp\retirement-repair-'+$target.key)))
        if ($emptyRecovery.StartsWith($mainRoot+'\.tmp\') -and (Test-Path -LiteralPath $emptyRecovery)) {
            if (@(Get-ChildItem -LiteralPath $emptyRecovery -Force).Count) { throw 'Recovery container is not empty' }
            Remove-Item -LiteralPath $emptyRecovery
        }
        Write-Output "Git registration and all files removed; protected empty directory remains: $original"
        continue
    }
    if (Test-Path -LiteralPath (Join-Path $original '.git')) { throw 'Residue has regained Git ownership; inspect before proceeding' }
    if (Test-Path -LiteralPath (Join-Path $original 'frontend\v2\node_modules')) { throw 'Shared dependency link must already be detached' }
    if ((Checked-Git $mainRoot @('rev-parse',$target.branch)) -ne $target.head) { throw 'Branch moved' }
    $temporary = [IO.Path]::GetFullPath((Join-Path $mainRoot ('.tmp\retirement-repair-'+$target.key)))
    if (-not $temporary.StartsWith($mainRoot+'\.tmp\', [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $temporary)) { throw 'Invalid recovery directory' }
    Checked-Git $mainRoot @('worktree','add','--no-checkout',$temporary,$target.branch) | Out-Null
    # Move only the Git-generated pointer file, not any Codex session data.
    Move-Item -LiteralPath (Join-Path $temporary '.git') -Destination (Join-Path $original '.git')
    Checked-Git $mainRoot @('worktree','repair',$original) | Out-Null
    if ((Checked-Git $original @('rev-parse','HEAD')) -ne $target.head) { throw 'Repair changed HEAD' }
    Checked-Git $original @('read-tree','HEAD') | Out-Null
    # Rehydrate this already-absorbed commit so ordinary remove can verify clean.
    Checked-Git $original @('restore','--source=HEAD','--worktree','--','.') | Out-Null
    if (@(Checked-Git $original @('status','--porcelain','--untracked-files=all')).Count) { throw 'Repaired worktree is not clean' }
    Checked-Git $mainRoot @('worktree','remove',$original) | Out-Null
    if (Test-Path -LiteralPath $original) { throw 'Normal Git removal left residue again' }
    if (@(Get-ChildItem -LiteralPath $temporary -Force).Count -ne 0) { throw 'Recovery directory is not empty' }
    Remove-Item -LiteralPath $temporary
    $results = @(Get-Content -LiteralPath (Join-Path $reviewRoot 'retirement-results.json') -Raw | ConvertFrom-Json)
    $results += [pscustomobject]@{path=$original; head=$target.head; removed=$true; recovered_git_link=$true; at=[DateTimeOffset]::Now.ToString('o')}
    $results | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $reviewRoot 'retirement-results.json') -Encoding utf8
    Write-Output "Repaired Git link and normally removed: $original"
}
