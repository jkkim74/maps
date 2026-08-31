# KIS KOSDAQ Index Interval Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the KIS KOSDAQ index request contract, prevent index polling outside the KRX regular session, and safely restore the production upper-limit engine in `recommend_only` mode.

**Architecture:** Keep the existing KIS endpoint and fail-fast parser. Represent the official one-minute interval as a module constant, add a pure regular-session gate beside the broader engine gate, and apply it only to the control loop's KOSDAQ request. Deploy with the engine off first, then enable `recommend_only` and roll back if runtime, status, DB, or order checks fail.

**Tech Stack:** Python 3.12, pytest, FastAPI, asyncio, SQLAlchemy/PostgreSQL, KIS Open API, systemd, PowerShell/OpenSSH.

## Global Constraints

- `MAPS_LIMIT_UP_MODE` remains `recommend_only`; do not enable `automatic`.
- KIS paper trading remains selected: `MAPS_BROKER_MODE=kis`, `KIS_REAL_TRADING=false`.
- Keep fail-fast empty-response behavior; do not add a fallback, cache, or alternate endpoint.
- Use `_INDEX_TIME_INTERVAL_SECONDS = "60"`; do not expose it as an environment setting.
- Keep the broad engine window at 08:50~15:40 for next-open and EOD actions.
- Restrict only KOSDAQ index polling to 09:00~15:30 on a KRX trading day.
- Do not model the observed 100-row response count as a stable protocol contract.
- Use TDD: each production change must be preceded by a test that fails for the expected reason.
- Preserve unrelated user changes under `docs/blog_series_backtest/`, `docs/diary/`, and `docs/stock/`.
- Do not deploy during 16:00~16:45 KST; verify `/tmp/maps_analyze.lock` is idle before each restart.
- If a post-enable safety check fails, restore the pre-enable `.env` backup, restart `maps`, and leave the engine OFF.

## File Map

- Modify `maps/execution/kis_adapter.py`: define and use the KIS one-minute index interval.
- Modify `tests/test_kis_adapter.py`: pin the outgoing interval parameter to `"60"`.
- Modify `maps/limit_up/runtime.py`: define the regular-session index gate and apply it to the control loop.
- Modify `tests/test_limit_up_runtime.py`: cover gate boundaries and ensure the control loop uses it.
- Modify `HANDOFF.md`: record the production result after successful observation.

---

### Task 1: Correct the KIS index interval contract

**Files:**
- Modify: `tests/test_kis_adapter.py:224-242`
- Modify: `maps/execution/kis_adapter.py:55-69,338-349`

**Interfaces:**
- Consumes: `KISAdapter._request(method, path, *, tr_id, params)` and `FakeSession.calls`.
- Produces: `_INDEX_TIME_INTERVAL_SECONDS: str = "60"`; `get_kosdaq_index() -> float` sends it as `FID_INPUT_HOUR_1`.

- [ ] **Step 1: Add the failing protocol assertion**

Add this assertion to `test_websocket_approval_and_kosdaq_index_use_official_contract` after the existing `FID_INPUT_ISCD` assertion:

```python
assert index["params"]["FID_INPUT_HOUR_1"] == "60"
```

- [ ] **Step 2: Run the focused test and verify RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q `
  '.\tests\test_kis_adapter.py::test_websocket_approval_and_kosdaq_index_use_official_contract'
```

Expected: FAIL because the actual value is a six-digit KST `HHMMSS` string rather than `"60"`.

- [ ] **Step 3: Add the protocol constant and use it**

Add beside the existing index path/TR-ID constants:

```python
_INDEX_TIME_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-timeprice"
_INDEX_TIME_INTERVAL_SECONDS = "60"
```

Replace the current-time expression inside `get_kosdaq_index()`:

```python
params={
    "FID_COND_MRKT_DIV_CODE": "U",
    "FID_INPUT_ISCD": "1001",
    "FID_INPUT_HOUR_1": _INDEX_TIME_INTERVAL_SECONDS,
},
```

