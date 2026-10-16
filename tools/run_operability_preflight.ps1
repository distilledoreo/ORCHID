param(
    [Parameter(Mandatory = $true)][string]$Workspace,
    [Parameter(Mandatory = $true)][string]$PiAgentDirectory,
    [Parameter(Mandatory = $true)][string]$SessionDirectory,
    [Parameter(Mandatory = $true)][string]$ArtifactDirectory,
    [Parameter(Mandatory = $true)][string]$Prompt
)

$key = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($key)) {
    throw 'OPENROUTER_API_KEY is unavailable in the user environment'
}
$env:OPENROUTER_API_KEY = $key
$env:PREFLIGHT_WORKSPACE = $Workspace
$env:PREFLIGHT_PI_AGENT_DIR = $PiAgentDirectory
$env:PREFLIGHT_SESSION_DIR = $SessionDirectory
$env:PREFLIGHT_ARTIFACT_DIR = $ArtifactDirectory
$env:PREFLIGHT_PROMPT = $Prompt
$env:PREFLIGHT_MAX_RUNTIME_MS = '2700000'
$env:PREFLIGHT_CHECKPOINT_MS = '60000'

node (Join-Path (Split-Path -Parent $PSScriptRoot) 'tools\pi_operability_preflight.mjs')
