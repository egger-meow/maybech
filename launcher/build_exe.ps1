<#
.SYNOPSIS
Builds maybech.exe from launcher/maybech_launcher.py.

.DESCRIPTION
Uses `uvx pyinstaller` (an ephemeral tool env) so PyInstaller never becomes a
dependency of the main project venv. The resulting maybech.exe is a
standalone single-file app with no console window; it can be copied and
double-clicked from anywhere on the machine (it stores which project folder
to run against in %LOCALAPPDATA%\maybech-launcher\config.json, prompting on
first run if that's not set yet).

.PARAMETER OutputDir
Where to place the built exe. Defaults to <repoRoot>\dist.

.EXAMPLE
.\launcher\build_exe.ps1
#>

[CmdletBinding()]
param(
    [string]$OutputDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $OutputDir) {
    $OutputDir = Join-Path $repoRoot "dist"
}

$workPath = Join-Path $env:TEMP "maybech-pyinstaller-build"

Write-Host "Building maybech.exe with PyInstaller (via uvx)..."
uvx pyinstaller `
    --onefile `
    --noconsole `
    --name maybech `
    --distpath $OutputDir `
    --workpath $workPath `
    --specpath $workPath `
    (Join-Path $PSScriptRoot "maybech_launcher.py")

Write-Host "Done: $(Join-Path $OutputDir 'maybech.exe')"
Write-Host "You can copy maybech.exe anywhere; it will ask for the project folder on first run."
