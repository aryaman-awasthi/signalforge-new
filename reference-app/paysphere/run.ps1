#Requires -Version 5.1
<#
Build the reference harness if it is stale, then run it. The PowerShell twin of
run.sh, for a machine without bash.

Nothing here is required by SignalForge: the platform reaches this application
over HTTP and would not notice if it were rewritten in another language, which
is the entire point of the /observe contract being frozen and public.

  .\run.ps1      from PowerShell
  run.cmd        from cmd.exe
#>
[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Here = $PSScriptRoot
$Jar = Join-Path $Here 'target\paysphere.jar'

function Get-Setting([string]$Name, [string]$Default) {
    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value
}

# Forward slashes so the H2 URL and the JSONL path in application.yml read the
# same way on every JVM.
$HereForJava = $Here.Replace('\', '/')

# PAYSPHERE_PORT is this application's own knob; SIGNALFORGE_HARNESS_PORT is the
# generic one the platform's launcher sets, since that launcher may not name
# this application.
$env:PAYSPHERE_PORT = Get-Setting 'PAYSPHERE_PORT' (Get-Setting 'SIGNALFORGE_HARNESS_PORT' '8080')
$env:PAYSPHERE_DATA_DIR = Get-Setting 'PAYSPHERE_DATA_DIR' "$HereForJava/data"
$env:PAYSPHERE_LOG_DIR = Get-Setting 'PAYSPHERE_LOG_DIR' "$HereForJava/logs"

$needsBuild = -not (Test-Path -LiteralPath $Jar)
if (-not $needsBuild) {
    $jarTime = (Get-Item -LiteralPath $Jar).LastWriteTimeUtc
    $newer = @(Get-ChildItem -Path (Join-Path $Here 'src'), (Join-Path $Here 'pom.xml') -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTimeUtc -gt $jarTime })
    if ($newer.Count -gt 0) { $needsBuild = $true }
}

if ($needsBuild) {
    $mvn = Get-Command mvn -ErrorAction SilentlyContinue
    if (-not $mvn) {
        [Console]::Error.WriteLine("[harness] maven is not on PATH and $Jar is missing or stale")
        [Console]::Error.WriteLine("[harness] install maven, or build elsewhere and drop the jar at $Jar")
        exit 1
    }
    Write-Host '[harness] building'
    Push-Location $Here
    try {
        & $mvn.Source -B -q -DskipTests package
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
    }
}

# JAVA_HOME first: the java.exe on PATH may be a launcher shim (Oracle's
# javapath is one) that starts the real JVM and exits, which leaves the JVM
# outside the process tree a launcher stops. The JDK's own binary is a direct
# child.
$javaExe = $null
if ($env:JAVA_HOME -and (Test-Path -LiteralPath (Join-Path $env:JAVA_HOME 'bin\java.exe'))) {
    $javaExe = Join-Path $env:JAVA_HOME 'bin\java.exe'
} else {
    $java = Get-Command java -ErrorAction SilentlyContinue
    if ($java) { $javaExe = $java.Source }
}
if (-not $javaExe) {
    [Console]::Error.WriteLine('[harness] java is not on PATH and JAVA_HOME is not set (Java 17 or newer is required)')
    exit 1
}

Write-Host "[harness] starting on :$env:PAYSPHERE_PORT (data: $env:PAYSPHERE_DATA_DIR)"
& $javaExe -jar $Jar
exit $LASTEXITCODE
