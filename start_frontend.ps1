<#
.SYNOPSIS
Starts the Maybech frontend dashboard in the current terminal.

.DESCRIPTION
Runs the Next.js dev server from `frontend/` and leaves frontend logs visible.
It reads NEXT_PUBLIC_API_URL and MAYBECH_API_TOKEN from the repository `.env`
when the current environment does not already set them.

.PARAMETER Port
Local Next.js dashboard port. Defaults to 3000.

.PARAMETER BackendUrl
Backend API URL exposed to the frontend. Defaults to NEXT_PUBLIC_API_URL from
the environment or `.env`, then falls back to http://127.0.0.1:8000.

.EXAMPLE
.\start_frontend.ps1

Starts the dashboard on http://localhost:3000.
#>

[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 3000,

    [string]$BackendUrl = $env:NEXT_PUBLIC_API_URL,

    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($Help) {
    @"
Usage:
  .\start_frontend.ps1 [options]

Starts the Maybech frontend dashboard in this terminal.

Options:
  -Port <port>
      Frontend port. Default: 3000

  -BackendUrl <url>
      Backend API URL. Default: NEXT_PUBLIC_API_URL from env or .env,
      then http://127.0.0.1:8000.

  -Help
      Show this help.

Examples:
  .\start_frontend.ps1
  .\start_frontend.ps1 -Port 3001
"@
    exit 0
}

$repoRoot = Resolve-Path $PSScriptRoot
$frontendRoot = Join-Path $repoRoot "frontend"

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

if (-not $BackendUrl) {
    $BackendUrl = Read-DotenvValue -Name "NEXT_PUBLIC_API_URL"
}
if (-not $BackendUrl) {
    $BackendUrl = "http://127.0.0.1:8000"
}

if (-not $env:NEXT_PUBLIC_MAYBECH_API_TOKEN) {
    $apiToken = Read-DotenvValue -Name "MAYBECH_API_TOKEN"
    if ($apiToken) {
        $env:NEXT_PUBLIC_MAYBECH_API_TOKEN = $apiToken
    }
}

$env:NEXT_PUBLIC_API_URL = $BackendUrl
Write-Host "Starting Maybech frontend: http://localhost:$Port"
Write-Host "Backend API: $BackendUrl"
Set-Location $frontendRoot
& npm.cmd run dev -- -p $Port