- [ ] **Step 4: Run the adapter file and verify GREEN**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q '.\tests\test_kis_adapter.py'
```

Expected: all adapter tests PASS, including the interval assertion.

- [ ] **Step 5: Commit only the adapter change**

```powershell
git add -- maps/execution/kis_adapter.py tests/test_kis_adapter.py
git diff --cached --check
git commit -m "fix: use KIS index interval contract"
```

Expected: the commit contains exactly those two files.

---

### Task 2: Gate KOSDAQ polling to the regular session

**Files:**
- Modify: `tests/test_limit_up_runtime.py:5-21,102-122`
- Modify: `maps/limit_up/runtime.py:56-87,477-507`

**Interfaces:**
- Consumes: `engine_active_at(wall: datetime.datetime) -> bool` and `_control_loop`'s wall clock.
- Produces: `index_guard_active_at(wall: datetime.datetime) -> bool`; the KOSDAQ request passes through it.

- [ ] **Step 1: Add the failing boundary test**

Import `index_guard_active_at`, then add:

```python
def test_index_guard_runs_only_during_the_krx_regular_session() -> None:
    """Next-open/EOD work stays live outside the narrower index window."""
    assert not index_guard_active_at(dt.datetime(2026, 8, 28, 8, 59, 59, tzinfo=KST))
    assert index_guard_active_at(dt.datetime(2026, 8, 28, 9, 0, tzinfo=KST))
    assert index_guard_active_at(dt.datetime(2026, 8, 28, 15, 30, tzinfo=KST))
    assert not index_guard_active_at(dt.datetime(2026, 8, 28, 15, 30, 1, tzinfo=KST))
    assert not index_guard_active_at(dt.datetime(2026, 8, 29, 10, 0, tzinfo=KST))
```

- [ ] **Step 2: Run the boundary test and verify RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q `
  '.\tests\test_limit_up_runtime.py::test_index_guard_runs_only_during_the_krx_regular_session'
```

Expected: collection ERROR because `index_guard_active_at` does not exist.

- [ ] **Step 3: Implement the pure boundary function**

Add beside `_ENGINE_OPEN` and `_ENGINE_CLOSE`:

```python
_INDEX_GUARD_OPEN = dt.time(9, 0)
_INDEX_GUARD_CLOSE = dt.time(15, 30)
```

Add immediately after `engine_active_at`:

```python
def index_guard_active_at(wall: dt.datetime) -> bool:
    """Return whether KOSDAQ index polling is valid during the regular session."""
    clock = wall.time().replace(tzinfo=None)
    return engine_active_at(wall) and _INDEX_GUARD_OPEN <= clock <= _INDEX_GUARD_CLOSE
```

- [ ] **Step 4: Run the boundary test and verify GREEN**

Run the Step 2 command again. Expected: PASS at all weekday boundaries and on Saturday.

- [ ] **Step 5: Add the failing control-loop wiring guard**

Add `import inspect`, then add:

```python
def test_control_loop_uses_the_narrow_index_guard() -> None:
    """A correct helper is useless unless the broker call passes through it."""
    from maps.limit_up.runtime import KISIntradayRuntime

    source = inspect.getsource(KISIntradayRuntime._control_loop)

    assert "if index_guard_active_at(wall) and" in source
```

- [ ] **Step 6: Run the wiring guard and verify RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q `
  '.\tests\test_limit_up_runtime.py::test_control_loop_uses_the_narrow_index_guard'
```

Expected: FAIL because `_control_loop` checks only the monotonic interval.

- [ ] **Step 7: Apply the gate to the broker call**

```python
if index_guard_active_at(wall) and now_mono - self._last_index_at >= 1.0:
    self._last_index_at = now_mono
    value = await asyncio.to_thread(self.adapter.get_kosdaq_index)
```

Do not change the broad engine gate, scan schedule, daily actions, or fallback behavior.

- [ ] **Step 8: Run the runtime file and verify GREEN**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q '.\tests\test_limit_up_runtime.py'
```

Expected: all runtime tests PASS, including boundary and wiring tests.

- [ ] **Step 9: Commit only the runtime gate change**

```powershell
git add -- maps/limit_up/runtime.py tests/test_limit_up_runtime.py
git diff --cached --check
git commit -m "fix: gate KIS index polling to market hours"
```

Expected: the commit contains exactly those two files.

---

