param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
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
if (-not (Test-Path $BackupFile)) {
    throw "Backup file not found: $BackupFile"
}

pg_restore --clean --if-exists --no-owner --dbname=$env:MAPS_DB_URL $BackupFile

Write-Output "Restore completed from: $BackupFile"
