<#
.SYNOPSIS
Records one ticker-scoped CDECR run's container, process-tree, stage, and WSL usage.

.EXAMPLE
pwsh -File scripts/cdecr-resource-monitor.ps1 `
  -ContainerName doxagent-v2-local-v2-initialization-1 `
  -Ticker NVDA

.DESCRIPTION
Run this script in a foreground terminal before submitting CDECR. It creates no scheduled
task and stops after the matching CDECR child exits plus the configured post-run window.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$ContainerName,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Ticker,

    [ValidatePattern('^[A-Za-z0-9._:-]*$')]
    [string]$ExecutionId = '',

    [string]$OutputRoot = '.tmp/cdecr-resource-monitor',

    [ValidateRange(1, 60)]
    [int]$SampleIntervalSeconds = 2,

    [ValidateRange(0, 3600)]
    [int]$BaselineSeconds = 120,

    [ValidateRange(1, 86400)]
    [int]$WaitTimeoutSeconds = 1800,

    [ValidateRange(0, 3600)]
    [int]$PostRunSeconds = 120,

    [ValidateRange(1, 172800)]
    [int]$MaxDurationSeconds = 43200
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Utf8NoBom {
    param([string]$Path, [string]$Value)
    [System.IO.File]::WriteAllText(
        $Path,
        $Value,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Append-Utf8NoBom {
    param([string]$Path, [string]$Value)
    [System.IO.File]::AppendAllText(
        $Path,
        $Value,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Get-Percentile {
    param([object[]]$Values, [double]$Percentile)
    $numbers = @($Values | Where-Object { $null -ne $_ } | ForEach-Object { [double]$_ } | Sort-Object)
    if ($numbers.Count -eq 0) { return $null }
    $index = [Math]::Ceiling(($Percentile / 100.0) * $numbers.Count) - 1
    $index = [Math]::Max(0, [Math]::Min($numbers.Count - 1, $index))
    return $numbers[$index]
}

function Get-Maximum {
    param([object[]]$Values)
    $numbers = @($Values | Where-Object { $null -ne $_ } | ForEach-Object { [double]$_ })
    if ($numbers.Count -eq 0) { return $null }
    return ($numbers | Measure-Object -Maximum).Maximum
}

function Get-Minimum {
    param([object[]]$Values)
    $numbers = @($Values | Where-Object { $null -ne $_ } | ForEach-Object { [double]$_ })
    if ($numbers.Count -eq 0) { return $null }
    return ($numbers | Measure-Object -Minimum).Minimum
}

function Get-Average {
    param([object[]]$Values)
    $numbers = @($Values | Where-Object { $null -ne $_ } | ForEach-Object { [double]$_ })
    if ($numbers.Count -eq 0) { return $null }
    return ($numbers | Measure-Object -Average).Average
}

function Format-MiB {
    param($Bytes)
    if ($null -eq $Bytes) { return 'n/a' }
    return ('{0:N1} MiB' -f ([double]$Bytes / 1MB))
}

function Format-Number {
    param($Value)
    if ($null -eq $Value) { return 'n/a' }
    return ('{0:N3}' -f [double]$Value)
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'docker.exe is not available on PATH'
}

$containerId = (& docker inspect --format '{{.Id}}' $ContainerName 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or -not $containerId) {
    throw "Docker container '$ContainerName' does not exist"
}
$running = (& docker inspect --format '{{.State.Running}}' $ContainerName 2>$null).Trim()
if ($running -ne 'true') {
    throw "Docker container '$ContainerName' is not running"
}

$Ticker = $Ticker.ToUpperInvariant()
$timestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
$outputBase = [System.IO.Path]::GetFullPath((Join-Path $OutputRoot "$Ticker-$timestamp"))
[System.IO.Directory]::CreateDirectory($outputBase) | Out-Null
$samplesPath = Join-Path $outputBase 'samples.jsonl'
$metadataPath = Join-Path $outputBase 'metadata.json'
$summaryPath = Join-Path $outputBase 'summary.json'
$reportPath = Join-Path $outputBase 'report.md'

$pythonProbe = @'
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

CGROUP = Path('/sys/fs/cgroup')
TICKER = os.environ.get('CDECR_MONITOR_TICKER', '').upper()
EXECUTION_ID = os.environ.get('CDECR_MONITOR_EXECUTION_ID', '')
PATTERN = 'doxagent.ticker_initialization.cdecr_process'

def text(path):
    try:
        return Path(path).read_text(encoding='utf-8', errors='replace').strip()
    except (OSError, PermissionError):
        return None

def number(path):
    value = text(path)
    if value in (None, '', 'max'):
        return None
    try:
        return int(value)
    except ValueError:
        return None

def key_values(path):
    output = {}
    value = text(path)
    if not value:
        return output
    for line in value.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                output[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return output

def colon_values_kib(path):
    output = {}
    value = text(path)
    if not value:
        return output
    for line in value.splitlines():
        if ':' not in line:
            continue
        key, raw = line.split(':', 1)
        parts = raw.strip().split()
        if parts and parts[0].isdigit():
            output[key] = int(parts[0]) * 1024
    return output

def process_record(pid):
    proc = Path('/proc') / str(pid)
    cmd_raw = (proc / 'cmdline').read_bytes()
    cmd = cmd_raw.replace(b'\0', b' ').decode('utf-8', errors='replace').strip()
    # status values such as VmRSS have a trailing kB token; parse them separately.
    status_text = text(proc / 'status') or ''
    status_fields = {}
    for line in status_text.splitlines():
        if ':' not in line:
            continue
        name, raw = line.split(':', 1)
        first = raw.strip().split()
        if first and first[0].isdigit():
            status_fields[name] = int(first[0])
    stat_text = text(proc / 'stat') or ''
    tail = stat_text[stat_text.rfind(')') + 2:].split()
    ppid = int(tail[1]) if len(tail) > 12 else 0
    cpu_ticks = (int(tail[11]) + int(tail[12])) if len(tail) > 12 else 0
    rollup = colon_values_kib(proc / 'smaps_rollup')
    try:
        fd_count = len(list((proc / 'fd').iterdir()))
    except OSError:
        fd_count = None
    return {
        'pid': pid,
        'ppid': ppid,
        'cmd': cmd,
        'cpu_ticks': cpu_ticks,
        'rss_bytes': status_fields.get('VmRSS', 0) * 1024,
        'pss_bytes': rollup.get('Pss', 0),
        'threads': status_fields.get('Threads', 0),
        'fd_count': fd_count,
    }

records = {}
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    try:
        item = process_record(int(entry.name))
        records[item['pid']] = item
    except (OSError, ValueError, IndexError):
        pass

def input_path_from_cmd(cmd):
    parts = cmd.split()
    try:
        return Path(parts[parts.index('--input') + 1])
    except (ValueError, IndexError):
        return None

def matches_target(item):
    if PATTERN not in item['cmd']:
        return False
    input_path = input_path_from_cmd(item['cmd'])
    if input_path is None or not input_path.is_file():
        return False
    if EXECUTION_ID and EXECUTION_ID not in str(input_path):
        return False
    try:
        payload = json.loads(input_path.read_text(encoding='utf-8'))
        return str(payload.get('binding', {}).get('ticker', '')).upper() == TICKER
    except (OSError, ValueError, TypeError):
        return False

root_pids = sorted(pid for pid, item in records.items() if matches_target(item))
selected = set(root_pids)
changed = True
while changed:
    changed = False
    for pid, item in records.items():
        if item['ppid'] in selected and pid not in selected:
            selected.add(pid)
            changed = True
selected_records = [records[pid] for pid in sorted(selected) if pid in records]

dispatch_input = None
if root_pids:
    dispatch_input = input_path_from_cmd(records[root_pids[-1]]['cmd'])
if dispatch_input is None:
    dispatch_root = Path('/data/initialization/cdecr-dispatches')
    candidates = []
    if dispatch_root.is_dir():
        for candidate in dispatch_root.glob('*/input.json'):
            try:
                payload = json.loads(candidate.read_text(encoding='utf-8'))
                if str(payload.get('binding', {}).get('ticker', '')).upper() != TICKER:
                    continue
                if EXECUTION_ID and EXECUTION_ID not in str(candidate):
                    continue
                candidates.append(candidate)
            except (OSError, ValueError, TypeError):
                pass
    if candidates:
        dispatch_input = max(candidates, key=lambda path: path.stat().st_mtime)

stage = None
registry_path = None
if dispatch_input is not None:
    try:
        payload = json.loads(dispatch_input.read_text(encoding='utf-8'))
        registry_path = payload.get('binding', {}).get('registry_path')
        if registry_path and Path(registry_path).is_file():
            uri = 'file:' + quote(str(Path(registry_path))) + '?mode=ro'
            connection = sqlite3.connect(uri, uri=True, timeout=0.1)
            connection.row_factory = sqlite3.Row
            try:
                row = connection.execute(
                    'SELECT epoch_id,status,current_stage,updated_at '
                    'FROM bulk_epochs ORDER BY updated_at DESC LIMIT 1'
                ).fetchone()
                if row is not None:
                    stage = dict(row)
            finally:
                connection.close()
    except (OSError, ValueError, TypeError, sqlite3.Error):
        pass

memory_stat = key_values(CGROUP / 'memory.stat')
memory_events = key_values(CGROUP / 'memory.events')
cpu_stat = key_values(CGROUP / 'cpu.stat')
io_totals = {'rbytes': 0, 'wbytes': 0, 'rios': 0, 'wios': 0}
io_text = text(CGROUP / 'io.stat') or ''
for line in io_text.splitlines():
    for token in line.split()[1:]:
        if '=' not in token:
            continue
        key, raw = token.split('=', 1)
        if key in io_totals:
            try:
                io_totals[key] += int(raw)
            except ValueError:
                pass

output = {
    'cgroup': {
        'memory_current_bytes': number(CGROUP / 'memory.current'),
        'memory_peak_bytes': number(CGROUP / 'memory.peak'),
        'memory_swap_current_bytes': number(CGROUP / 'memory.swap.current'),
        'memory_anon_bytes': memory_stat.get('anon'),
        'memory_file_bytes': memory_stat.get('file'),
        'memory_kernel_bytes': memory_stat.get('kernel'),
        'oom': memory_events.get('oom', 0),
        'oom_kill': memory_events.get('oom_kill', 0),
        'cpu_usage_usec': cpu_stat.get('usage_usec'),
        'cpu_user_usec': cpu_stat.get('user_usec'),
        'cpu_system_usec': cpu_stat.get('system_usec'),
        'pids_current': number(CGROUP / 'pids.current'),
        'io': io_totals,
    },
    'process': {
        'target_seen': bool(root_pids),
        'root_pids': root_pids,
        'process_count': len(selected_records),
        'rss_bytes': sum(item['rss_bytes'] for item in selected_records),
        'pss_bytes': sum(item['pss_bytes'] for item in selected_records),
        'cpu_ticks': sum(item['cpu_ticks'] for item in selected_records),
        'threads': sum(item['threads'] for item in selected_records),
        'fd_count': sum(item['fd_count'] or 0 for item in selected_records),
    },
    'dispatch_input': str(dispatch_input) if dispatch_input else None,
    'registry_path': registry_path,
    'stage': stage,
    'clock_ticks_per_second': os.sysconf('SC_CLK_TCK'),
}
print(json.dumps(output, separators=(',', ':')))
'@

$probeEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pythonProbe))
$probeBootstrap = "import base64;exec(base64.b64decode('$probeEncoded'))"
$startedAt = (Get-Date).ToUniversalTime()
$metadata = [ordered]@{
    schema_version = 'cdecr_resource_monitor.v1'
    ticker = $Ticker
    execution_id = if ($ExecutionId) { $ExecutionId } else { $null }
    container_name = $ContainerName
    container_id = $containerId
    started_at = $startedAt.ToString('o')
    sample_interval_seconds = $SampleIntervalSeconds
    baseline_seconds = $BaselineSeconds
    wait_timeout_seconds = $WaitTimeoutSeconds
    post_run_seconds = $PostRunSeconds
    max_duration_seconds = $MaxDurationSeconds
    logical_processors = [Environment]::ProcessorCount
    output_directory = $outputBase
    note = 'Container metrics include the lightweight docker-exec probe; process metrics exclude it.'
}
Write-Utf8NoBom $metadataPath (($metadata | ConvertTo-Json -Depth 8) + "`n")

$records = New-Object 'System.Collections.Generic.List[object]'
$seenTarget = $false
$runStartedAt = $null
$runEndedAt = $null
$postDeadline = $null
$outcome = 'RUNNING'
$previousTimestamp = $null
$previousContainerCpu = $null
$previousProcessCpu = $null
$lastProgress = $startedAt.AddSeconds(-30)

Write-Host "Monitoring ticker=$Ticker container=$ContainerName"
Write-Host "Output: $outputBase"
Write-Host "Collecting up to $BaselineSeconds seconds of baseline; start CDECR in another terminal."

while ($true) {
    $sampleStarted = (Get-Date).ToUniversalTime()
    $elapsed = ($sampleStarted - $startedAt).TotalSeconds
    if ($elapsed -ge $MaxDurationSeconds) {
        $outcome = 'MAX_DURATION_REACHED'
        break
    }

    $probeOutput = & docker exec `
        -e "CDECR_MONITOR_TICKER=$Ticker" `
        -e "CDECR_MONITOR_EXECUTION_ID=$ExecutionId" `
        $ContainerName python -c $probeBootstrap 2>&1
    if ($LASTEXITCODE -ne 0) {
        $outcome = 'CONTAINER_PROBE_FAILED'
        Write-Warning ($probeOutput -join "`n")
        break
    }
    $probe = ($probeOutput -join "`n") | ConvertFrom-Json
    $targetSeenNow = [bool]$probe.process.target_seen

    if ($targetSeenNow -and -not $seenTarget) {
        $seenTarget = $true
        $runStartedAt = $sampleStarted
        Write-Host "CDECR process detected at $($runStartedAt.ToString('o'))"
    }
    if ($targetSeenNow -and $null -ne $runEndedAt) {
        $runEndedAt = $null
        $postDeadline = $null
        Write-Host 'Matching CDECR process reappeared; post-run countdown was cancelled.'
    }
    if ($seenTarget -and -not $targetSeenNow -and $null -eq $runEndedAt) {
        $runEndedAt = $sampleStarted
        $postDeadline = $runEndedAt.AddSeconds($PostRunSeconds)
        Write-Host "CDECR process exited at $($runEndedAt.ToString('o')); collecting post-run samples."
    }

    if ($targetSeenNow) {
        $phase = 'RUNNING'
    } elseif ($null -ne $runEndedAt) {
        $phase = 'POST_RUN'
    } elseif ($elapsed -lt $BaselineSeconds) {
        $phase = 'BASELINE'
    } else {
        $phase = 'WAITING'
    }

    $containerCpuCores = $null
    $processCpuCores = $null
    if ($null -ne $previousTimestamp) {
        $deltaSeconds = ($sampleStarted - $previousTimestamp).TotalSeconds
        if ($deltaSeconds -gt 0 -and $null -ne $probe.cgroup.cpu_usage_usec -and $null -ne $previousContainerCpu) {
            $containerCpuCores = ([double]$probe.cgroup.cpu_usage_usec - [double]$previousContainerCpu) / (1000000.0 * $deltaSeconds)
        }
        if ($deltaSeconds -gt 0 -and $null -ne $previousProcessCpu) {
            $processCpuCores = ([double]$probe.process.cpu_ticks - [double]$previousProcessCpu) / ([double]$probe.clock_ticks_per_second * $deltaSeconds)
        }
    }

    $hostProcesses = @(Get-Process -Name 'vmmemWSL', 'com.docker.backend' -ErrorAction SilentlyContinue)
    $vmmem = @($hostProcesses | Where-Object { $_.ProcessName -eq 'vmmemWSL' })
    $dockerBackend = @($hostProcesses | Where-Object { $_.ProcessName -eq 'com.docker.backend' })
    $hostMetrics = [ordered]@{
        vmmem_working_set_bytes = if ($vmmem.Count) { ($vmmem | Measure-Object WorkingSet64 -Sum).Sum } else { $null }
        vmmem_private_bytes = if ($vmmem.Count) { ($vmmem | Measure-Object PrivateMemorySize64 -Sum).Sum } else { $null }
        docker_backend_working_set_bytes = if ($dockerBackend.Count) { ($dockerBackend | Measure-Object WorkingSet64 -Sum).Sum } else { $null }
    }

    $record = [ordered]@{
        timestamp = $sampleStarted.ToString('o')
        elapsed_seconds = [Math]::Round($elapsed, 3)
        phase = $phase
        container_cpu_cores = $containerCpuCores
        process_cpu_cores = $processCpuCores
        cgroup = $probe.cgroup
        process = $probe.process
        stage = $probe.stage
        dispatch_input = $probe.dispatch_input
        registry_path = $probe.registry_path
        host = $hostMetrics
    }
    $records.Add([pscustomobject]$record)
    Append-Utf8NoBom $samplesPath (($record | ConvertTo-Json -Depth 10 -Compress) + "`n")

    $previousTimestamp = $sampleStarted
    $previousContainerCpu = $probe.cgroup.cpu_usage_usec
    $previousProcessCpu = if ($targetSeenNow) { $probe.process.cpu_ticks } else { $null }

    if (($sampleStarted - $lastProgress).TotalSeconds -ge 30) {
        $stageName = if ($null -ne $probe.stage) { $probe.stage.current_stage } else { 'n/a' }
        Write-Host ("[{0}] phase={1} stage={2} container={3} process_pss={4}" -f `
            $sampleStarted.ToString('HH:mm:ss'), $phase, $stageName, `
            (Format-MiB $probe.cgroup.memory_current_bytes), `
            (Format-MiB $probe.process.pss_bytes))
        $lastProgress = $sampleStarted
    }

    if (-not $seenTarget -and $elapsed -ge $WaitTimeoutSeconds) {
        $outcome = 'TARGET_NOT_OBSERVED'
        break
    }
    if ($null -ne $postDeadline -and $sampleStarted -ge $postDeadline) {
        $outcome = 'COMPLETED'
        break
    }
    Start-Sleep -Seconds $SampleIntervalSeconds
}

$finishedAt = (Get-Date).ToUniversalTime()
$runningRecords = @($records | Where-Object { $_.phase -eq 'RUNNING' })
$baselineRecords = @($records | Where-Object { $_.phase -eq 'BASELINE' })
$firstRecord = if ($records.Count) { $records[0] } else { $null }
$lastRecord = if ($records.Count) { $records[$records.Count - 1] } else { $null }
$oomDelta = $null
$oomKillDelta = $null
if ($null -ne $firstRecord -and $null -ne $lastRecord) {
    $oomDelta = [int64]$lastRecord.cgroup.oom - [int64]$firstRecord.cgroup.oom
    $oomKillDelta = [int64]$lastRecord.cgroup.oom_kill - [int64]$firstRecord.cgroup.oom_kill
}

$stageSummaries = @()
foreach ($group in @($runningRecords | Where-Object { $null -ne $_.stage } | Group-Object { $_.stage.current_stage })) {
    $stageSummaries += [ordered]@{
        stage = $group.Name
        sample_count = $group.Count
        container_memory_peak_bytes = Get-Maximum @($group.Group | ForEach-Object { $_.cgroup.memory_current_bytes })
        process_pss_peak_bytes = Get-Maximum @($group.Group | ForEach-Object { $_.process.pss_bytes })
        container_cpu_p95_cores = Get-Percentile @($group.Group | ForEach-Object { $_.container_cpu_cores }) 95
        process_cpu_p95_cores = Get-Percentile @($group.Group | ForEach-Object { $_.process_cpu_cores }) 95
    }
}

$summary = [ordered]@{
    schema_version = 'cdecr_resource_summary.v1'
    ticker = $Ticker
    execution_id = if ($ExecutionId) { $ExecutionId } else { $null }
    container_name = $ContainerName
    container_id = $containerId
    outcome = $outcome
    started_at = $startedAt.ToString('o')
    cdecr_detected_at = if ($null -ne $runStartedAt) { $runStartedAt.ToString('o') } else { $null }
    cdecr_exited_at = if ($null -ne $runEndedAt) { $runEndedAt.ToString('o') } else { $null }
    finished_at = $finishedAt.ToString('o')
    monitor_duration_seconds = [Math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
    sample_count = $records.Count
    running_sample_count = $runningRecords.Count
    baseline_sample_count = $baselineRecords.Count
    baseline_container_memory_p95_bytes = Get-Percentile @($baselineRecords | ForEach-Object { $_.cgroup.memory_current_bytes }) 95
    observed_container_memory_peak_bytes = Get-Maximum @($records | ForEach-Object { $_.cgroup.memory_current_bytes })
    cgroup_lifetime_memory_peak_bytes = Get-Maximum @($records | ForEach-Object { $_.cgroup.memory_peak_bytes })
    container_anon_peak_bytes = Get-Maximum @($runningRecords | ForEach-Object { $_.cgroup.memory_anon_bytes })
    container_file_cache_peak_bytes = Get-Maximum @($runningRecords | ForEach-Object { $_.cgroup.memory_file_bytes })
    container_swap_peak_bytes = Get-Maximum @($records | ForEach-Object { $_.cgroup.memory_swap_current_bytes })
    cdecr_process_rss_peak_bytes = Get-Maximum @($runningRecords | ForEach-Object { $_.process.rss_bytes })
    cdecr_process_pss_peak_bytes = Get-Maximum @($runningRecords | ForEach-Object { $_.process.pss_bytes })
    cdecr_process_threads_peak = Get-Maximum @($runningRecords | ForEach-Object { $_.process.threads })
    container_pids_peak = Get-Maximum @($runningRecords | ForEach-Object { $_.cgroup.pids_current })
    container_cpu_average_cores = Get-Average @($runningRecords | ForEach-Object { $_.container_cpu_cores })
    container_cpu_p95_cores = Get-Percentile @($runningRecords | ForEach-Object { $_.container_cpu_cores }) 95
    container_cpu_peak_cores = Get-Maximum @($runningRecords | ForEach-Object { $_.container_cpu_cores })
    process_cpu_average_cores = Get-Average @($runningRecords | ForEach-Object { $_.process_cpu_cores })
    process_cpu_p95_cores = Get-Percentile @($runningRecords | ForEach-Object { $_.process_cpu_cores }) 95
    process_cpu_peak_cores = Get-Maximum @($runningRecords | ForEach-Object { $_.process_cpu_cores })
    host_vmmem_working_set_peak_bytes = Get-Maximum @($records | ForEach-Object { $_.host.vmmem_working_set_bytes })
    host_docker_backend_working_set_peak_bytes = Get-Maximum @($records | ForEach-Object { $_.host.docker_backend_working_set_bytes })
    oom_delta = $oomDelta
    oom_kill_delta = $oomKillDelta
    stages = $stageSummaries
    caveats = @(
        'Container metrics include the lightweight docker-exec probe; CDECR process-tree metrics exclude it.',
        'cgroup_lifetime_memory_peak_bytes may include work before this monitor started.',
        'Windows vmmemWSL metrics include every running WSL workload and Docker container.'
    )
}
Write-Utf8NoBom $summaryPath (($summary | ConvertTo-Json -Depth 10) + "`n")

$report = @"
# CDECR Resource Monitor — $Ticker

- Outcome: ``$outcome``
- Container: ``$ContainerName``
- Samples: $($records.Count) total / $($runningRecords.Count) running
- Monitor duration: $($summary.monitor_duration_seconds) seconds
- Baseline container memory P95: $(Format-MiB $summary.baseline_container_memory_p95_bytes)
- Observed container memory peak: $(Format-MiB $summary.observed_container_memory_peak_bytes)
- CDECR process RSS peak: $(Format-MiB $summary.cdecr_process_rss_peak_bytes)
- CDECR process PSS peak: $(Format-MiB $summary.cdecr_process_pss_peak_bytes)
- Container CPU average / P95 / peak: $(Format-Number $summary.container_cpu_average_cores) / $(Format-Number $summary.container_cpu_p95_cores) / $(Format-Number $summary.container_cpu_peak_cores) cores
- CDECR CPU average / P95 / peak: $(Format-Number $summary.process_cpu_average_cores) / $(Format-Number $summary.process_cpu_p95_cores) / $(Format-Number $summary.process_cpu_peak_cores) cores
- OOM / OOM-kill delta: $oomDelta / $oomKillDelta

Raw samples: ``samples.jsonl``. Machine-readable summary: ``summary.json``.

The cgroup lifetime peak can predate this run. For server sizing, prefer the observed
run peak and process PSS, then retain 30–50% headroom and add the other resident services.
"@
Write-Utf8NoBom $reportPath ($report + "`n")

Write-Host "Monitor finished: $outcome"
Write-Host "Summary: $summaryPath"
if ($outcome -eq 'CONTAINER_PROBE_FAILED') { exit 2 }
if ($outcome -eq 'TARGET_NOT_OBSERVED') { exit 3 }
if ($outcome -eq 'MAX_DURATION_REACHED') { exit 4 }
exit 0
