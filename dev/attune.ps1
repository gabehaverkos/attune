# Run from any directory: & <repo>/dev/attune.ps1 dev [start|stop|status|logs|down|wipe]
[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('dev')]
    [string]$Command,

    [Parameter(Position = 1)]
    [ValidateSet('start', 'stop', 'status', 'logs', 'down', 'wipe')]
    [string]$Action = 'start'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$envFile = Join-Path $repoRoot '.env'
if (-not (Test-Path $envFile -PathType Leaf)) {
    throw 'Create .env from .env.example and fill in its settings. See README.md.'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI is required. Install and start Docker Desktop first.'
}

# Explicit paths make the command independent of the current directory.
# Relative paths inside the Compose files resolve against the repository root.
# Keep the app's existing Compose project naming (including shell overrides).
$composeArgs = @(
    'compose', '--project-directory', $repoRoot,
    '--env-file', $envFile,
    '-f', (Join-Path $repoRoot 'docker-compose.yml'),
    '-f', (Join-Path $PSScriptRoot 'docker-compose.dev.yml')
)
$projectArgs = @($composeArgs)
# A wipe keeps Caddy's certificate, so the browser that accepted it once is not
# asked again after every wipe.
$preservedVolumes = @('caddy_data', 'caddy_config')
switch ($Action) {
    'start'  { $composeArgs += @('up', '-d', '--build') }
    'stop'   { $composeArgs += @('stop') }
    'status' { $composeArgs += @('ps', '-a') }
    'logs'   { $composeArgs += @('logs', '--follow', '--tail', '100') }
    # Remove containers and networks; named volumes (all data) survive. Containers
    # of services no longer in the Compose files go too, or they hold on to the
    # network and their volumes.
    'down'   { $composeArgs += @('down', '--remove-orphans') }
    # The named volumes go too, below, destroying the stack's data.
    'wipe'   { $composeArgs += @('down', '--remove-orphans') }
}
& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    throw "$Command $Action failed (Docker exit code $LASTEXITCODE)."
}
if ($Action -eq 'wipe') {
    $project = (& docker @projectArgs config --format json | ConvertFrom-Json).name
    if ($LASTEXITCODE -ne 0 -or -not $project) {
        throw 'dev wipe could not resolve the Compose project name.'
    }
    # Windows PowerShell strips quotes inside native arguments, so read the whole
    # label string rather than a {{.Label "..."}} template.
    # Always an array: a single name would otherwise be a string, and splatting a
    # string passes it to docker one character at a time.
    $volumes = @(docker volume ls --filter "label=com.docker.compose.project=$project" --format '{{.Name}}|{{.Labels}}' |
        ForEach-Object {
            $name, $labels = $_ -split '\|', 2
            $key = if ($labels -match '(?:^|,)com\.docker\.compose\.volume=([^,]+)') { $Matches[1] }
            if ($key -notin $preservedVolumes) { $name }
        })
    if ($volumes) {
        & docker volume rm @volumes
        if ($LASTEXITCODE -ne 0) {
            throw "dev wipe failed to remove volumes (Docker exit code $LASTEXITCODE)."
        }
    }
    Write-Host "Kept $($preservedVolumes -join ', '). Remove them with: docker volume rm $(($preservedVolumes | ForEach-Object { "${project}_$_" }) -join ' ')"
}
if ($Action -eq 'start') {
    Write-Host 'Attune is starting at https://localhost. The first time, your browser asks you to accept its certificate.'
}
