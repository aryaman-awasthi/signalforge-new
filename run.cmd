@echo off
rem SignalForge launcher for cmd.exe. Runs run.ps1 in Windows PowerShell with a
rem one-shot, process-scoped execution-policy bypass, so a machine whose policy
rem refuses unsigned scripts still boots. Ctrl-C stops everything run.ps1 started.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
exit /b %ERRORLEVEL%
