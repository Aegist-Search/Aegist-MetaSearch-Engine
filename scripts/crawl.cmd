@echo off
rem Wrapper per crawl.ps1: passa -ExecutionPolicy Bypass senza cambiare nulla a sistema.
rem Uso:  .\scripts\crawl.cmd -Url https://developer.mozilla.org -Depth 2
rem        .\scripts\crawl.cmd -Url https://developer.mozilla.org -Ingest -Replace
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0crawl.ps1" %*
exit /b %ERRORLEVEL%
