# MAPS AWS Lightsail Deployment Runbook

## 1. Scope

This runbook documents the AWS Lightsail deployment path for MAPS and the
daily operations commands for restart, health checks, logs, and updates.

Target runtime:

- AWS Lightsail Ubuntu 24.04 LTS
- MAPS FastAPI app served by `uvicorn`
- `systemd` service name: `maps`
- `nginx` reverse proxy on port `80`
- PostgreSQL local database
- Application process bound to `127.0.0.1:8000`

Keep real trading disabled during first cloud validation:

```env
MAPS_LIVE_TRADING_ENABLED=false
KIS_REAL_TRADING=false
```

Enable real trading only after paper trading, order/cancel tests, scheduler
dry-runs, alerts, and backup restore tests pass.

## 2. Lightsail Instance

Recommended instance:

- Platform: Linux/Unix
- Blueprint: Ubuntu 24.04 LTS
- Plan: 2 GB RAM / 2 vCPU / 60 GB SSD
- Instance name: `maps-prod`
- Static IP: create and attach to the instance

Firewall baseline:

```text
SSH    TCP 22   your-public-ip/32
HTTP   TCP 80   Any IPv4 address
HTTPS  TCP 443  Any IPv4 address
```

Do not open port `8000` to the internet. `uvicorn` should listen only on
`127.0.0.1:8000`, and `nginx` should proxy public traffic to it.

If IPv6 is not needed, disable IPv6 networking to keep the exposure surface
simple.

## 3. Base Server Setup

Connect to the instance with Lightsail browser SSH, then install packages:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3 python3-venv python3-pip postgresql postgresql-contrib nginx
```

Clone the source:

```bash
cd /opt
sudo git clone https://github.com/jkkim74/maps.git
sudo chown -R ubuntu:ubuntu /opt/maps
cd /opt/maps
```

Create Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 4. PostgreSQL Setup

Create database and user:

```bash
sudo -u postgres psql
```

Inside `psql`:

```sql
CREATE USER maps_app WITH PASSWORD 'change-this-password';
CREATE DATABASE maps OWNER maps_app;
GRANT ALL PRIVILEGES ON DATABASE maps TO maps_app;
\q
```

Apply migrations:

```bash
cd /opt/maps
source .venv/bin/activate
alembic upgrade head
```

## 5. Production `.env`

Create the server-side `.env`:

```bash
cd /opt/maps
nano .env
```

Production baseline:

```env
MAPS_ENV=production
MAPS_LOG_LEVEL=INFO
MAPS_LOG_DIR=logs
MAPS_LOG_FILE=maps.log
MAPS_DB_URL=postgresql+psycopg2://maps_app:change-this-password@127.0.0.1:5432/maps

MAPS_BROKER_MODE=kis
MAPS_LIVE_TRADING_ENABLED=false
MAPS_DATA_PROVIDER=pykrx
MAPS_SCHEDULER_ENABLED=true
MAPS_SCHEDULER_TIMEZONE=Asia/Seoul

MAPS_DATA_COLLECTION_TIME=16:10
MAPS_CANDIDATE_TIME=16:20
MAPS_VALIDATION_TIME=16:40
MAPS_ORDER_TIME=08:55
MAPS_EOD_TIME=15:35
MAPS_BROKER_SYNC_INTERVAL_SECONDS=60

KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_ACCOUNT_NO=...
KIS_REAL_TRADING=false
KIS_REAL_BASE_URL=https://openapi.koreainvestment.com:9443
KIS_PAPER_BASE_URL=https://openapivts.koreainvestment.com:29443

DART_API_KEY=...
SLACK_WEBHOOK_URL=
```

Never commit `.env`. If any API key, account number, or password has been
shown in a shared screen or chat, rotate it before production trading.

## 6. Manual App Test

Run the app directly before registering the service:

```bash
cd /opt/maps
source .venv/bin/activate
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open a second SSH terminal and check:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/ops/config
curl http://127.0.0.1:8000/api/v1/ops/scheduler
```

Stop the manual process with `Ctrl+C`.

## 7. `systemd` Service

Create service file:

```bash
sudo nano /etc/systemd/system/maps.service
```

Content:

```ini
[Unit]
Description=MAPS Trading Server
After=network.target postgresql.service

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/maps
ExecStart=/opt/maps/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable maps
sudo systemctl start maps
sudo systemctl status maps --no-pager
```

Expected status:

```text
Active: active (running)
```

If `systemctl start maps` is run without `sudo`, Lightsail may ask for a
password and fail. Use `sudo systemctl start maps`.

## 8. `nginx` Reverse Proxy

Create site config:

```bash
sudo nano /etc/nginx/sites-available/maps
```

Content:

```nginx
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Enable:

```bash
sudo ln -s /etc/nginx/sites-available/maps /etc/nginx/sites-enabled/maps
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

External access:

```text
http://<lightsail-static-ip>
```

## 9. Daily Operations

Check service:

```bash
sudo systemctl status maps --no-pager
```

Restart service:

```bash
sudo systemctl restart maps
sudo systemctl status maps --no-pager
```

Stop service:

```bash
sudo systemctl stop maps
```

Start service:

```bash
sudo systemctl start maps
```

Follow service logs:

```bash
sudo journalctl -u maps -f
```

Show recent service logs:

```bash
sudo journalctl -u maps -n 100 --no-pager
```

Follow application log:

```bash
tail -f /opt/maps/logs/maps.log
```

Health checks:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/ops/config
curl http://127.0.0.1:8000/api/v1/ops/scheduler
```

Check public web path from the server:

```bash
curl -I http://127.0.0.1/
```

Check listening ports:

```bash
sudo ss -ltnp | grep -E ':80|:8000'
```

Expected:

- `nginx` listening on `0.0.0.0:80`
- `uvicorn` listening on `127.0.0.1:8000`

## 10. Source Update Deployment

Use this flow when deploying the latest GitHub source:

```bash
cd /opt/maps
git status --short --branch
git pull
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
sudo systemctl restart maps
sudo systemctl status maps --no-pager
curl http://127.0.0.1:8000/health
```

If local untracked files block `git pull`, inspect them first. Do not delete
production `.env`, logs, or backups.

## 11. Troubleshooting

Service failed to start:

```bash
sudo systemctl status maps --no-pager
sudo journalctl -u maps -n 120 --no-pager
```

App is running but public page does not open:

```bash
sudo systemctl status nginx --no-pager
sudo nginx -t
curl -I http://127.0.0.1:8000/health
curl -I http://127.0.0.1/
```

Database connection issue:

```bash
sudo systemctl status postgresql --no-pager
psql "postgresql://maps_app:change-this-password@127.0.0.1:5432/maps" -c "select now();"
```

Scheduler must run in only one process. Do not run a second `uvicorn` instance
with `MAPS_SCHEDULER_ENABLED=true`.

## 12. Backup Reminder

For production-like use, run at least one daily PostgreSQL backup after market
close and periodically test restore into a separate database. See
`docs/DEPLOYMENT_POSTGRESQL.md` for the project backup/restore commands.