### Task 3: Verify, deploy with the engine off, then enable `recommend_only`

**Files:**
- Verify: all Python source and tests.
- Modify on production: `/opt/maps/.env` only after fixed code is deployed and healthy.

**Interfaces:**
- Consumes: the two implementation commits and production PostgreSQL revision `0032_limit_up_ledger`.
- Produces: runtime `mode=recommend_only`, `manual_lock=false`, `unknown_positions=[]`, with zero `limit_up_v1` orders.

- [ ] **Step 1: Run focused upper-limit verification**

```powershell
$limitUpTests = Get-ChildItem '.\tests\test_limit_up_*.py' |
    Select-Object -ExpandProperty FullName
& '.\.venv\Scripts\python.exe' -m pytest -q `
    $limitUpTests '.\tests\test_kis_adapter.py' '.\tests\test_migrations.py'
& '.\.venv\Scripts\python.exe' -m pytest -q `
    '.\tests\test_scheduler.py' -k 'after_hours_watch'
```

Expected: every selected test passes.

- [ ] **Step 2: Run full regression and static checks**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q
& '.\.venv\Scripts\python.exe' -m compileall -q maps
git diff --check
```

Expected: full suite PASS, `compileall` exit 0, and no whitespace errors.

- [ ] **Step 3: Verify commits and push `master`**

```powershell
git log -5 --oneline --decorate
git status -sb
git push origin master
```

Expected: push succeeds without force; unrelated user changes remain unstaged.

- [ ] **Step 4: Deploy code while the engine remains OFF**

Run through SSH after confirming the current KST is outside 16:00~16:45:

```bash
set -euo pipefail
cd /opt/maps
flock -n /tmp/maps_analyze.lock true
grep -qx 'MAPS_LIMIT_UP_ENABLED=false' .env
git pull --ff-only origin master
.venv/bin/python -m alembic current
sudo systemctl restart maps
for i in 1 2 3 4 5 6 7 8 9 10; do
  curl -fsS http://127.0.0.1:8000/health && break
  sleep 1
done
systemctl is-active maps
```

Expected: Alembic `0032_limit_up_ledger (head)`, health OK, service active.

- [ ] **Step 5: Back up `.env`, enable only the engine flag, and restart**

```bash
set -euo pipefail
cd /opt/maps
flock -n /tmp/maps_analyze.lock true
grep -qx 'MAPS_LIMIT_UP_MODE=recommend_only' .env
grep -qx 'MAPS_LIMIT_UP_ENABLED=false' .env
stamp=$(TZ=Asia/Seoul date '+%Y%m%d_%H%M%S')
backup=".env.bak.limit_up_recommend_${stamp}"
cp -p .env "$backup"
sed -i 's/^MAPS_LIMIT_UP_ENABLED=false$/MAPS_LIMIT_UP_ENABLED=true/' .env
chmod --reference="$backup" .env
chown --reference="$backup" .env
grep -qx 'MAPS_LIMIT_UP_ENABLED=true' .env
echo "$backup" > /tmp/maps_limit_up_env_backup
sudo systemctl restart maps
for i in 1 2 3 4 5 6 7 8 9 10; do
  curl -fsS http://127.0.0.1:8000/health && break
  sleep 1
done
systemctl is-active maps
```

Expected: exactly one flag changes, `.env` keeps owner/mode, health OK, service active.

- [ ] **Step 6: Read authenticated runtime status without exposing credentials**

```bash
cd /opt/maps
.venv/bin/python - <<'PY'
import requests
from maps.common.settings import get_settings

