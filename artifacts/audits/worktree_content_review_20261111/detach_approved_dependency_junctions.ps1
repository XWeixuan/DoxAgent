$ErrorActionPreference = 'Stop'
$trees = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'trees.json') -Raw | ConvertFrom-Json)
$records = @()
foreach ($tree in $trees) {
    $treeRoot = [IO.Path]::GetFullPath($tree.path)
    if ($treeRoot -eq 'C:\Users\WEIXUANXIE\Desktop\DoxAgent') { throw 'Main directory is excluded' }
    $dependencyRoot = [IO.Path]::GetFullPath((Join-Path $treeRoot 'frontend\v2\node_modules'))
    if (-not $dependencyRoot.StartsWith($treeRoot+'\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid cache path' }
    if (-not (Test-Path -LiteralPath $dependencyRoot)) { continue }
    $rootInfo = Get-Item -LiteralPath $dependencyRoot -Force
    if ($rootInfo.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        $linkTarget = $rootInfo.Target
        [IO.Directory]::Delete($dependencyRoot)
        $records += [pscustomobject]@{worktree=$treeRoot; dependency_root=$dependencyRoot; junctions_unlinked=1; root_junction_target=$linkTarget}
        Write-Output "$($tree.key): unlinked dependency root junction without touching its target"
        continue
    }
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($dependencyRoot)
    $count = 0
    while ($pending.Count) {
        $directory = $pending.Pop()
        foreach ($item in [IO.DirectoryInfo]::new($directory).GetFileSystemInfos()) {
            $path = [IO.Path]::GetFullPath($item.FullName)
            if (-not $path.StartsWith($dependencyRoot+'\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Cache entry escaped approved root' }
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                # Nonrecursive deletion unlinks the junction itself, never its target.
                if ($item.Attributes -band [IO.FileAttributes]::Directory) { [IO.Directory]::Delete($path) }
                else { [IO.File]::Delete($path) }
                $count++
            } elseif ($item.Attributes -band [IO.FileAttributes]::Directory) {
                $pending.Push($path)
            }
        }
    }
    $records += [pscustomobject]@{worktree=$treeRoot; dependency_root=$dependencyRoot; junctions_unlinked=$count}
    Write-Output "$($tree.key): unlinked $count dependency links without following targets"
}
$records | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'junction-detachment.json') -Encoding utf8
