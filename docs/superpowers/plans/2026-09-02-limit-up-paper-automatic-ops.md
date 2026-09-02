# Limit-Up Paper Automatic Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permanently switch the production upper-limit V1 engine from `recommend_only` to `automatic` on the existing KIS paper account and verify safe operation.

**Architecture:** Preserve `/opt/maps/.env`, replace exactly one mode setting, and restart the `maps` systemd unit so the mode becomes persistent. Verify the account remains paper-only, the startup safety gate accepts automatic mode, and the live runtime stays healthy; restore the backup immediately if any success condition fails.

**Tech Stack:** PowerShell/OpenSSH client, Ubuntu systemd, Python 3.12, FastAPI health endpoint, journald, SQLAlchemy/PostgreSQL.

## Global Constraints

- Do not change `KIS_REAL_TRADING`; it must remain `false`.
- Do not change application code, database schema, Git HEAD, or any `.env` key except `MAPS_LIMIT_UP_MODE`.
- The target value is exactly `MAPS_LIMIT_UP_MODE=automatic`.
- Preserve a timestamped `.env` backup before mutation.
- If startup, health, safety-gate, or mode verification fails, restore the backup and restart once into `recommend_only`.
- Do not expose secrets or print the full `.env`.

---

### Task 1: Preflight the paper-account automatic switch

**Files:**
- Read: `/opt/maps/.env`
- Read: `/opt/maps/maps/limit_up/service.py`
- Read: PostgreSQL `limit_up_daily_guard`, `limit_up_session`, and `order_log`

**Interfaces:**
- Consumes: `automatic_mode_blocked_reason(settings) -> str | None`
- Produces: a preflight result proving one exact mode line, paper account, no automatic blocker, healthy service, and current guard/session state

- [ ] **Step 1: Verify server identity and service state**

Run through SSH:

```bash
date '+%Y-%m-%d %H:%M:%S %Z'
systemctl show maps -p ActiveState -p SubState -p MainPID -p NRestarts
cd /opt/maps
git rev-parse --short HEAD
```

Expected: KST server time, `ActiveState=active`, `SubState=running`, nonzero `MainPID`, and a readable Git HEAD.

- [ ] **Step 2: Verify the exact mutable setting without printing secrets**

Run:

```bash
cd /opt/maps
test "$(grep -c '^MAPS_LIMIT_UP_MODE=recommend_only$' .env)" -eq 1
test "$(grep -c '^MAPS_LIMIT_UP_MODE=' .env)" -eq 1
grep -E '^(MAPS_LIMIT_UP_ENABLED|MAPS_LIMIT_UP_MODE|MAPS_BROKER_MODE|KIS_REAL_TRADING|MAPS_LIVE_TRADING_ENABLED|MAPS_CONFIRM_REAL_TRADING)=' .env
```

Expected: one mode line set to `recommend_only`, limit-up enabled, broker `kis`, `KIS_REAL_TRADING=false`, and live trading enabled.

- [ ] **Step 3: Verify the runtime safety gate and persisted state**

Run `/opt/maps/.venv/bin/python` from `/opt/maps` with this read-only script:

```python
import datetime as dt
import json

from maps.common.db import SessionLocal
from maps.common.models import LimitUpDailyGuard, LimitUpSession, OrderLog
from maps.common.settings import get_settings
from maps.limit_up.service import automatic_mode_blocked_reason

day = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
start_utc = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(15, 0))
s = get_settings()
with SessionLocal() as db:
    guard = db.get(LimitUpDailyGuard, day)
    sessions = db.query(LimitUpSession).filter(LimitUpSession.ref_date == day).all()
    result = {
        "maps_broker_mode": s.maps_broker_mode,
        "kis_real_trading": s.kis_real_trading,
        "maps_live_trading_enabled": s.maps_live_trading_enabled,
        "maps_limit_up_enabled": s.maps_limit_up_enabled,
        "maps_limit_up_mode": s.maps_limit_up_mode,
        "automatic_mode_blocked_reason": automatic_mode_blocked_reason(s),
        "guard": None if guard is None else {
            "attempts": guard.attempts,
            "pattern_failures": guard.pattern_failures,
            "kosdaq_high": guard.kosdaq_high,
            "halted_reasons": guard.halted_reasons or [],
        },
        "sessions": [
            {"ticker": row.ticker, "state": row.state, "mode": row.execution_mode}
            for row in sessions
        ],
        "limit_up_order_count": db.query(OrderLog).filter(
            OrderLog.strategy_id.like("limit_up_v1%"),
            OrderLog.created_at >= start_utc,
        ).count(),
    }
    print(json.dumps(result, ensure_ascii=False, default=str))
```

