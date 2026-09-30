<#
.SYNOPSIS
    Start the CertEx stack and tell you where it is.

.DESCRIPTION
    Wraps `docker compose up` with the three things that otherwise have to be
    remembered: that Docker Desktop may not be running yet, that a first run needs
    a .env, and that the ports are configurable so the URLs printed at the end are
    not always localhost:3000.

    Nothing here is required - `docker compose up` works on its own. This exists so
    that starting the stack is one command whose output says what happened.

.PARAMETER Build
    Rebuild the images first. Needed after a dependency change; not after an edit
    to application code, which is mounted into the containers.

.PARAMETER Attach
    Run in the foreground and stream the logs, the way plain `docker compose up`
    does. Without it the stack is started detached and this script returns.

.PARAMETER Service
    Start only these services and what they depend on, e.g. `postgres redis minio`
    to run the test suite against a bare stack.

.EXAMPLE
    ./scripts/run.ps1
    Start everything, detached, and print the URLs.

.EXAMPLE
    ./scripts/run.ps1 -Build
    Rebuild the images, then start.

.EXAMPLE
    ./scripts/run.ps1 -Service postgres, redis, minio
    Just the data stores, which is what the test suite needs.
#>
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$Attach,
    [string[]]$Service = @()
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Step { param([string]$Text) Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Note { param([string]$Text) Write-Host "    $Text" -ForegroundColor DarkGray }

# --- Docker ------------------------------------------------------------------
# `docker info` is the honest check: the CLI exists long before the engine is
# ready, and every later command fails obscurely until it is.
Write-Step 'Checking Docker'
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Docker is not responding.' -ForegroundColor Red
    Write-Note 'Start Docker Desktop and wait for it to say "Engine running", then try again.'
    exit 1
}
Write-Note 'engine is up'

# --- .env --------------------------------------------------------------------
# The stack has defaults for everything, so a missing .env is a working stack on
# the default ports. Copying the example is still the better first run, because
# that is where the ports and the seeded accounts are documented.
if (-not (Test-Path '.env')) {
    Write-Step 'Creating .env from .env.example'
    Copy-Item '.env.example' '.env'
    Write-Note 'edit it before deploying anywhere - the shipped secrets are public'
}

# --- Up ----------------------------------------------------------------------
$composeArgs = @('compose', 'up')
if ($Build) { $composeArgs += '--build' }
if (-not $Attach) { $composeArgs += '-d' }
if ($Service.Count -gt 0) { $composeArgs += $Service }

Write-Step ("Starting {0}" -f ($(if ($Service.Count) { $Service -join ', ' } else { 'the stack' })))
if ($Build) { Write-Note 'building images - the first build takes a while' }

& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host 'The stack did not start.' -ForegroundColor Red
    Write-Note 'docker compose logs --tail 50   shows why'
    exit $LASTEXITCODE
}

if ($Attach) { exit 0 }

# --- Where it is -------------------------------------------------------------
# Read back from .env rather than assuming the defaults, so the URLs printed are
# the ones that actually work on this machine.
function Get-EnvValue {
    param([string]$Name, [string]$Default)
    if (-not (Test-Path '.env')) { return $Default }
    $line = Select-String -Path '.env' -Pattern "^$Name=(.*)$" | Select-Object -First 1
    if (-not $line) { return $Default }
    $value = $line.Matches[0].Groups[1].Value.Trim()
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value
}

Write-Step 'Running'
& docker compose ps --format 'table {{.Service}}\t{{.Status}}'

if ($Service.Count -eq 0) {
    $webPort = Get-EnvValue -Name 'WEB_PORT' -Default '3000'
    $apiPort = Get-EnvValue -Name 'API_PORT' -Default '8000'
    $consolePort = Get-EnvValue -Name 'MINIO_CONSOLE_PORT' -Default '9001'
    $admin = Get-EnvValue -Name 'SEED_ADMIN_EMAIL' -Default 'admin@example.com'
    $password = Get-EnvValue -Name 'SEED_ADMIN_PASSWORD' -Default 'admin12345'

    Write-Host ''
    Write-Host 'Frontend       ' -NoNewline -ForegroundColor Green
    Write-Host "http://localhost:$webPort   $admin / $password"
    Write-Host 'API docs       ' -NoNewline -ForegroundColor Green
    Write-Host "http://localhost:$apiPort/docs"
    Write-Host 'MinIO console  ' -NoNewline -ForegroundColor Green
    Write-Host "http://localhost:$consolePort"
    Write-Host ''
    Write-Note 'docker compose logs -f api     follow one service'
    Write-Note './scripts/stop.ps1             stop it again'
}
