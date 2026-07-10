<#
.SYNOPSIS
Starts the Maybech backend API and its LINE webhook tunnel.

.DESCRIPTION
Runs the Maybech backend in the current terminal so backend logs stay visible.
By default it also opens a separate PowerShell window for the ngrok LINE webhook
tunnel, so tunnel logs stay separate from backend logs.

.PARAMETER Mode
Runtime mode passed to `src.runtime api`. Valid values are simulation, demo,
live_safe, and live_armed. Defaults to demo.

.PARAMETER Port
Local FastAPI port. Defaults to 8000.

.PARAMETER NgrokDomain
Reserved ngrok domain. If omitted, the script reads MAYBECH_NGROK_DOMAIN from
the current environment or from the repository `.env` file.

.PARAMETER NoLineWebhookTunnel
Start only the backend and do not open the ngrok tunnel window.

.EXAMPLE
.\start_backend.ps1

Starts the Demo backend in this terminal and opens ngrok for the LINE webhook
in a separate terminal window.

.EXAMPLE
.\start_backend.ps1 -Mode simulation -NoLineWebhookTunnel

Starts only the Simulation backend.
#>

[CmdletBinding()]
param(
    [ValidateSet("simulation", "demo", "live_safe", "live_armed")]
    [string]$Mode = "demo",

    [ValidateRange(1, 65535)]
    [int]$Port = 8000,

    [string]$NgrokDomain = $env:MAYBECH_NGROK_DOMAIN,

    [switch]$NoLineWebhookTunnel,

    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($Help) {
    @"
Usage:
  .\start_backend.ps1 [options]

Starts the Maybech backend API in this terminal. By default it also opens a
separate PowerShell window for the ngrok LINE webhook tunnel.

Options:
  -Mode <simulation|demo|live_safe|live_armed>
      Backend runtime mode. Default: demo

  -Port <port>
      Backend FastAPI port. Default: 8000

  -NgrokDomain <domain>
      Reserved ngrok domain. Defaults to MAYBECH_NGROK_DOMAIN from env or .env.

  -NoLineWebhookTunnel
      Do not start ngrok.

  -Help
      Show this help.

Examples:
  .\start_backend.ps1
  .\start_backend.ps1 -Mode demo -Port 8000
  .\start_backend.ps1 -Mode simulation -NoLineWebhookTunnel
"@
    exit 0
}

$repoRoot = Resolve-Path $PSScriptRoot
Set-Location $repoRoot

function Read-DotenvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $envPath = Join-Path $repoRoot ".env"
    if (-not (Test-Path $envPath)) {
        return ""
    }

    $escapedName = [Regex]::Escape($Name)
    $line = Get-Content $envPath |
        Where-Object { $_ -match "^\s*$escapedName\s*=" } |
        Select-Object -First 1
    if (-not $line) {
        return ""
    }

    return ($line -replace "^\s*$escapedName\s*=", "").Trim().Trim('"').Trim("'")
}

if (-not $NgrokDomain) {
    $NgrokDomain = Read-DotenvValue -Name "MAYBECH_NGROK_DOMAIN"
}

if (-not $NoLineWebhookTunnel -and -not $NgrokDomain) {
    throw "Set MAYBECH_NGROK_DOMAIN in .env, pass -NgrokDomain, or use -NoLineWebhookTunnel."
}

$portOwner = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($portOwner) {
    $owner = Get-Process -Id $portOwner.OwningProcess -ErrorAction SilentlyContinue
    $ownerName = if ($owner) { $owner.ProcessName } else { "unknown" }
    throw "Port $Port is already in use by PID $($portOwner.OwningProcess) ($ownerName). Stop that backend first, or pass -Port."
}

$leaseCheckCode = @"
from src.config.settings import settings
from src.runtime.lease import RuntimeLease

lease = RuntimeLease(db_path=settings.MAYBECH_DB_PATH)
status = lease.acquire()
lease.release()
print(status.database)
"@
$localPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path $localPython) {
    $leaseCheck = & $localPython -c $leaseCheckCode 2>&1
} else {
    $leaseCheck = & uv run python -c $leaseCheckCode 2>&1
}
if ($LASTEXITCODE -ne 0) {
    throw "Maybech runtime lease is not available. Another backend is still running or holding the database. Details: $leaseCheck"
}

if (-not $NoLineWebhookTunnel) {
    $tunnelCommand = @"
Set-Location '$repoRoot'
`$backendUrl = 'http://127.0.0.1:$Port/health'
`$ready = `$false
for (`$i = 0; `$i -lt 30; `$i++) {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri `$backendUrl -TimeoutSec 2 | Out-Null
        `$ready = `$true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not `$ready) {
    Write-Host 'Backend did not become healthy; ngrok tunnel was not started.'
    exit 1
}
Write-Host 'Starting LINE webhook tunnel: https://$NgrokDomain/notifications/line/webhook'
ngrok http --domain=$NgrokDomain $Port
"@
    Start-Process -FilePath "powershell" -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-Command", $tunnelCommand
    )
}

Write-Host "Starting Maybech backend: http://127.0.0.1:$Port ($Mode)"
if (-not $NoLineWebhookTunnel) {
    Write-Host "LINE webhook URL: https://$NgrokDomain/notifications/line/webhook"
}
& uv run python -m src.runtime api --mode $Mode --port $Port
