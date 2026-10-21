#Requires -Version 5.1
<#
.SYNOPSIS
    Prepara i file di configurazione locali di Aegis Search su Windows.

.DESCRIPTION
    Copia i template .env.example nei file .env / .env.local senza
    sovrascrivere file già esistenti e crea la cartella data/.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
#>

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot

function Copy-EnvTemplate {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (Test-Path -LiteralPath $Destination) {
        Write-Host "SKIP  $Destination (file esistente)" -ForegroundColor Yellow
        return
    }

    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Template non trovato: $Source"
    }

    Copy-Item -LiteralPath $Source -Destination $Destination
    Write-Host "OK    $Destination creato" -ForegroundColor Green
}

try {
    Write-Host "Aegis Search - setup Windows" -ForegroundColor Cyan
    Write-Host ""

    Copy-EnvTemplate -Source ".env.example" -Destination ".env"
    Copy-EnvTemplate -Source "backend\.env.example" -Destination "backend\.env"
    Copy-EnvTemplate -Source "frontend\.env.example" -Destination "frontend\.env.local"

    $dataDirectory = Join-Path $projectRoot "data"
    if (-not (Test-Path -LiteralPath $dataDirectory)) {
        New-Item -ItemType Directory -Path $dataDirectory | Out-Null
        Write-Host "OK    data\ creata" -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "Prossimi passi:" -ForegroundColor Cyan
    Write-Host "  1. Apri .env e imposta MEILI_MASTER_KEY e BRAVE_API_KEY."
    Write-Host "     Opzionale: EXA_API_KEY, SERPER_API_KEY, TAVILY_API_KEY per lo scudo esterno."
    Write-Host "  2. Se esegui il backend fuori da Docker, allinea backend\.env."
    Write-Host "  3. Avvia lo stack:  docker compose up -d --build"
    Write-Host "  4. Verifica:        curl.exe http://127.0.0.1:8000/api/v1/health"
    Write-Host "  5. Frontend:        cd frontend; npm install; npm run dev"
    Write-Host ""
    Write-Host "Se PowerShell blocca npm o l'attivazione del venv, usa npm.cmd e:"
    Write-Host "  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass"
}
finally {
    Pop-Location
}
