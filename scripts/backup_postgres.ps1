param(
    [string]$BackupDir = "backups",
    [string]$EnvFile = ".env"
)

$ErrorActionPreference = "Stop"

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
        $name, $value = $_ -split '=', 2
        if ($name) {
            [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
        }
    }
}

if (-not $env:MAPS_DB_URL) {
    throw "MAPS_DB_URL is not set."
}

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$target = Join-Path $BackupDir "maps_$timestamp.dump"

pg_dump --format=custom --file=$target $env:MAPS_DB_URL

Write-Output "Backup created: $target"
