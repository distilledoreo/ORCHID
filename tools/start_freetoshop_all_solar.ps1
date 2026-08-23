$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$artifact = if ($env:ORCHID_ALL_SOLAR_ARTIFACT) { $env:ORCHID_ALL_SOLAR_ARTIFACT } else { Join-Path $root 'artifacts\agent_benchmarks\freetoshop_pi_vs_orchid_all_solar' }
$stage = if ($env:ORCHID_ALL_SOLAR_STAGE) { $env:ORCHID_ALL_SOLAR_STAGE } else { 'C:\Users\disti\benchmark_work\freetoshop_pi_vs_orchid_all_solar_20260823' }
$stateName = 'orchid-state'
$db = Join-Path $stage "$stateName\memory.db"
if (Test-Path -LiteralPath $db) { throw "Refusing to reuse benchmark database: $db" }
$key = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($key)) { throw 'OPENROUTER_API_KEY is unavailable in the user environment' }
$logDir = Join-Path $artifact 'gateway'
$null = New-Item -ItemType Directory -Force -Path $logDir
$env:OPENROUTER_API_KEY = $key
$env:ORCHID_DB = $db
$env:ORCHID_BACKEND_URL = 'https://openrouter.ai/api'
$env:ORCHID_BACKEND_API_KEY = $key
$env:ORCHID_BACKEND_MODEL = 'upstage/solar-pro4'
$env:ORCHID_SELECTOR_URL = 'https://openrouter.ai/api'
$env:ORCHID_SELECTOR_API_KEY = $key
$env:ORCHID_SELECTOR_MODEL = 'upstage/solar-pro4'
$env:ORCHID_CANONICALIZER_URL = 'https://openrouter.ai/api'
$env:ORCHID_CANONICALIZER_API_KEY = $key
$env:ORCHID_CANONICALIZER_MODEL = 'upstage/solar-pro4'
$env:ORCHID_CONSOLIDATOR_URL = 'https://openrouter.ai/api'
$env:ORCHID_CONSOLIDATOR_API_KEY = $key
$env:ORCHID_CONSOLIDATOR_MODEL = 'upstage/solar-pro4'
$env:ORCHID_COLD_MEMORY_MODE = 'inject'
$env:ORCHID_COLD_MEMORY_TIMEOUT_MS = '50'
$env:ORCHID_COLD_MEMORY_TOKEN_BUDGET = '512'
$env:ORCHID_COLD_MEMORY_MAX_INJECTED = '3'
$python = (Get-Command python).Source
$process = Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','memory_gateway.gateway:app','--host','127.0.0.1','--port','7332') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'stdout.log') -RedirectStandardError (Join-Path $logDir 'stderr.log') -PassThru
Start-Sleep -Seconds 3
$listener = Get-NetTCPConnection -State Listen -LocalPort 7332 -ErrorAction SilentlyContinue
if (-not $listener) { throw 'ORCHID gateway did not bind 127.0.0.1:7332' }
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:7332/healthz' -TimeoutSec 10
[pscustomobject]@{ pid=$process.Id; database=$db; selector='https://openrouter.ai/api/upstage/solar-pro4'; canonicalizer='https://openrouter.ai/api/upstage/solar-pro4'; consolidator='https://openrouter.ai/api/upstage/solar-pro4'; health=$health } | ConvertTo-Json -Compress
