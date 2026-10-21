#Requires -Version 5.1
<#
.SYNOPSIS
    Esegue un crawl Katana nell'immagine Docker e, se richiesto, lo indicizza.

.DESCRIPTION
    * monta la cartella data/ del progetto su /data del container, cosi' il
      JSONL finisce sempre in <progetto>\data\katana.jsonl (mai nella root)
    * lancia l'immagine ufficiale projectdiscovery/katana:latest
    * verifica che il file esista, non sia vuoto e contenga il body HTML
    * con -Ingest esegue la pipeline di ingestione nel container backend

.PARAMETER Url
    URL di partenza del crawl. Obbligatorio.

.PARAMETER Depth
    Profondita' massima del crawl (katana -d). Default 3.

.PARAMETER RateLimit
    Richieste al secondo (katana -rl). Default 20.

.PARAMETER Timeout
    Timeout per singola richiesta in secondi (katana -timeout). Default 10.

.PARAMETER Retry
    Tentativi per richiesta (katana -retry). Default 2.

.PARAMETER Output
    File di output, relativo alla root del progetto. Deve stare dentro data/.
    Default data/katana.jsonl.

.PARAMETER Headless
    Usa Chromium headless (-system-chrome -headless) per i siti che richiedono
    JavaScript. Piu' lento e piu' pesante.

.PARAMETER Ingest
    Dopo il crawl esegue python -m app.ingestion.cli nel container backend.

.PARAMETER Replace
    Usato con -Ingest: sostituisce i documenti esistenti (--replace).

.PARAMETER KatanaArgs
    Argomenti aggiuntivi passati a katana, per esempio "-jc","-kf","all".

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\crawl.ps1 -Url https://developer.mozilla.org -Depth 2

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\crawl.ps1 -Url https://developer.mozilla.org -Depth 2 -Ingest -Replace

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\crawl.ps1 -Url https://example.com -Headless -KatanaArgs "-jc"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Url,

    [ValidateRange(1, 10)]
    [int]$Depth = 3,

    [ValidateRange(1, 500)]
    [int]$RateLimit = 20,

    [ValidateRange(1, 120)]
    [int]$Timeout = 10,

    [ValidateRange(0, 10)]
    [int]$Retry = 2,

    [string]$Output = "data/katana.jsonl",

    [switch]$Headless,

    [switch]$Ingest,

    [switch]$Replace,

    [string[]]$KatanaArgs = @()
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$dataDir = Join-Path $projectRoot "data"

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

function ConvertTo-DockerPath {
    param([string]$Path)
    return ($Path -replace '\\', '/')
}

function Count-Lines {
    param([string]$Path)

    $count = 0
    $reader = [System.IO.StreamReader]::new($Path)
    try {
        while ($null -ne $reader.ReadLine()) {
            $count++
        }
    }
    finally {
        $reader.Dispose()
    }
    return $count
}

if ($Url -notmatch '^https?://') {
    Fail "L'URL deve iniziare con http:// o https:// (ricevuto: $Url)."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "docker non trovato nel PATH. Avvia Docker Desktop e riprova."
}

if (-not (Test-Path -LiteralPath $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
}

$dataFull = [System.IO.Path]::GetFullPath($dataDir)
$fullOutput = if ([System.IO.Path]::IsPathRooted($Output)) {
    [System.IO.Path]::GetFullPath($Output)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $Output))
}

if (-not $fullOutput.StartsWith($dataFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    Fail "L'output deve stare dentro data/: ricevuto $fullOutput."
}

$remainder = $fullOutput.Substring($dataFull.Length)
if (-not ($remainder.StartsWith('\') -or $remainder.StartsWith('/'))) {
    Fail "L'output deve stare dentro data/: ricevuto $fullOutput."
}

$relative = $remainder.TrimStart([char[]]@('\', '/'))
if ([string]::IsNullOrWhiteSpace($relative)) {
    Fail "Indica un file, non la cartella data/: -Output data\katana.jsonl"
}
$containerOutput = "/data/" + ($relative -replace '\\', '/')

$outputDirectory = Split-Path -Parent $fullOutput
if (-not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory | Out-Null
}

# Katana scrive su un file temporaneo: il crawl precedente resta intatto se il
# nuovo crawl fallisce, e non si rischia di mescolare due crawl.
$containerTemp = "$containerOutput.tmp"
$fullTemp = "$fullOutput.tmp"
if (Test-Path -LiteralPath $fullTemp) {
    Remove-Item -LiteralPath $fullTemp -Force
}

Step "Crawl di $Url (depth $Depth, $RateLimit req/s)"
Write-Host "    output: $fullOutput"

$dockerArgs = @(
    "run", "--rm",
    "-v", "$(ConvertTo-DockerPath $dataFull):/data",
    "projectdiscovery/katana:latest",
    "-u", $Url,
    "-d", "$Depth",
    "-rl", "$RateLimit",
    "-timeout", "$Timeout",
    "-retry", "$Retry",
    "-jsonl",
    "-o", $containerTemp
)

if ($Headless) {
    $dockerArgs += @("-system-chrome", "-headless")
}

if ($KatanaArgs.Count -gt 0) {
    $dockerArgs += $KatanaArgs
}

& docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    Fail "katana ha restituito $LASTEXITCODE."
}

if (-not (Test-Path -LiteralPath $fullTemp)) {
    Fail "Katana non ha creato $fullTemp. Controlla che l'URL risponda e che il mount sia corretto."
}

$records = Count-Lines -Path $fullTemp
if ($records -eq 0) {
    Remove-Item -LiteralPath $fullTemp -Force -ErrorAction SilentlyContinue
    Fail "Il crawl è vuoto: nessuna riga scritta da Katana."
}

$bodyLength = 0
try {
    $firstLine = Get-Content -LiteralPath $fullTemp -TotalCount 1
    if ($firstLine) {
        $firstRecord = $firstLine | ConvertFrom-Json
        if ($firstRecord.response.body) {
            $bodyLength = $firstRecord.response.body.Length
        }
    }
}
catch {
    Warn "Impossibile leggere la prima riga del crawl: $($_.Exception.Message)"
}

Move-Item -LiteralPath $fullTemp -Destination $fullOutput -Force

Ok "$records record salvati in $fullOutput"
if ($bodyLength -gt 0) {
    Ok "Body HTML presente nel primo record ($bodyLength caratteri)"
}
else {
    Warn "Il primo record non contiene response.body: la pipeline non potra' estrarre titolo e testo. Rimuovi -omit-body dal crawl."
}

if ($Ingest) {
    Step "Ingestione in Meilisearch"
    Push-Location $projectRoot
    try {
        $ingestArgs = @(
            "compose", "exec", "backend",
            "python", "-m", "app.ingestion.cli",
            "--input", $containerOutput
        )
        if ($Replace) {
            $ingestArgs += "--replace"
        }

        & docker @ingestArgs
        if ($LASTEXITCODE -ne 0) {
            Fail "Ingestione fallita ($LASTEXITCODE). Il backend e' avviato? Prova: docker compose up -d --build backend"
        }
    }
    finally {
        Pop-Location
    }
    Ok "Indice aggiornato da $containerOutput"
}
else {
    Write-Host ""
    Write-Host "Per indicizzare il crawl:" -ForegroundColor Cyan
    Write-Host "  docker compose exec backend python -m app.ingestion.cli --input $containerOutput --replace"
    Write-Host "oppure rilancia questo script con -Ingest -Replace"
}
