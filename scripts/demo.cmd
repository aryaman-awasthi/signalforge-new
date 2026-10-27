@echo off
rem Demo driver for cmd.exe. Runs demo.ps1 in Windows PowerShell with a one-shot,
rem process-scoped execution-policy bypass. Arguments pass through to drill_run.py.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0demo.ps1" %*
exit /b %ERRORLEVEL%