Expected: `kis_real_trading=false`, mode `recommend_only`, blocker `None`, and no unresolved manual-lock evidence. Record the current sessions before restart so recovery can be checked afterward.

### Task 2: Back up the environment and apply the persistent mode

**Files:**
- Modify: `/opt/maps/.env`
- Create: `/opt/maps/.env.bak.limit_up_automatic_YYYYMMDD_HHMMSS_KST`

**Interfaces:**
- Consumes: successful Task 1 preflight
- Produces: exactly one persistent `MAPS_LIMIT_UP_MODE=automatic` line plus a recoverable backup path

- [ ] **Step 1: Create the verified backup and replace exactly one setting atomically**

In one SSH shell, set the backup path and run this standard-library script as `ubuntu`:

```bash
export BACKUP_PATH="/opt/maps/.env.bak.limit_up_automatic_$(date +%Y%m%d_%H%M%S_KST)"
cd /opt/maps
.venv/bin/python -
```

Pass the following program on standard input:

```python
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

env_path = Path("/opt/maps/.env").resolve(strict=True)
backup_path = Path(os.environ["BACKUP_PATH"])
old = b"MAPS_LIMIT_UP_MODE=recommend_only"
new = b"MAPS_LIMIT_UP_MODE=automatic"

before = env_path.read_bytes()
lines = before.splitlines(keepends=True)
mode_lines = [line.rstrip(b"\r\n") for line in lines if line.startswith(b"MAPS_LIMIT_UP_MODE=")]
if mode_lines != [old]:
    raise SystemExit(f"refusing unexpected mode lines: {mode_lines!r}")

shutil.copy2(env_path, backup_path)
backup = backup_path.read_bytes()
if hashlib.sha256(before).digest() != hashlib.sha256(backup).digest():
    raise SystemExit("backup hash mismatch")

replaced = []
for line in lines:
    ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
    body = line[: -len(ending)] if ending else line
    replaced.append(new + ending if body == old else line)
after = b"".join(replaced)

temp_path = env_path.with_name(f".{env_path.name}.limit-up-automatic.tmp")
temp_path.write_bytes(after)
os.chmod(temp_path, stat.S_IMODE(env_path.stat().st_mode))
os.replace(temp_path, env_path)

final = env_path.read_bytes()
before_plain = before.splitlines()
final_plain = final.splitlines()
changes = [
    [left.decode("utf-8"), right.decode("utf-8")]
    for left, right in zip(before_plain, final_plain, strict=True)
    if left != right
]
if changes != [[old.decode(), new.decode()]]:
    shutil.copy2(backup_path, env_path)
    raise SystemExit(f"unexpected env diff; backup restored: {changes!r}")

print(json.dumps({
    "backup_path": str(backup_path),
    "backup_hash_matches": True,
    "changes": changes,
}))
```

Expected output contains the backup path, `backup_hash_matches=true`, and exactly one changed pair from `recommend_only` to `automatic`.

- [ ] **Step 2: Re-read only the target line**

Run:

