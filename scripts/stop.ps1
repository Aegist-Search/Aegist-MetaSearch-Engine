#Requires -Version 5.1
<#
.SYNOPSIS
    Ferma frontend e backend di Aegis Search.

.DESCRIPTION
    * termina l'albero di processi del frontend avviato da start.ps1
      (npm -> node) leggendo logs\frontend.pid
    * se il PID non c'e' piu', cerca il processo node in ascolto sulla porta
    * ferma i container con docker compose stop (i volumi restano)

.PARAMETER Port
    Porta del frontend usata per il fallback. Default 3000.

.PARAMETER Down
    Usa "docker compose down": rimuove container e rete, ma non i volumi.

.PARAMETER Volumes
    Implica "docker compose down --volumes" ed elimina anche il volume
    meili_data: l'indice Meilisearch viene perso e va ricreato con una nuova
    ingestione.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\stop.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\stop.ps1 -Down

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\stop.ps1 -Volumes
#>

[CmdletBinding()]
param(
    [int]$Port = 3000,
    [switch]$Down,
    [switch]$Volumes
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $projectRoot "logs"
$pidFile = Join-Path $logDir "frontend.pid"

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

Step "Arresto frontend"

$stoppedFrontend = $false

if (Test-Path -LiteralPath $pidFile) {
    # Get-Content -Raw restituisce $null su un file vuoto: il cast evita un errore.
    $savedRaw = ([string](Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue)).Trim()
    if ($savedRaw -match '^\d+$') {
        $savedPid = [int]$savedRaw
        if (Stop-ProcessTree -ProcessId $savedPid) {
            Ok "Frontend fermato (PID $savedPid)"
            $stoppedFrontend = $true
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

# Fallback: se il PID e' andato perso, chiudi il node che ascolta sulla porta.
$listenerPid = Get-ListenerPid -LocalPort $Port
if ($listenerPid -gt 0) {
    $listener = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
    if ($null -ne $listener -and $listener.ProcessName -eq "node") {
        if (Stop-ProcessTree -ProcessId $listenerPid) {
            Ok "Processo node sulla porta $Port fermato (PID $listenerPid)"
            $stoppedFrontend = $true
        }
    }
    elseif ($null -ne $listener) {
        Warn "La porta $Port e' occupata da $($listener.ProcessName) (PID $listenerPid): non lo tocco."
    }
}

if (-not $stoppedFrontend) {
    Warn "Nessun frontend avviato dallo script trovato sulla porta $Port"
}

Step "Arresto backend (Docker Compose)"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Warn "docker non trovato nel PATH: i container non sono stati fermati"
    exit 1
}

Push-Location $projectRoot
try {
    if ($Volumes) {
        Warn "Elimino anche il volume meili_data: l'indice andra' ricreato con una nuova ingestione."
        & docker compose down --volumes
    }
    elseif ($Down) {
        & docker compose down
    }
    else {
        & docker compose stop
    }

    if ($LASTEXITCODE -ne 0) {
        Warn "docker compose ha restituito $LASTEXITCODE"
        exit 1
    }
}
finally {
    Pop-Location
}

if ($Volumes) {
    Ok "Container, rete e volume dell'indice rimossi"
}
elseif ($Down) {
    Ok "Container e rete rimossi (volume meili_data conservato)"
}
else {
    Ok "Container fermati (volumi conservati)"
}

Write-Host ""
Write-Host "Tutto fermo. Per riavviare: .\scripts\start.cmd" -ForegroundColor Cyan
