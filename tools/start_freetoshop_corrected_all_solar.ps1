$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$artifact = if ($env:ORCHID_CORRECTED_ARTIFACT) {
    $env:ORCHID_CORRECTED_ARTIFACT
} else {
    Join-Path $root 'artifacts\agent_benchmarks\freetoshop_pi_vs_orchid_corrected_all_solar'
}
$stage = if ($env:ORCHID_CORRECTED_STAGE) {
    $env:ORCHID_CORRECTED_STAGE
} else {
    'C:\Users\disti\benchmark_work\freetoshop_pi_vs_orchid_corrected_all_solar_20260823'
}
$database = Join-Path $stage 'orchid-state\memory.db'
if (Test-Path -LiteralPath $database) {
    throw "Refusing to reuse benchmark database: $database"
}
if (Get-NetTCPConnection -State Listen -LocalPort 7332 -ErrorAction SilentlyContinue) {
    throw 'Refusing to reuse occupied benchmark gateway port 7332'
}
$key = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($key)) {
    throw 'OPENROUTER_API_KEY is unavailable in the user environment'
}

$logDir = Join-Path $artifact 'gateway'
$null = New-Item -ItemType Directory -Force -Path $logDir
$env:OPENROUTER_API_KEY = $key
$env:ORCHID_DB = $database
$env:ORCHID_BACKEND_URL = 'https://openrouter.ai/api'
$env:ORCHID_BACKEND_API_KEY = $key
$env:ORCHID_BACKEND_MODEL = 'upstage/solar-pro4'
$env:ORCHID_SELECTOR_URL = 'https://openrouter.ai/api'
$env:ORCHID_SELECTOR_API_KEY = $key
$env:ORCHID_SELECTOR_MODEL = 'upstage/solar-pro4'
$env:ORCHID_CANONICALIZER_ENABLED = '0'
$env:ORCHID_CONSOLIDATOR_URL = 'https://openrouter.ai/api'
$env:ORCHID_CONSOLIDATOR_API_KEY = $key
$env:ORCHID_CONSOLIDATOR_MODEL = 'upstage/solar-pro4'
$env:ORCHID_COMPACTION_CONTEXT_TOKENS = '524288'
$env:ORCHID_COMPACTION_RESERVED_OUTPUT_TOKENS = '8192'
$env:ORCHID_COMPACTION_FIXED_OVERHEAD_TOKENS = '4096'
$env:ORCHID_COMPACTION_SAFETY_MARGIN = '0.7'
$env:ORCHID_COMPACTION_REQUEST_AMPLIFICATION = '3.0'
$env:ORCHID_COLD_MEMORY_MODE = 'off'
$env:ORCHID_MODEL_TIMEOUT_SECONDS = '120'

$python = (Get-Command python).Source
$process = Start-Process `
    -FilePath $python `
    -ArgumentList @('-m', 'uvicorn', 'memory_gateway.gateway:app', '--host', '127.0.0.1', '--port', '7332') `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'stdout.log') `
    -RedirectStandardError (Join-Path $logDir 'stderr.log') `
    -PassThru

$deadline = [DateTime]::UtcNow.AddSeconds(20)
do {
    Start-Sleep -Milliseconds 250
    $listener = Get-NetTCPConnection -State Listen -LocalPort 7332 -ErrorAction SilentlyContinue
} while (-not $listener -and [DateTime]::UtcNow -lt $deadline -and -not $process.HasExited)
if (-not $listener) {
    throw 'ORCHID gateway did not bind 127.0.0.1:7332'
}
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:7332/healthz' -TimeoutSec 10
$pidRecord = [ordered]@{
    pid = $process.Id
    started_at = [DateTime]::UtcNow.ToString('o')
    database = $database
    port = 7332
}
$pidRecord | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDir 'PID.json') -Encoding utf8
[pscustomobject]@{
    pid = $process.Id
    database = $database
    selector = 'upstage/solar-pro4'
    canonicalizer_enabled = $false
    consolidator = 'upstage/solar-pro4'
    source_window_tokens = 119466
    cold_memory_mode = 'off'
    health = $health
} | ConvertTo-Json -Compress
