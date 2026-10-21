#Requires -Version 5.1
<#
.SYNOPSIS
    Avvia backend e frontend di Aegis Search in background, senza aprire terminali.

.DESCRIPTION
    * crea data/ e logs/ se mancano
    * avvia Meilisearch e FastAPI con Docker Compose (container detached, nessuna finestra)
    * avvia Next.js con "npm run dev" in un processo nascosto, log su file
    * attende che backend e frontend rispondano, poi stampa gli URL
    * con -Stop ferma il frontend e i container

.PARAMETER Stop
    Ferma il frontend e i container invece di avviarli.

.PARAMETER NoBuild
    Non ricostruisce l'immagine del backend (avvio piu' veloce).

.PARAMETER Port
    Porta del frontend. Default 3000.

.PARAMETER TimeoutSeconds
    Attesa massima per backend e frontend. Default 120.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1 -Stop
#>

[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$NoBuild,
    [int]$Port = 3000,
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $projectRoot "frontend"
$dataDir = Join-Path $projectRoot "data"
$logDir = Join-Path $projectRoot "logs"
$pidFile = Join-Path $logDir "frontend.pid"
$stdoutLog = Join-Path $logDir "frontend.out.log"
$stderrLog = Join-Path $logDir "frontend.err.log"
$backendHealthUrl = "http://127.0.0.1:8000/api/v1/health"
$frontendUrl = "http://127.0.0.1:$Port"

function Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Ok {
    param([string]$Message)
    Write-Host "OK   $Message" -ForegroundColor Green
}

function Warn {
    param([string]$Message)
    Write-Host "WARN $Message" -ForegroundColor Yellow
}

function Fail {
    param([string]$Message)
    Write-Host "ERR  $Message" -ForegroundColor Red
    exit 1
}

# Restituisce il PID in ascolto sulla porta, oppure 0.
function Get-ListenerPid {
    param([int]$LocalPort)

    if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        return 0
    }

    $connection = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $connection) {
        return 0
    }
    return [int]$connection.OwningProcess
}

# Verifica se la porta TCP accetta connessioni.
function Test-PortOpen {
    param(
        [string]$ComputerName = "127.0.0.1",
        [int]$TcpPort
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync($ComputerName, $TcpPort)
        if (-not $task.Wait(500)) {
            return $false
        }
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

# Termina il processo e tutti i figli (npm -> node).
function Stop-ProcessTree {
    param([int]$ProcessId)

    if ($ProcessId -le 0) {
        return $false
    }
    if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
        return $false
    }

    & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

if ($Stop) {
    Step "Arresto frontend"
    $stopped = $false

    if (Test-Path -LiteralPath $pidFile) {
        $savedPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
        if ($savedPid -match '^\d+$') {
            if (Stop-ProcessTree -ProcessId ([int]$savedPid)) {
                $stopped = $true
            }
        }
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }

    # Fallback: processi node rimasti in ascolto sulla porta, utile se il PID e' andato perso.
    $listenerPid = Get-ListenerPid -LocalPort $Port
    if ($listenerPid -gt 0) {
        $listener = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
        if ($null -ne $listener -and $listener.ProcessName -eq "node") {
            if (Stop-ProcessTree -ProcessId $listenerPid) {
                $stopped = $true
            }
        }
        elseif ($null -ne $listener) {
            Warn "La porta $Port e' occupata da $($listener.ProcessName) (PID $listenerPid): non la tocco."
        }
    }

    if ($stopped) {
        Ok "Frontend fermato"
    }
    else {
        Warn "Nessun frontend avviato dallo script trovato sulla porta $Port"
    }

    Step "Arresto backend (Docker Compose)"
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Push-Location $projectRoot
        try {
            & docker compose stop
            if ($LASTEXITCODE -eq 0) {
                Ok "Container fermati (il volume di Meilisearch resta)"
            }
            else {
                Warn "docker compose stop ha restituito $LASTEXITCODE"
            }
        }
        finally {
            Pop-Location
        }
    }
    else {
        Warn "docker non trovato nel PATH"
    }

    Write-Host ""
    return
}

Step "Aegis Search - avvio in background"

foreach ($directory in @($dataDir, $logDir)) {
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory | Out-Null
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".env"))) {
    Warn ".env non trovato: esegui prima .\scripts\setup.ps1 e compila le chiavi."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "docker non trovato nel PATH. Avvia Docker Desktop e riprova."
}

