@echo off
rem Wrapper per stop.ps1: passa -ExecutionPolicy Bypass senza cambiare nulla a sistema.
rem Uso:  .\scripts\stop.cmd
rem        .\scripts\stop.cmd -Down
rem        .\scripts\stop.cmd -Volumes
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*
exit /b %ERRORLEVEL%