settings = get_settings()
login = requests.post(
    "http://127.0.0.1:8000/api/v1/mobile/login",
    json={"username": settings.maps_auth_username, "password": settings.maps_auth_password},
    timeout=10,
)
login.raise_for_status()
token = login.json()["token"]
status = requests.get(
    "http://127.0.0.1:8000/api/v1/limit-up/status",
    headers={"Authorization": f"Bearer {token}"},
    timeout=10,
)
status.raise_for_status()
payload = status.json()
print({
    "mode": payload["mode"],
    "manual_lock": payload["manual_lock"],
    "unknown_positions": payload["unknown_positions"],
    "entry_halted": payload["entry_halted"],
    "halted_reasons": payload["halted_reasons"],
    "session_count": len(payload["sessions"]),
})
PY
```

Expected: `recommend_only`, `manual_lock=false`, `unknown_positions=[]`. Record any legitimate daily guard reason instead of clearing it.

- [ ] **Step 7: Observe for 30 seconds and query order/DB safety**

After a 30-second observation interval, run:

```bash
cd /opt/maps
sudo journalctl -u maps --since '-2 minutes' --no-pager | \
  grep -E 'KIS KOSDAQ index response was empty|Upper-limit control-loop iteration failed|EGW00201|상한가 V1 기동' || true
sudo -u postgres psql -d maps -X -A -F '|' -P pager=off -v ON_ERROR_STOP=1 <<'SQL'
BEGIN TRANSACTION READ ONLY;
SELECT count(*) AS all_limit_up_orders
FROM order_log
WHERE strategy_id LIKE 'limit_up_v1:%';
SELECT state, execution_mode, count(*)
FROM limit_up_session
GROUP BY state, execution_mode
ORDER BY state, execution_mode;
SELECT
  (SELECT count(*) FROM limit_up_order_leg l LEFT JOIN limit_up_session s ON s.id=l.session_id WHERE s.id IS NULL) AS orphan_legs,
  (SELECT count(*) FROM limit_up_event e LEFT JOIN limit_up_session s ON s.id=e.session_id WHERE s.id IS NULL) AS orphan_events,
  (SELECT count(*) FROM limit_up_tape t LEFT JOIN limit_up_session s ON s.id=t.session_id WHERE s.id IS NULL) AS orphan_tapes;
COMMIT;
SQL
```

Expected: no empty-index/control-loop/rate-limit error; zero orders and zero orphan rows.

- [ ] **Step 8: Verify external health and production commit**

```bash
cd /opt/maps
git rev-parse --short HEAD
systemctl is-active maps
curl -fsS http://127.0.0.1:8000/health
curl -sS -o /dev/null -w '%{http_code}\n' https://maps.magable.kr/
```

Expected: production HEAD matches local `master`, service active, internal health OK, external authenticated redirect `303`.

- [ ] **Step 9: Roll back immediately if a safety condition fails**

Execute only when Steps 5-8 fail:

```bash
set -euo pipefail
cd /opt/maps
backup=$(cat /tmp/maps_limit_up_env_backup)
test -f "$backup"
cp -p "$backup" .env
grep -qx 'MAPS_LIMIT_UP_ENABLED=false' .env
sudo systemctl restart maps
for i in 1 2 3 4 5 6 7 8 9 10; do
  curl -fsS http://127.0.0.1:8000/health && break
  sleep 1
done
systemctl is-active maps
```

Expected: engine OFF, health OK, service active. Preserve logs and stop; do not attempt a second production fix in this deploy step.

---

### Task 4: Record the verified operating state

**Files:**
- Modify: `HANDOFF.md:1`

**Interfaces:**
- Consumes: actual commit hashes, test counts, backup path, runtime status, DB/order query, and health results from Task 3.
- Produces: a top HANDOFF section that supersedes the prior engine-OFF record.

- [ ] **Step 1: Prepend the observed result**

Record the following fields with the exact outputs captured in Task 3: root cause, interval constant, 09:00~15:30 gate, focused/full test counts, deployed HEAD, Alembic revision, service and health results, runtime status, order/orphan counts, `.env` backup path, and the fact that `automatic` remains unapproved. Do not record expected values as observed results.

- [ ] **Step 2: Verify and commit only HANDOFF**

```powershell
git diff --check -- HANDOFF.md
git add -- HANDOFF.md
git diff --cached --check
git commit -m "docs: hand off limit-up recommend-only restart"
git push origin master
```

Expected: only `HANDOFF.md` is committed; unrelated local changes remain untouched.

- [ ] **Step 3: Fast-forward production docs without restarting**

```bash
cd /opt/maps
git pull --ff-only origin master
git rev-parse --short HEAD
systemctl is-active maps
```

Expected: production HEAD matches `origin/master`; service remains active and is not restarted for documentation only.
