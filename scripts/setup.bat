@echo off
rem Aegis Search - setup (Windows)
rem
rem Copies the .env templates next to their destination files without
rem overwriting anything and creates data\.
rem
rem Usage: scripts\setup.bat
setlocal EnableExtensions
cd /d "%~dp0.."

set "FAILED="

echo Aegis Search - setup
echo.

call :copy_template ".env.example" ".env"
call :copy_template "backend\.env.example" "backend\.env"
call :copy_template "frontend\.env.example" "frontend\.env.local"

if not exist "data" (
    mkdir "data"
    if errorlevel 1 (
        echo ERR   cannot create data\
        exit /b 1
    )
)
echo OK    data\ ready

echo.
echo Next steps:
echo   1. Edit .env and set MEILI_MASTER_KEY and BRAVE_API_KEY.
echo      Optional: EXA_API_KEY, SERPER_API_KEY, TAVILY_API_KEY for the shield.
echo   2. Start the stack:  scripts\start.bat
echo   3. Backend health:   curl.exe http://127.0.0.1:8000/api/v1/health
echo   4. Frontend:         http://127.0.0.1:3000

if defined FAILED exit /b 1
exit /b 0

:copy_template
if not exist "%~1" (
    echo ERR   template not found: %~1
    set "FAILED=1"
    goto :eof
)
if exist "%~2" (
    echo SKIP  %~2 already exists
    goto :eof
)
copy /y "%~1" "%~2" >nul
if errorlevel 1 (
    echo ERR   cannot create %~2
    set "FAILED=1"
    goto :eof
)
echo OK    %~2 created
goto :eof
