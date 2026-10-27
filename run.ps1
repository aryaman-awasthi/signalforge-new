#Requires -Version 5.1
<#
SignalForge single-machine launcher for Windows (no Docker, no bash).

The PowerShell twin of run.sh, for a machine that has cmd.exe and PowerShell
and nothing else. Boots the reference harness, applies migrations, starts the
API and the web app, then *waits until each one answers* and prints where it
is. Two rules, and both exist because a demo that starts silently and
half-works is worse than one that refuses:

  1. Nothing is reported ready until it has answered a real request.
  2. If something essential does not come up, this exits non-zero with the
     log path, rather than leaving a half-started estate behind.

The critical path needs no Docker, no network and no GENAILAB_API_KEY. With
the key absent the platform runs deterministically and says so on every AI
surface; that is a supported mode, not a degraded install.

  .\run.ps1      from PowerShell (Windows PowerShell 5.1 or PowerShell 7)
  run.cmd        from cmd.exe, or by double-click

Ctrl-C stops everything this started.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root = $PSScriptRoot

function Say([string]$Message) { Write-Host "[run] $Message" }

function Fail([string]$Message) {
    [Console]::Error.WriteLine("[run] FAILED: $Message")
    exit 1
}

function Import-DotEnv([string]$Path) {
    # Configuration is read from the process environment (signalforge/config.py
    # reads os.environ and nothing else), so the file the operator is told to
    # write in docs/09 has to become the environment here, the way run.sh's
    # `set -a; . .env; set +a` does it. .env wins over an ambient value; to
    # override one for a single boot, comment it out of .env.
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
    # ${NAME:-default}: unset and empty both mean "use the default".
    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value
}

function Resolve-Python {
    foreach ($candidate in @("$Root\.venv\Scripts\python.exe", "$Root\.venv\bin\python")) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    Say "no .venv found - using python from PATH (create one: python -m venv .venv; .venv\Scripts\pip install -e .)"
    return 'python'
}

# One log per component, stdout and stderr together and in order, exactly as
# run.sh's `>log 2>&1`. cmd.exe does the redirection because Start-Process can
# only split the two streams into two files. `/S /C "..."` is cmd's documented
# way to take a whole command line verbatim: the outer quotes are stripped and
# everything inside, quotes included, is run as typed.
$script:Children = @()
$script:Ports = @()
$script:StartedAt = Get-Date

