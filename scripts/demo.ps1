#Requires -Version 5.1
<#
Set a *running* SignalForge up to the moment before a declaration. The
PowerShell twin of demo.sh, for a machine without bash.

  run.cmd               # in one terminal
  scripts\demo.cmd      # in another

This is the live half of the demonstration: it drives the reference harness
and the platform over HTTP - signing up, onboarding through the public
contracts, inviting the people an incident needs, driving steady traffic until
a baseline exists, injecting the published fault, and then stopping, because
what happens next is a person pressing Declare.

Any argument after the drill file is passed through to drill_run.py, so
`scripts\demo.cmd drills\demo-setup.yaml --scenario license-expiry` works.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Drill,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest = @()
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot

function Import-DotEnv([string]$Path) {
    # Same .env load as run.ps1: the drill runner signs requests and may ask the
    # platform for model-backed surfaces, so it needs the same configuration.
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

function Get-Setting([string]$Name, [string]$Default) {
    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value
}

Import-DotEnv "$Root\.env"

$Py = 'python'
foreach ($candidate in @("$Root\.venv\Scripts\python.exe", "$Root\.venv\bin\python")) {
    if (Test-Path -LiteralPath $candidate) { $Py = $candidate; break }
}

$Api = Get-Setting 'SIGNALFORGE_API' 'http://127.0.0.1:8000'
$App = Get-Setting 'SIGNALFORGE_APP_BASE_URL' 'http://127.0.0.1:8080'
# demo-setup is the demonstration world: every connector an incident touches,
# all six golden signals, four journeys, a topology and one person per role, so
# no panel of the cockpit reads empty. s4-acceptance is the acceptance scenario
# and is played by passing it as the argument.
if ([string]::IsNullOrWhiteSpace($Drill)) { $Drill = "$Root\drills\demo-setup.yaml" }

if (-not (Test-Path -LiteralPath $Drill)) {
    [Console]::Error.WriteLine("[demo] no such drill: $Drill")
    [Console]::Error.WriteLine('[demo] drills are demo data (CLAUDE.md rule 7); the product runs without them')
    exit 1
}

Write-Host "[demo] platform  $Api"
Write-Host "[demo] harness   $App"
Write-Host "[demo] scenario  $(Split-Path -Leaf $Drill)"
Write-Host ''

& $Py "$Root\scripts\drill_run.py" $Drill --api $Api --app-base-url $App @Rest
exit $LASTEXITCODE
