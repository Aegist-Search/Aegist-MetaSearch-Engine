@echo off
rem Aegis Search - crawl (Windows)
rem
rem * runs projectdiscovery/katana:latest in Docker, mounting data\ at /data, so
rem   the JSONL always lands in data\ and never in the project root
rem * checks that the file exists, is not empty and carries the HTML body
rem * with -Ingest runs the ingestion pipeline inside the backend container
rem
rem Usage:
rem   scripts\crawl.bat -Url https://example.com [-Depth 3] [-RateLimit 20]
rem                     [-Timeout 10] [-Retry 2] [-Output data\katana.jsonl]
rem                     [-Headless] [-Ingest] [-Replace] [extra katana args]
rem
rem Extra arguments that are not recognised are forwarded to katana, one flag
rem per argument (they cannot contain spaces).
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0.."

set "DATA_DIR=%CD%\data"
set "URL="
set "DEPTH=3"
set "RATE=20"
set "TIMEOUT=10"
set "RETRY=2"
set "OUTPUT=data\katana.jsonl"
set "HEADLESS="
set "INGEST="
set "REPLACE="
set "EXTRA="

:parse
if "%~1"=="" goto :parsed
set "ARG=%~1"
if /i "%ARG%"=="-url" goto :opt_url
if /i "%ARG%"=="--url" goto :opt_url
if /i "%ARG%"=="-depth" goto :opt_depth
if /i "%ARG%"=="--depth" goto :opt_depth
if /i "%ARG%"=="-ratelimit" goto :opt_rate
if /i "%ARG%"=="--rate-limit" goto :opt_rate
if /i "%ARG%"=="-timeout" goto :opt_timeout
if /i "%ARG%"=="--timeout" goto :opt_timeout
if /i "%ARG%"=="-retry" goto :opt_retry
if /i "%ARG%"=="--retry" goto :opt_retry
if /i "%ARG%"=="-output" goto :opt_output
if /i "%ARG%"=="--output" goto :opt_output
if /i "%ARG%"=="-headless" goto :opt_headless
if /i "%ARG%"=="--headless" goto :opt_headless
if /i "%ARG%"=="-ingest" goto :opt_ingest
if /i "%ARG%"=="--ingest" goto :opt_ingest
if /i "%ARG%"=="-replace" goto :opt_replace
if /i "%ARG%"=="--replace" goto :opt_replace
if /i "%ARG%"=="-h" goto :opt_help
if /i "%ARG%"=="--help" goto :opt_help
if /i "%ARG%"=="--" (
    shift
    goto :collect_extra
)
set "EXTRA=!EXTRA! %ARG%"
shift
goto :parse

:collect_extra
if "%~1"=="" goto :parsed
set "EXTRA=!EXTRA! %~1"
shift
goto :collect_extra

:opt_url
set "URL=%~2"
if "%URL%"=="" goto :usage_error
shift
shift
goto :parse

:opt_depth
set "DEPTH=%~2"
if "%DEPTH%"=="" goto :usage_error
shift
shift
goto :parse

:opt_rate
set "RATE=%~2"
if "%RATE%"=="" goto :usage_error
shift
shift
goto :parse

:opt_timeout
set "TIMEOUT=%~2"
if "%TIMEOUT%"=="" goto :usage_error
shift
shift
goto :parse

:opt_retry
set "RETRY=%~2"
if "%RETRY%"=="" goto :usage_error
shift
shift
goto :parse

:opt_output
set "OUTPUT=%~2"
if "%OUTPUT%"=="" goto :usage_error
shift
shift
goto :parse

:opt_headless
set "HEADLESS=1"
shift
goto :parse

:opt_ingest
set "INGEST=1"
shift
goto :parse

:opt_replace
set "REPLACE=1"
shift
goto :parse

:opt_help
call :usage
exit /b 0

:parsed
if not defined URL (
    echo ERR   a -Url is required ^(try -h^)
    exit /b 1
)

if /i "!URL:~0,7!"=="http://" goto :url_ok
if /i "!URL:~0,8!"=="https://" goto :url_ok
echo ERR   the URL must start with http:// or https:// ^(got: %URL%^)
exit /b 1

:url_ok
for %%V in (DEPTH RATE TIMEOUT RETRY) do call :check_number %%V
if errorlevel 1 exit /b 2

if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"

rem The output must stay inside data\, which is the only folder mounted in /data.
if /i not "!OUTPUT:~0,5!"=="data\" if /i not "!OUTPUT:~0,5!"=="data/" (
    echo ERR   the output must stay inside data\ ^(got: %OUTPUT%^)
    exit /b 1
)
set "REL=!OUTPUT:~5!"
if "!REL!"=="" (
    echo ERR   name a file, not the data\ folder: -Output data\katana.jsonl
    exit /b 1
)

