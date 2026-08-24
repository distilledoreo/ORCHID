$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$artifact = Join-Path $root "artifacts\agent_benchmarks\orchid_foreground_substitution_v1\preflight_$stamp"
$stage = "C:\Users\disti\benchmark_work\orchid_foreground_preflight_$stamp"
$sourceStage = "C:\Users\disti\benchmark_work\freetoshop_pi_vs_orchid_corrected_all_solar_20260823"
$goal = Join-Path $root "artifacts\agent_benchmarks\freetoshop_pi_vs_orchid_corrected_all_solar\BENCHMARK_GOAL.md"
$port = 7334
$database = Join-Path $stage "orchid-state\memory.db"

if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
    throw "Refusing to reuse occupied preflight gateway port $port"
}
$key = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($key)) {
    throw 'OPENROUTER_API_KEY is unavailable in the user environment'
}

New-Item -ItemType Directory -Force -Path $stage, (Join-Path $stage 'orchid-state'), (Join-Path $stage 'sessions'), (Join-Path $artifact 'gateway'), (Join-Path $artifact 'controller') | Out-Null
Copy-Item -LiteralPath $goal -Destination (Join-Path $artifact 'BENCHMARK_GOAL.md') -Force
robocopy (Join-Path $sourceStage 'arm-b-orchid') (Join-Path $stage 'arm-b-orchid') /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy arm-b-orchid failed: $LASTEXITCODE" }
Copy-Item -LiteralPath (Join-Path $artifact 'BENCHMARK_GOAL.md') -Destination (Join-Path $stage 'arm-b-orchid\BENCHMARK_GOAL.md') -Force
robocopy (Join-Path $sourceStage 'pi-agent-b') (Join-Path $stage 'pi-agent-preflight') /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy pi-agent failed: $LASTEXITCODE" }

$modelsPath = Join-Path $stage 'pi-agent-preflight\models.json'
python -c @"
import json
from pathlib import Path
path = Path(r'$modelsPath')
payload = json.loads(path.read_text(encoding='utf-8'))
provider = payload['providers']['orchid']
provider['baseUrl'] = 'http://127.0.0.1:$port/v1'
provider['headers']['X-Memory-Thread'] = 'orchid-preflight'
path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
"@

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
$env:ORCHID_FOREGROUND_TELEMETRY_PATH = (Join-Path $artifact 'telemetry.jsonl')
$env:ORCHID_PREFLIGHT_STAGE = $stage
$env:ORCHID_PREFLIGHT_ARTIFACT = $artifact
$env:ORCHID_PREFLIGHT_GATEWAY_URL = "http://127.0.0.1:$port"
$env:ORCHID_PREFLIGHT_THREAD_ID = 'orchid-preflight'

$python = (Get-Command python).Source
$gateway = Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'memory_gateway.gateway:app', '--host', '127.0.0.1', '--port', "$port") -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $artifact 'gateway\stdout.log') -RedirectStandardError (Join-Path $artifact 'gateway\stderr.log') -PassThru
$deadline = [DateTime]::UtcNow.AddSeconds(20)
do {
    Start-Sleep -Milliseconds 250
    $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
} while (-not $listener -and [DateTime]::UtcNow -lt $deadline -and -not $gateway.HasExited)
if (-not $listener) { throw 'ORCHID preflight gateway did not bind 127.0.0.1:7334' }
Invoke-RestMethod -Uri "http://127.0.0.1:$port/healthz" -TimeoutSec 10 | Out-Null
@{ pid = $gateway.Id; started_at = [DateTime]::UtcNow.ToString('o'); database = $database; port = $port } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $artifact 'gateway\PID.json') -Encoding utf8

Write-Output "stage=$stage"
Write-Output "artifact=$artifact"
Write-Output "gateway_pid=$($gateway.Id)"
node (Join-Path $root 'tools\orchid_foreground_preflight_driver.mjs')
