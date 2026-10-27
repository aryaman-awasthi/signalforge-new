#Requires -Version 5.1
<#
Return this machine to the deterministic demo baseline. The PowerShell twin of
reset.sh, for a machine without bash.

What that baseline *is*, precisely: no organisation, no application, no
incident, no telemetry, no cached model responses - and every line of source,
spec, drill and seed exactly where it was. SignalForge holds no demo data of
its own; drills/ and seeds/ are data that a run consumes, never state a run
mutates. So "reset" means "delete what a run wrote", and nothing else.

It refuses to run anywhere that does not look like this repository, because a
script whose job is a recursive delete should be paranoid about where it is
standing.

  .\reset.ps1    from PowerShell
  reset.cmd      from cmd.exe
#>
[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Root = $PSScriptRoot

function Say([string]$Message) { Write-Host "[reset] $Message" }

function Import-DotEnv([string]$Path) {
    # Same .env load as run.ps1: reset touches paths that config.py resolves, so
    # it has to resolve them the same way the platform will.
    if (-not (Test-Path -LiteralPath $Path)) { return }
    foreach ($raw in Get-Content -LiteralPath $Path) {
        $line = $raw.Trim()
        if ($line -eq '' -or $line.StartsWith('#')) { continue }
        if ($line.StartsWith('export ')) { $line = $line.Substring(7).TrimStart() }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $line.Substring(0, $eq).Trim()
        if ($key -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { continue }
        $value = $line.Substring($eq + 1).Trim()
        if ($value.Length -gt 0 -and ($value[0] -eq '"' -or $value[0] -eq "'")) {
            $close = $value.IndexOf($value[0], 1)
            if ($close -gt 0) { $value = $value.Substring(1, $close - 1) } else { $value = $value.Substring(1) }
        } else {
            $value = ($value -replace '\s+#.*$', '').Trim()
        }
        [Environment]::SetEnvironmentVariable($key, $value, 'Process')
    }
}

function Remove-Tree([string]$Path) {
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force }
}

Import-DotEnv "$Root\.env"

# Markers a *release* actually contains (not CLAUDE.md, which the package
# deliberately excludes - the same lesson reset.sh learned in S8-T10).
foreach ($marker in @('signalforge\api\main.py', 'specs\requirements.yaml', 'run.sh')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Root $marker))) {
        [Console]::Error.WriteLine("[reset] refusing: $Root does not look like a SignalForge checkout ($marker missing)")
        exit 1
    }
}

try {
    # 1. Everything the platform wrote. var\ holds the SQLite databases, the
    #    rollup store, the LLM cache, the RTM recording, the non-functional
    #    measurements and the run logs. All of it is derived; none of it is source.
    Say "clearing platform runtime state (var\)"
    Remove-Tree "$Root\var"
    New-Item -ItemType Directory -Force -Path "$Root\var" | Out-Null

    # 2. Everything the reference harness wrote. Its jar is left alone: rebuilding
    #    it needs maven and a minute, and it is a build artefact rather than state.
    Say "clearing reference harness databases and logs"
    foreach ($harness in @(Get-ChildItem -Path "$Root\reference-app" -Directory -ErrorAction SilentlyContinue)) {
        Remove-Tree (Join-Path $harness.FullName 'data')
        Remove-Tree (Join-Path $harness.FullName 'logs')
    }

    # 3. Caches. Not state, but a stale one makes a rerun ambiguous.
    Say "clearing test and bytecode caches"
    Remove-Tree "$Root\.pytest_cache"
    foreach ($top in @("$Root\signalforge", "$Root\scripts")) {
        foreach ($cache in @(Get-ChildItem -Path $top -Directory -Recurse -Filter '__pycache__' -ErrorAction SilentlyContinue)) {
            Remove-Tree $cache.FullName
        }
    }
} catch {
    # On Windows a file that a running process holds open cannot be deleted, so
    # a reset under a live run.ps1 stops here rather than half-clearing.
    [Console]::Error.WriteLine("[reset] FAILED: $($_.Exception.Message)")
    [Console]::Error.WriteLine("[reset] if SignalForge or the reference harness is still running, stop it (Ctrl-C in its window) and run this again")
    exit 1
}

# 4. Migrations are applied by run.ps1 against the empty database, so there is
#    nothing to do here. Stating it rather than leaving it implicit: a reset
#    that half-migrated would be worse than one that did not migrate at all.
Say "database will be migrated from empty on the next run.ps1"

Write-Host ''
Write-Host '  Baseline restored.'
Write-Host ''
Write-Host '    deleted   var\ (databases, rollups, LLM cache, RTM recording, logs)'
Write-Host '              reference-app\*\data, reference-app\*\logs'
Write-Host '              __pycache__, .pytest_cache'
Write-Host '    untouched source, specs\, drills\, seeds\, observability\, the reference'
Write-Host '              config repository, node_modules, the harness jar, and anything'
Write-Host '              outside this checkout'
Write-Host ''
Write-Host '  Next: run.cmd   (or .\run.ps1)'
Write-Host ''