where docker >nul 2>&1
if errorlevel 1 (
    echo ERR   docker not found in PATH. Start Docker Desktop and retry.
    exit /b 1
)

set "CONTAINER_OUTPUT=/data/!REL:\=/!"
set "FULL_OUTPUT=%DATA_DIR%\!REL!"
set "FULL_TEMP=%FULL_OUTPUT%.tmp"
set "CONTAINER_TEMP=!CONTAINER_OUTPUT!.tmp"

for %%D in ("%FULL_OUTPUT%") do set "OUT_DIR=%%~dpD"
if not exist "!OUT_DIR!" mkdir "!OUT_DIR!"
if exist "%FULL_TEMP%" del /q "%FULL_TEMP%"

echo ==^> Crawl %URL% ^(depth %DEPTH%, %RATE% req/s^)
echo     output: %FULL_OUTPUT%

set "DOCKER_ROOT=!DATA_DIR:\=/!"

if defined HEADLESS (
    docker run --rm -v "!DOCKER_ROOT!:/data" projectdiscovery/katana:latest -u "%URL%" -d %DEPTH% -rl %RATE% -timeout %TIMEOUT% -retry %RETRY% -system-chrome -headless -jsonl -o "!CONTAINER_TEMP!" %EXTRA%
) else (
    docker run --rm -v "!DOCKER_ROOT!:/data" projectdiscovery/katana:latest -u "%URL%" -d %DEPTH% -rl %RATE% -timeout %TIMEOUT% -retry %RETRY% -jsonl -o "!CONTAINER_TEMP!" %EXTRA%
)
if errorlevel 1 (
    echo ERR   katana failed
    exit /b 1
)

if not exist "%FULL_TEMP%" (
    echo ERR   katana did not create %FULL_TEMP% ^(check the URL and the mount^)
    exit /b 1
)

set /a RECORDS=0
for /f "usebackq delims=" %%L in ("%FULL_TEMP%") do set /a RECORDS+=1
if %RECORDS% LEQ 0 (
    del /q "%FULL_TEMP%"
    echo ERR   the crawl is empty: katana wrote no line
    exit /b 1
)

move /y "%FULL_TEMP%" "%FULL_OUTPUT%" >nul
if errorlevel 1 (
    echo ERR   cannot move the crawl into %FULL_OUTPUT%
    exit /b 1
)
echo OK    %RECORDS% records written to %FULL_OUTPUT%

rem Heuristic: the JSONL carries the HTML body when the record has a body field.
findstr /i /c:"body" "%FULL_OUTPUT%" >nul 2>&1
if errorlevel 1 (
    echo WARN  no response.body found: the pipeline cannot extract title and text ^(do not use -omit-body^)
) else (
    echo OK    HTML body present in the crawl
)

if defined INGEST (
    echo ==^> Ingest into Meilisearch
    if defined REPLACE (
        docker compose exec -T backend python -m app.ingestion.cli --input "!CONTAINER_OUTPUT!" --replace
    ) else (
        docker compose exec -T backend python -m app.ingestion.cli --input "!CONTAINER_OUTPUT!"
    )
    if errorlevel 1 (
        echo ERR   ingestion failed ^(is the backend up? try: docker compose up -d --build backend^)
        exit /b 1
    )
    echo OK    Index updated from !CONTAINER_OUTPUT!
) else (
    echo.
    echo To index the crawl:
    echo   docker compose exec backend python -m app.ingestion.cli --input !CONTAINER_OUTPUT! --replace
    echo or rerun this script with -Ingest -Replace
)
exit /b 0

:check_number
set "VALUE=!%~1!"
set "DIGITS="
for /f "delims=0123456789" %%A in ("!VALUE!") do set "DIGITS=1"
if defined DIGITS (
    echo ERR   %~1 must be a number, got: !VALUE!
    exit /b 2
)
goto :eof

:usage
echo Usage: scripts\crawl.bat -Url URL [-Depth N] [-RateLimit N] [-Timeout N]
echo                           [-Retry N] [-Output data\katana.jsonl] [-Headless]
echo                           [-Ingest] [-Replace] [extra katana args]
echo.
echo   -Url URL         starting URL ^(required^)
echo   -Depth N         katana -d ^(default 3^)
echo   -RateLimit N     katana -rl ^(default 20^)
echo   -Timeout N       katana -timeout, seconds ^(default 10^)
echo   -Retry N         katana -retry ^(default 2^)
echo   -Output PATH     file inside data\ ^(default data\katana.jsonl^)
echo   -Headless        add -system-chrome -headless for JavaScript sites
echo   -Ingest          run the ingestion pipeline afterwards
echo   -Replace         with -Ingest, replace the existing documents
goto :eof

:usage_error
call :usage
exit /b 2