function Start-Logged([string]$Name, [string]$CommandLine, [string]$WorkingDirectory, [string]$Log, [int]$Port) {
    $arguments = "/S /C `"$CommandLine > `"$Log`" 2>&1`""
    $process = Start-Process -FilePath $env:ComSpec -ArgumentList $arguments `
        -WorkingDirectory $WorkingDirectory -NoNewWindow -PassThru
    $null = $process.Handle   # cache it so ExitCode is readable after it exits
    $script:Children += @{ Name = $Name; Process = $process; Log = $Log }
    if ($Port) { $script:Ports += $Port }
}

function Stop-Children {
    $ErrorActionPreference = 'Continue'
    foreach ($child in $script:Children) {
        $process = $child.Process
        if ($process -and -not $process.HasExited) {
            # /T takes the whole tree: cmd -> python/node/java and their children.
            & taskkill.exe /PID $process.Id /T /F > $null 2>&1
        }
    }
    # A launcher shim (Oracle's javapath java.exe is one) hands off to the real
    # process and exits, which breaks the parent link the tree kill walks.
    # Anything still holding a port this run opened, and younger than this run,
    # is ours; nothing that was there before this run is touched.
    foreach ($port in $script:Ports) {
        try {
            foreach ($conn in @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)) {
                $holder = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
                if ($holder -and $holder.StartTime -gt $script:StartedAt) {
                    Stop-Process -Id $holder.Id -Force -ErrorAction SilentlyContinue
                }
            }
        } catch { }
    }
}

function Test-Answers([string]$Url) {
    # Any HTTP status below 500 is "it answered". A guarded path returns 401 and
    # that is a component that is up - readiness is about the socket and the
    # application behind it, not about this probe holding a credential.
    try {
        $request = [System.Net.WebRequest]::Create($Url)
        $request.Timeout = 2000
        $request.Proxy = $null
        $response = $request.GetResponse()
        $code = [int]$response.StatusCode
        $response.Close()
        return ($code -lt 500)
    } catch {
        $exception = $_.Exception
        if ($exception -is [System.Management.Automation.MethodInvocationException] -and $exception.InnerException) {
            $exception = $exception.InnerException
        }
        if ($exception -is [System.Net.WebException] -and $exception.Response) {
            $code = [int]$exception.Response.StatusCode
            $exception.Response.Close()
            return ($code -lt 500)
        }
        return $false
    }
}

function Assert-PortFree([string]$Name, [int]$Port) {
    # The readiness probe cannot tell this run's process from a stray one that
    # already holds the port - it would report "up" and then watch our process
    # die - so the port is checked before the component is started, and the
    # holder is named so it can be stopped.
    try {
        $holders = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    } catch { return }
    if ($holders.Count -eq 0) { return }
    $owner = Get-Process -Id $holders[0].OwningProcess -ErrorAction SilentlyContinue
    $who = if ($owner) { "pid $($owner.Id) ($($owner.ProcessName))" } else { "pid $($holders[0].OwningProcess)" }
    Fail "$Name cannot start: port $Port is already in use by $who - stop it, or set another port in .env"
}

function Wait-Until([string]$Name, [string]$Url, [string]$Log) {
    # Poll until it answers or the budget runs out. A component that has already
    # exited is reported at once rather than after the full budget.
    $deadline = (Get-Date).AddSeconds($ReadyTimeoutS)
    while ((Get-Date) -lt $deadline) {
        if (Test-Answers $Url) { Say "$Name is up"; return }
        $child = @($script:Children | Where-Object { $_.Name -eq $Name }) | Select-Object -Last 1
        if ($child -and $child.Process.HasExited) {
            Fail "$Name exited with code $($child.Process.ExitCode) before answering $Url - see $Log"
        }
        Start-Sleep -Seconds 1
    }
    Fail "$Name did not answer $Url within ${ReadyTimeoutS}s - see $Log"
}

Import-DotEnv "$Root\.env"

$Py = Resolve-Python
$ApiHost = Get-Setting 'SIGNALFORGE_HOST' '127.0.0.1'
$ApiPort = Get-Setting 'SIGNALFORGE_PORT' '8000'
$WebPort = Get-Setting 'SIGNALFORGE_WEB_PORT' '3000'
# The reference harness reads its own port from its own environment; this file
# may not name the reference application (CLAUDE.md rule 2), so the knob here is
# generic and the harness's launcher maps it.
$HarnessPort = Get-Setting 'SIGNALFORGE_HARNESS_PORT' '8080'
$LogDir = "$Root\var\run"
$ReadyTimeoutS = [int](Get-Setting 'SIGNALFORGE_READY_TIMEOUT_S' '120')
$PowerShellExe = (Get-Process -Id $PID).Path

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$started = $false
$harnessStarted = $false

try {
    # --- the reference harness (demo data; deleting it leaves a working product) ---
    # Discovered by glob rather than by name, because this file is core and may
    # not name the reference application (CLAUDE.md rule 2).
    foreach ($harness in @(Get-ChildItem -Path "$Root\reference-app" -Directory -ErrorAction SilentlyContinue)) {
        $launcher = Join-Path $harness.FullName 'run.ps1'
        if (-not (Test-Path -LiteralPath $launcher)) { continue }
        $hasJar = @(Get-ChildItem -Path (Join-Path $harness.FullName 'target') -Filter '*.jar' -ErrorAction SilentlyContinue).Count -gt 0
        $hasMaven = $null -ne (Get-Command mvn -ErrorAction SilentlyContinue)
        if ($hasJar -or $hasMaven) {
            Assert-PortFree 'reference harness' $HarnessPort
            Say "starting reference harness $($harness.Name) on :$HarnessPort"
            $harnessLog = "$LogDir\harness.log"
            Start-Logged -Name 'reference harness' `
                -CommandLine "`"$PowerShellExe`" -NoProfile -ExecutionPolicy Bypass -File `"$launcher`"" `
                -WorkingDirectory $harness.FullName -Log $harnessLog -Port $HarnessPort
            $started = $true
            $harnessStarted = $true
            Wait-Until 'reference harness' "http://127.0.0.1:$HarnessPort/observe/health" $harnessLog
        } else {
            Say "reference harness $($harness.Name): not built and maven is absent - skipping"
            Say "  (the platform does not need it; the demo drill does)"
        }
    }

    # --- the platform -------------------------------------------------------------
    if (-not (Test-Path -LiteralPath "$Root\signalforge\api\main.py")) {
        Fail "signalforge\api\main.py is missing - this is not a SignalForge checkout"
    }

    Say "applying database migrations"
    $migrateLog = "$LogDir\migrate.log"
    $migration = Start-Process -FilePath $env:ComSpec `
        -ArgumentList "/S /C `"`"$Py`" -m alembic upgrade head > `"$migrateLog`" 2>&1`"" `
        -WorkingDirectory $Root -NoNewWindow -Wait -PassThru
    if ($migration.ExitCode -ne 0) { Fail "migrations did not apply - see $migrateLog" }

    Assert-PortFree 'api' $ApiPort
    Say "starting api on :$ApiPort"
    $apiLog = "$LogDir\api.log"
    Start-Logged -Name 'api' `
        -CommandLine "`"$Py`" -m uvicorn signalforge.api.main:app --host $ApiHost --port $ApiPort" `
        -WorkingDirectory $Root -Log $apiLog -Port $ApiPort
    $started = $true
    Wait-Until 'api' "http://${ApiHost}:$ApiPort/health" $apiLog

    # --- the cockpit ---------------------------------------------------------------
    if (Test-Path -LiteralPath "$Root\web\package.json") {
        if (Test-Path -LiteralPath "$Root\web\node_modules") {
            Assert-PortFree 'web' $WebPort
            Say "starting web on :$WebPort"
            $webLog = "$LogDir\web.log"
            Start-Logged -Name 'web' -CommandLine "npm run dev -- --port $WebPort" `
                -WorkingDirectory "$Root\web" -Log $webLog -Port $WebPort
            $started = $true
            Wait-Until 'web' "http://127.0.0.1:$WebPort/" $webLog
        } else {
            Say "web: node_modules is absent - run 'npm install' in web\ first"
            Say "  (the API and the drill are unaffected)"
        }
    }

    if (-not $started) { Fail "nothing started" }

    $modelLine = if ([string]::IsNullOrWhiteSpace($env:GENAILAB_API_KEY)) {
        'no key set - deterministic mode, which is supported'
    } else {
        'gateway configured'
    }

    Write-Host ''
    Write-Host '  SignalForge is up.'
    Write-Host ''
    Write-Host "    cockpit        http://127.0.0.1:$WebPort"
    Write-Host "    api            http://${ApiHost}:$ApiPort"
    Write-Host "    api docs       http://${ApiHost}:$ApiPort/docs"
    Write-Host "    health         http://${ApiHost}:$ApiPort/health"
    Write-Host "    metrics        http://${ApiHost}:$ApiPort/metrics"
    if ($harnessStarted) { Write-Host "    reference app  http://127.0.0.1:$HarnessPort" }
    Write-Host ''
    Write-Host "  Logs: $LogDir"
    Write-Host "  Model: $modelLine"
    Write-Host ''
    Write-Host '  Next: scripts\demo.cmd              (sets the cockpit up to a live P0 proposal)'
    Write-Host '        python scripts\demo_check.py   (verifies the twelve final-demo checks)'
    Write-Host ''
    Write-Host '  Ctrl-C stops everything.'
    Write-Host ''

    # The bash launcher `wait`s for its children; here a component that dies is
    # also named, because a silent half-estate is the thing this script exists
    # to refuse.
    while ($true) {
        Start-Sleep -Seconds 1
        foreach ($child in $script:Children) {
            if ($child.Process.HasExited) {
                Fail "$($child.Name) exited with code $($child.Process.ExitCode) - see $($child.Log)"
            }
        }
    }
} finally {
    Stop-Children
}