Push-Location $projectRoot
try {
    Step "Backend: docker compose up -d"
    if ($NoBuild) {
        & docker compose up -d
    }
    else {
        & docker compose up -d --build
    }
    if ($LASTEXITCODE -ne 0) {
        Fail "docker compose up ha restituito $LASTEXITCODE"
    }
    Ok "Container avviati"
}
finally {
    Pop-Location
}

Step "Attendo il backend su $backendHealthUrl"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$backendReady = $false
while ((Get-Date) -lt $deadline) {
    try {
        $null = Invoke-RestMethod -Uri $backendHealthUrl -TimeoutSec 3
        $backendReady = $true
        break
    }
    catch {
        Start-Sleep -Milliseconds 750
    }
}

if ($backendReady) {
    Ok "Backend pronto: http://127.0.0.1:8000/docs"
}
else {
    Warn "Backend non raggiungibile entro $TimeoutSeconds s. Controlla con: docker compose logs backend"
}

$existingPid = Get-ListenerPid -LocalPort $Port
if ($existingPid -gt 0) {
    Warn "La porta $Port e' gia' in uso (PID $existingPid): il frontend non viene avviato di nuovo."
}
else {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($null -eq $npm) {
        $npm = Get-Command npm -ErrorAction SilentlyContinue
    }
    if ($null -eq $npm) {
        Fail "npm non trovato nel PATH. Installa Node.js 20+ e riapri il terminale."
    }

    $nextIntlInstalled = Test-Path -LiteralPath (Join-Path $frontendDir "node_modules\next-intl\package.json")
    if (-not (Test-Path -LiteralPath (Join-Path $frontendDir "node_modules")) -or -not $nextIntlInstalled) {
        Step "Installazione dipendenze frontend (npm install)"
        Push-Location $frontendDir
        try {
            # npm install (not ci): a newly added dependency may not be in the
            # lockfile yet, so ci would fail on a fresh clone.
            & $npm.Source install --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) {
                Fail "npm install ha restituito $LASTEXITCODE"
            }
        }
        finally {
            Pop-Location
        }
        Ok "Dipendenze installate"
    }

    Step "Frontend: npm run dev (processo nascosto, log in logs\)"
    Set-Content -LiteralPath $stdoutLog -Value ""
    Set-Content -LiteralPath $stderrLog -Value ""

    $comSpec = $env:ComSpec
    if ([string]::IsNullOrWhiteSpace($comSpec)) {
        $comSpec = Join-Path $env:SystemRoot "System32\cmd.exe"
    }

    # cmd.exe /c: avvio senza finestra visibile e senza legare il processo a questa shell.
    $frontend = Start-Process -FilePath $comSpec `
        -ArgumentList "/c npm run dev -- --port $Port" `
        -WorkingDirectory $frontendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru
    Set-Content -LiteralPath $pidFile -Value $frontend.Id

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $frontendReady = $false
    while ((Get-Date) -lt $deadline) {
        if ($frontend.HasExited) {
            break
        }
        # Next.js puo' ascoltare su 127.0.0.1 o su ::1: verifica entrambi.
        if ((Test-PortOpen -TcpPort $Port) -or (Test-PortOpen -ComputerName "localhost" -TcpPort $Port)) {
            $frontendReady = $true
            break
        }
        Start-Sleep -Milliseconds 750
    }

    if ($frontendReady) {
        Ok "Frontend pronto: $frontendUrl"
    }
    else {
        Warn "Il frontend non ha aperto la porta $Port. Ultime righe dei log:"
        foreach ($logFile in @($stderrLog, $stdoutLog)) {
            if (Test-Path -LiteralPath $logFile) {
                Write-Host "--- $logFile"
                Get-Content -LiteralPath $logFile -Tail 15 | ForEach-Object { Write-Host "    $_" }
            }
        }
        Fail "Frontend non avviato. Controlla i log in logs\."
    }
}

Write-Host ""
Write-Host "Aegis Search in esecuzione" -ForegroundColor Cyan
Write-Host "  Frontend   $frontendUrl"
Write-Host "  Backend    http://127.0.0.1:8000"
Write-Host "  API docs   http://127.0.0.1:8000/docs"
Write-Host "  Log        logs\frontend.out.log, logs\frontend.err.log"
Write-Host "  Stop       .\scripts\stop.cmd"

$crawlFile = Join-Path $dataDir "katana.jsonl"
if (Test-Path -LiteralPath $crawlFile) {
    Write-Host ""
    Write-Host "Crawl disponibile: data\katana.jsonl" -ForegroundColor Cyan
    Write-Host "  docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl"
}
