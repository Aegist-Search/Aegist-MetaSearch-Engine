@echo off
rem Aegis Search - start (Windows)
rem
rem * starts Meilisearch, Tor and FastAPI with Docker Compose
rem * waits for the backend health endpoint
rem * starts "npm run dev" in a minimised window (logs in logs\)
rem * waits for the frontend port and prints the URLs
rem
rem Usage:
rem   scripts\start.bat [-Port 3000] [-Timeout 120] [-NoBuild] [-Stop]
rem
rem -Stop calls stop.bat, which is the recommended way to stop everything.
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0.."

set "FRONTEND=%CD%\frontend"
set "LOGDIR=%CD%\logs"
set "STDOUT_LOG=%LOGDIR%\frontend.out.log"
set "STDERR_LOG=%LOGDIR%\frontend.err.log"
set "BACKEND_HEALTH=http://127.0.0.1:8000/api/v1/health"

set "PORT=3000"
set "TIMEOUT=120"
set "NOBUILD="
set "STOP="

:parse
if "%~1"=="" goto :parsed
set "ARG=%~1"
if /i "%ARG%"=="-port" goto :opt_port
if /i "%ARG%"=="--port" goto :opt_port
if /i "%ARG%"=="-timeout" goto :opt_timeout
if /i "%ARG%"=="--timeout" goto :opt_timeout
if /i "%ARG%"=="-nobuild" goto :opt_nobuild
if /i "%ARG%"=="--no-build" goto :opt_nobuild
if /i "%ARG%"=="-stop" goto :opt_stop
if /i "%ARG%"=="--stop" goto :opt_stop
if /i "%ARG%"=="-h" goto :opt_help
if /i "%ARG%"=="--help" goto :opt_help
echo Unknown option: %ARG%
goto :usage_error

:opt_port
set "PORT=%~2"
if "%PORT%"=="" goto :usage_error
shift
shift
goto :parse

:opt_timeout
set "TIMEOUT=%~2"
if "%TIMEOUT%"=="" goto :usage_error
shift
shift
goto :parse

:opt_nobuild
set "NOBUILD=1"
shift
goto :parse

:opt_stop
set "STOP=1"
shift
goto :parse

:opt_help
call :usage
exit /b 0

:parsed
set "DIGITS="
for /f "delims=0123456789" %%A in ("%PORT%") do set "DIGITS=1"
if defined DIGITS (
    echo ERR   port must be a number, got: %PORT%
    exit /b 2
)
set "DIGITS="
for /f "delims=0123456789" %%A in ("%TIMEOUT%") do set "DIGITS=1"
if defined DIGITS (
    echo ERR   timeout must be a number, got: %TIMEOUT%
    exit /b 2
)

if defined STOP (
    call "%~dp0stop.bat" --port %PORT%
    exit /b %ERRORLEVEL%
)

where docker >nul 2>&1
if errorlevel 1 (
    echo ERR   docker not found in PATH. Start Docker Desktop and retry.
    exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo ERR   npm not found in PATH ^(Node.js 20+^).
    exit /b 1
)
where curl.exe >nul 2>&1
if errorlevel 1 (
    echo ERR   curl.exe not found: it ships with Windows 10 1803+ and is used for readiness checks.
    exit /b 1
)

if not exist "data" mkdir "data"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

echo ==^> Start the stack ^(docker compose up -d^)
if defined NOBUILD (
    docker compose up -d
) else (
    docker compose up -d --build
)
if errorlevel 1 (
    echo ERR   docker compose up failed
    exit /b 1
)
echo OK    Containers started

if not exist ".env" echo WARN  .env not found: run scripts\setup.bat first

echo ==^> Wait for the backend on %BACKEND_HEALTH%
set /a WAITED=0
set "BACKEND_READY="

:wait_backend
curl.exe -fsS -o NUL --max-time 3 "%BACKEND_HEALTH%" >nul 2>&1
if not errorlevel 1 (
    set "BACKEND_READY=1"
    goto :backend_done
)
if %WAITED% GEQ %TIMEOUT% goto :backend_timeout
timeout /t 1 /nobreak >nul 2>&1
if errorlevel 1 ping -n 2 127.0.0.1 >nul
set /a WAITED+=1
goto :wait_backend

:backend_timeout
echo WARN  backend not reachable after %TIMEOUT%s ^(check: docker compose logs backend^)
goto :backend_done

:backend_done
if defined BACKEND_READY echo OK    Backend ready: http://127.0.0.1:8000/docs

call :port_pid %PORT%
if defined PORT_PID (
    echo WARN  port %PORT% is already in use ^(PID %PORT_PID%^): the frontend is not started again
    goto :summary
)

if not exist "%FRONTEND%\node_modules" (
    echo ==^> Install frontend dependencies ^(npm install^)
    pushd "%FRONTEND%"
    call npm install --no-audit --no-fund
    if errorlevel 1 (
        popd
        echo ERR   npm install failed
        exit /b 1
    )
    popd
    echo OK    Dependencies installed
)

echo ==^> Start the frontend on port %PORT% ^(logs in logs\^)
type nul>"%STDOUT_LOG%"
type nul>"%STDERR_LOG%"
start "Aegis frontend" /min cmd /c "npm --prefix frontend run dev -- --port %PORT% 1>>logs\frontend.out.log 2>>logs\frontend.err.log"

set /a WAITED=0

:wait_frontend
call :port_pid %PORT%
if defined PORT_PID goto :frontend_ready
if %WAITED% GEQ %TIMEOUT% goto :frontend_timeout
timeout /t 1 /nobreak >nul 2>&1
if errorlevel 1 ping -n 2 127.0.0.1 >nul
set /a WAITED+=1
goto :wait_frontend

:frontend_ready
echo OK    Frontend ready: http://127.0.0.1:%PORT%
goto :summary

:frontend_timeout
echo WARN  the frontend did not open port %PORT%. Output of %STDERR_LOG%:
type "%STDERR_LOG%"
echo ERR   frontend did not start ^(see logs\^)
exit /b 1

:summary
echo.
echo Aegis Search is running
echo   Frontend   http://127.0.0.1:%PORT%
echo   Backend    http://127.0.0.1:8000
echo   API docs   http://127.0.0.1:8000/docs
echo   Logs       logs\frontend.out.log, logs\frontend.err.log
echo   Stop       scripts\stop.bat

if exist "data\katana.jsonl" (
    echo.
    echo Crawl available: data\katana.jsonl
    echo   docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl
)
exit /b 0

rem Sets PORT_PID to the PID listening on the given TCP port (empty if none).
:port_pid
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /r /c:":%~1 .*LISTENING"') do set "PORT_PID=%%P"
goto :eof

:usage
echo Usage: scripts\start.bat [-Port N] [-Timeout N] [-NoBuild] [-Stop]
echo.
echo   -Port N       frontend port ^(default 3000^)
echo   -Timeout N    seconds to wait for backend and frontend ^(default 120^)
echo   -NoBuild      do not rebuild the backend image
echo   -Stop         stop the frontend started by these scripts and exit
goto :eof

:usage_error
call :usage
exit /b 2
