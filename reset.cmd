@echo off
rem SignalForge reset for cmd.exe. Runs reset.ps1 in Windows PowerShell with a
rem one-shot, process-scoped execution-policy bypass.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0reset.ps1" %*
exit /b %ERRORLEVEL%
