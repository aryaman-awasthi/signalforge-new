@echo off
rem Reference harness launcher for cmd.exe: builds the jar if it is stale, then
rem runs it. Runs run.ps1 in Windows PowerShell with a one-shot, process-scoped
rem execution-policy bypass.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
exit /b %ERRORLEVEL%
