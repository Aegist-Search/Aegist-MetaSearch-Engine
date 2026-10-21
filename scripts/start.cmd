@echo off
rem Wrapper per avviare start.ps1 senza toccare la ExecutionPolicy di sistema.
rem Uso:  .\scripts\start.cmd
rem        .\scripts\start.cmd -Stop
rem        .\scripts\start.cmd -NoBuild
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
exit /b %ERRORLEVEL%
