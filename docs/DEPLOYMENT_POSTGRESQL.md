# MAPS PostgreSQL Deployment Runbook

## 1. Scope

This runbook covers production-style deployment settings for:

- PostgreSQL database configuration
- `.env` values
- logs and rotation
- backups and restore
- restart policy

Keep live trading disabled until KIS paper trading, scheduler dry-runs, Slack alerts, and backup restore checks pass.

## 2. PostgreSQL Setup

Create a dedicated database and user:

```sql
CREATE USER maps_app WITH PASSWORD 'change-me';
CREATE DATABASE maps OWNER maps_app;
GRANT ALL PRIVILEGES ON DATABASE maps TO maps_app;
```

Recommended connection URL:

```env
MAPS_DB_URL=postgresql+psycopg2://maps_app:change-me@127.0.0.1:5432/maps
```

Install dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Apply migrations:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

## 3. Production `.env`

Minimum production-like baseline:

```env
MAPS_ENV=production
MAPS_LOG_LEVEL=INFO
MAPS_LOG_DIR=logs
MAPS_LOG_FILE=maps.log
MAPS_LOG_MAX_BYTES=10485760
MAPS_LOG_BACKUP_COUNT=10
MAPS_DB_URL=postgresql+psycopg2://maps_app:change-me@127.0.0.1:5432/maps

MAPS_BROKER_MODE=kis
MAPS_LIVE_TRADING_ENABLED=false
MAPS_DATA_PROVIDER=pykrx
MAPS_SCHEDULER_ENABLED=true
MAPS_SCHEDULER_TIMEZONE=Asia/Seoul
MAPS_BROKER_SYNC_INTERVAL_SECONDS=60

KIS_REAL_TRADING=false
SLACK_WEBHOOK_URL=
```

Live trading requires both:

```env
MAPS_LIVE_TRADING_ENABLED=true
KIS_REAL_TRADING=true
```

Do not enable both until a paper order/cancel test and backup restore test are complete.

## 4. Logs

MAPS writes logs to:

```text
logs/maps.log
logs/maps.log.1
...
```

Default rotation:

- 10 MB per file
- 10 retained backups

Operational checks:

```powershell
Get-Content .\logs\maps.log -Tail 100
Select-String -Path .\logs\maps.log -Pattern "ERROR|CRITICAL|Kill Switch"
```

## 5. Backups

Create a PostgreSQL custom-format backup:

```powershell
.\scripts\backup_postgres.ps1 -BackupDir .\backups
```

Restore into the database in `MAPS_DB_URL`:

```powershell
.\scripts\restore_postgres.ps1 -BackupFile .\backups\maps_YYYYMMDD_HHMMSS.dump
```

Backup policy:

- Run at least once daily after market close.
- Keep 7 daily backups and 4 weekly backups.
- Test restore monthly into a separate database.
- Store one encrypted copy outside the trading host.

Windows Task Scheduler example:

```powershell
powershell.exe -ExecutionPolicy Bypass -File D:\workspace2\maps\maps\scripts\backup_postgres.ps1 -BackupDir D:\maps_backups
```

## 6. Restart Policy

For Windows service hosting, use NSSM or Task Scheduler.

Recommended command:

```powershell
D:\workspace2\maps\maps\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Restart rules:

- Restart on process exit.
- Delay restart by 10 seconds.
- Redirect stdout/stderr to `logs/service_stdout.log` and `logs/service_stderr.log`.
- Keep `MAPS_SCHEDULER_ENABLED=true` only for one process. Do not run two API processes with scheduler enabled.

Manual restart:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object OwningProcess
Stop-Process -Id <PID>
Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList @("-m","uvicorn","main:app","--host","127.0.0.1","--port","8000") -WorkingDirectory "D:\workspace2\maps\maps" -WindowStyle Hidden
```

## 7. Health Checks

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ops/config
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ops/scheduler
```

Expected:

- `/health` returns `status=ok`
- `/ops/config` has `ready=true` or only intentional warnings
- scheduler has exactly one running owner process

## 8. Pre-Live Checklist

- PostgreSQL migration applied
- Backup created and restored into test DB
- Slack alert test sent
- KIS paper balance/open-order sync works
- KIS paper order and cancel tested with minimal quantity
- `MAPS_LIVE_TRADING_ENABLED=false` until final approval
- Scheduler enabled in only one process
