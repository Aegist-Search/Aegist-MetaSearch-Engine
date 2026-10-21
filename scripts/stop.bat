@echo off
rem Aegis Search - stop (Windows)
rem
rem * closes the "Aegis frontend" window opened by start.bat
rem * falls back to the node process listening on the port
rem * stops the containers (docker compose stop, down or down --volumes)
rem
rem Usage: scripts\stop.bat [-Port 3000] [-Down] [-Volumes]
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0.."

set "PORT=3000"
set "DOWN="
set "VOLUMES="

:parse
if "%~1"=="" goto :parsed
set "ARG=%~1"
if /i "%ARG%"=="-port" goto :opt_port
if /i "%ARG%"=="--port" goto :opt_port
if /i "%ARG%"=="-down" goto :opt_down
if /i "%ARG%"=="--down" goto :opt_down
if /i "%ARG%"=="-volumes" goto :opt_volumes
if /i "%ARG%"=="--volumes" goto :opt_volumes
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

:opt_down
set "DOWN=1"
shift
goto :parse

:opt_volumes
set "VOLUMES=1"
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

echo ==^> Stop the frontend
set "STOPPED="

rem start.bat opens a console titled "Aegis frontend": closing it ends npm and node.
taskkill /FI "WINDOWTITLE eq Aegis frontend" /T /F >nul 2>&1
if not errorlevel 1 set "STOPPED=1"

call :port_pid %PORT%
if defined PORT_PID (
    tasklist /FI "PID eq %PORT_PID%" 2>nul | findstr /i "node.exe" >nul
    if not errorlevel 1 (
        taskkill /PID %PORT_PID% /T /F >nul 2>&1
        if not errorlevel 1 (
            echo OK    node process on port %PORT% stopped ^(PID %PORT_PID%^)
            set "STOPPED=1"
        )
    ) else (
        echo WARN  port %PORT% is used by a process that is not node ^(PID %PORT_PID%^): leaving it alone
    )
)

if defined STOPPED (
    echo OK    Frontend stopped
) else (
    echo WARN  no frontend started by these scripts is listening on port %PORT%
)

echo ==^> Stop the backend ^(Docker Compose^)

where docker >nul 2>&1
if errorlevel 1 (
    echo WARN  docker not found in PATH: containers were not stopped
    exit /b 1
)

if defined VOLUMES (
    echo WARN  removing the meili_data volume: the index must be rebuilt with a new ingestion
    docker compose down --volumes
) else if defined DOWN (
    docker compose down
) else (
    docker compose stop
)
if errorlevel 1 (
    echo WARN  docker compose failed
    exit /b 1
)

if defined VOLUMES (
    echo OK    Containers, network and index volume removed
) else if defined DOWN (
    echo OK    Containers and network removed ^(meili_data volume kept^)
) else (
    echo OK    Containers stopped ^(volumes kept^)
)

echo.
echo All stopped. Start again with scripts\start.bat
exit /b 0

rem Sets PORT_PID to the PID listening on the given TCP port (empty if none).
:port_pid
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /r /c:":%~1 .*LISTENING"') do set "PORT_PID=%%P"
goto :eof

:usage
echo Usage: scripts\stop.bat [-Port N] [-Down] [-Volumes]
echo.
echo   -Port N      frontend port used for the fallback ^(default 3000^)
echo   -Down        docker compose down: removes containers and network, keeps volumes
echo   -Volumes     docker compose down --volumes: also deletes the index
goto :eof

:usage_error
call :usage
exit /b 2
