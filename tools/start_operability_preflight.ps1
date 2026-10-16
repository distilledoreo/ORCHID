param(
    [Parameter(Mandatory = $true)][string]$Database,
    [Parameter(Mandatory = $true)][string]$ArtifactDirectory,
    [int]$Port = 7333,
    [int]$ContextTokens = 12000
)

if (Test-Path -LiteralPath $Database) {
    throw "Refusing to reuse preflight database: $Database"
}
$key = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($key)) {
    throw 'OPENROUTER_API_KEY is unavailable in the user environment'
}

$null = New-Item -ItemType Directory -Force -Path $ArtifactDirectory
$env:ORCHID_DB = $Database
$env:ORCHID_BACKEND_URL = 'https://openrouter.ai/api'
$env:ORCHID_BACKEND_API_KEY = $key
$env:ORCHID_BACKEND_MODEL = 'upstage/solar-pro4'
$env:ORCHID_SELECTOR_URL = 'http://127.0.0.1:1234/v1'
$env:ORCHID_SELECTOR_MODEL = 'qwen3.5-4b@q6_k'
$env:ORCHID_SELECTOR_API_KEY = ''
$env:ORCHID_CANONICALIZER_URL = 'http://127.0.0.1:1234/v1'
$env:ORCHID_CANONICALIZER_MODEL = 'qwen3.5-4b@q6_k'
$env:ORCHID_CANONICALIZER_API_KEY = ''
$env:ORCHID_CONSOLIDATOR_URL = 'https://openrouter.ai/api'
$env:ORCHID_CONSOLIDATOR_MODEL = 'upstage/solar-pro4'
$env:ORCHID_CONSOLIDATOR_API_KEY = $key
$env:ORCHID_CONTEXT_TOKENS = [string]$ContextTokens
$env:ORCHID_COLD_MEMORY_MODE = 'off'
$env:ORCHID_WORKER_POLL_SECONDS = '0.25'
$env:ORCHID_MODEL_TIMEOUT_SECONDS = '180'

$python = (Get-Command python).Source
$stdout = Join-Path $ArtifactDirectory 'gateway.stdout.log'
$stderr = Join-Path $ArtifactDirectory 'gateway.stderr.log'
$process = Start-Process -FilePath $python -ArgumentList @(
    '-m', 'uvicorn', 'memory_gateway.gateway:app',
    '--host', '127.0.0.1', '--port', [string]$Port
) -WorkingDirectory (Split-Path -Parent $PSScriptRoot) -WindowStyle Hidden `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru

Start-Sleep -Seconds 3
$health = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/healthz" -f $Port) -TimeoutSec 10
[pscustomobject]@{
    pid = $process.Id
    port = $Port
    database = $Database
    health = $health
} | ConvertTo-Json -Compress