```bash
test "$(grep -c '^MAPS_LIMIT_UP_MODE=automatic$' /opt/maps/.env)" -eq 1
test "$(grep -c '^MAPS_LIMIT_UP_MODE=' /opt/maps/.env)" -eq 1
grep '^MAPS_LIMIT_UP_MODE=' /opt/maps/.env
```

Expected: one line, `MAPS_LIMIT_UP_MODE=automatic`.

### Task 3: Restart and verify automatic paper execution

**Files:**
- Read: systemd unit `maps`
- Read: journald unit logs
- Read: `/opt/maps/.env`
- Read: PostgreSQL upper-limit tables

**Interfaces:**
- Consumes: the backup path and updated `.env` from Task 2
- Produces: a healthy persistent automatic runtime, or a verified rollback to `recommend_only`

- [ ] **Step 1: Restart once and capture the restart boundary**

Run:

```bash
restart_at=$(date --iso-8601=seconds)
sudo systemctl restart maps
systemctl is-active maps
systemctl show maps -p MainPID -p NRestarts -p ActiveState -p SubState
```

Expected: `active`, a new nonzero PID, `ActiveState=active`, and `SubState=running`.

- [ ] **Step 2: Verify health and startup mode**

Run:

```bash
curl -fsS http://127.0.0.1:8000/health
sudo journalctl -u maps --since "$restart_at" --no-pager -o cat
```

Expected: health JSON with `status=ok` and exactly one startup line containing `상한가 V1 기동: mode=automatic`. There must be no `상한가 V1 기동 거부`, `상한가 V1 기동 실패`, traceback, control-loop failure, or deadman failure.

- [ ] **Step 3: Verify resolved non-secret settings and persisted guard**

Run the Task 1 read-only Python check again.

Expected: `maps_limit_up_mode=automatic`, `kis_real_trading=false`, blocker `None`, and the pre-restart guard/session state recovered without unsafe reset.

- [ ] **Step 4: Observe the first post-restart runtime window**

Capture one observation, wait 30 seconds, then capture a second observation:

```bash
for pass in 1 2; do
  echo "observation=$pass"
  date '+%Y-%m-%d %H:%M:%S %Z'
  systemctl is-active maps
  curl -fsS http://127.0.0.1:8000/health
  sudo journalctl -u maps --since "$restart_at" --no-pager -o cat \
    | grep -E -c 'Upper-limit control-loop iteration failed|deadman.*failed|고아 limit_up 보유로 수동 잠금|emergency OFF|EGW00201|Upper-limit WebSocket disconnected|feed_disconnected.*해제' || true
  test "$pass" -eq 2 || sleep 30
done
```

Re-run the exact Task 1 Step 3 Python query after the second observation to capture `limit_up_v1%` order rows. Expected: no control-loop/deadman/manual-lock/emergency events. WebSocket or rate-limit warnings must be reported with recovery status and must not be hidden.

- [ ] **Step 5: Roll back on any failed success condition**

If Steps 1-4 fail, use the `BACKUP_PATH` exported in Task 2 and run:

```bash
test -n "$BACKUP_PATH"
BACKUP_PATH="$BACKUP_PATH" python3 -c 'import os, shutil; shutil.copy2(os.environ["BACKUP_PATH"], "/opt/maps/.env")'
rollback_at=$(date --iso-8601=seconds)
sudo systemctl restart maps
test "$(grep -c '^MAPS_LIMIT_UP_MODE=recommend_only$' /opt/maps/.env)" -eq 1
systemctl is-active maps
curl -fsS http://127.0.0.1:8000/health
sudo journalctl -u maps --since "$rollback_at" --no-pager -o cat \
  | grep '상한가 V1 기동: mode=recommend_only'
```

Stop after the verified rollback. Do not attempt a second automatic switch.

- [ ] **Step 6: Report the operational result**

Report the KST switch time, backup path, final mode, paper-account confirmation, service PID/state, health result, guard/session/order state, any warnings, and whether rollback was required. Do not claim automatic operation unless all success criteria were freshly verified.
