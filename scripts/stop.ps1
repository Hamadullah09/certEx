<#
.SYNOPSIS
    Stop the CertEx stack.

.DESCRIPTION
    `docker compose down` stops the containers and leaves the volumes, so the
    register, the scans and the queue survive a restart. That is the default here
    too, and the two ways of losing data are separate, named flags rather than
    something you can reach by accident.

.PARAMETER Volumes
    Also delete the volumes: the database, the object store and Redis. This
    destroys the register and every uploaded document. You are asked to confirm.

.PARAMETER Images
    Also remove the images this project built. Frees a few gigabytes; the next run
    needs `-Build`.

.PARAMETER Service
    Stop only these services, leaving the rest running.

.EXAMPLE
    ./scripts/stop.ps1
    Stop everything. Data is kept.

.EXAMPLE
    ./scripts/stop.ps1 -Service web
    Stop the frontend and leave the API and the workers running.

.EXAMPLE
    ./scripts/stop.ps1 -Volumes
    Start over from an empty register. Asks first.
#>
[CmdletBinding()]
param(
    [switch]$Volumes,
    [switch]$Images,
    [string[]]$Service = @()
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Step { param([string]$Text) Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Note { param([string]$Text) Write-Host "    $Text" -ForegroundColor DarkGray }

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Step 'Docker is not running'
    Write-Note 'nothing to stop'
    exit 0
}

# --- Stopping some services, not the stack -----------------------------------
if ($Service.Count -gt 0) {
    if ($Volumes -or $Images) {
        Write-Host '-Volumes and -Images apply to the whole stack, not to named services.' -ForegroundColor Red
        exit 1
    }
    Write-Step ("Stopping {0}" -f ($Service -join ', '))
    & docker compose stop @Service
    exit $LASTEXITCODE
}

# --- Destroying data is a deliberate act -------------------------------------
if ($Volumes) {
    Write-Host ''
    Write-Host 'This deletes the register, every uploaded document and the queue.' -ForegroundColor Yellow
    Write-Host 'It cannot be undone.' -ForegroundColor Yellow
    $answer = Read-Host 'Type DELETE to continue'
    if ($answer -ne 'DELETE') {
        Write-Step 'Left alone'
        Write-Note 'nothing was removed'
        exit 0
    }
}

$composeArgs = @('compose', 'down')
if ($Volumes) { $composeArgs += '--volumes' }
if ($Images) { $composeArgs += @('--rmi', 'local') }

Write-Step 'Stopping the stack'
& docker @composeArgs
$code = $LASTEXITCODE

if ($code -eq 0) {
    if ($Volumes) {
        Write-Note 'volumes removed - the next start begins with an empty register'
    }
    else {
        Write-Note 'volumes kept - the register and the scans are still there'
    }
    Write-Note './scripts/run.ps1   start it again'
}

exit $code
