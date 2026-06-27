# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MAPS (Market-Adaptive Profit Management System) is a validation-first Korean stock auto-trading platform. The core philosophy is **blocking bad strategies from reaching live accounts**, not running promising-looking strategies quickly.

## Commands

### Run the API server
```powershell
# From d:\workspace\maps\maps\
uvicorn main:app --reload
# or on port 8001:
uvicorn main:app --reload --port 8001
```

### Run tests
```powershell
# All tests
pytest

# Single test file
pytest tests/test_walk_forward.py

# Single test function
pytest tests/test_walk_forward.py::test_function_name -v

# With coverage
pytest --tb=short
```

### Database migrations (Alembic)
```powershell
# Apply all pending migrations
alembic upgrade head

# Create a new migration (auto-detect)
alembic revision --autogenerate -m "description"

# Downgrade one step
alembic downgrade -1
```

### Mobile app (Capacitor/Vite)
```powershell
cd apps/mobile
npm install
npm run dev        # dev server, proxies /api to localhost:8000
npm run cap:sync   # sync web build to native project
```

### Environment setup
```powershell
Copy-Item .env.example .env
# Edit .env with your values
# Virtual env is at .venv\ — activate with .venv\Scripts\Activate.ps1
```

## Architecture

The application is a FastAPI server (`main.py`) with a Jinja2 web dashboard and a separate Capacitor mobile client (`apps/mobile/`).

### Module layout (`maps/`)

| Package | Responsibility |
|---|---|
| `common/` | `settings.py` (pydantic-settings), `db.py` (SQLAlchemy engine + session), `models.py` (all ORM tables), `constants.py`, `exceptions.py` |
| `data/` | `collector.py` orchestrates collection; `krx_adapter.py` / `MockKRXAdapter` fetch OHLCV; `ohlcv_repo.py`, `security_repo.py` are DB repos |
| `data_quality/` | `universe_filter.py` — as-of-date universe generator. `generate(ref_date)` only uses information available on that date |
| `indicator/` | `trend_strength.py` — computes 0-100 TrendStrength score (MA20 position 40%, RSI14 30%, volume ratio 30%) and classifies into S1–S5 buckets; feeds `CandidateSnapshot.ts_bucket` |
| `strategy/` | `base.py` abstract; `pullback_v3.py`, `ath_breakout_v1/v2.py`, `donchian_v1/v2.py`, `multi_asset_trend_v1.py` concrete strategies; `live_rules.py` per-strategy stop-loss percentages |
| `backtest/` | `engine.py` + `cost_model.py` — runs strategy backtests |
| `validation/` | `walk_forward.py`, `plateau.py`, `monte_carlo.py` — validation suite |
| `promotion/` | `gate.py` — decides strategy stage advancement and writes audit log |
| `execution/` | `broker_adapter.py` (abstract), `mock_broker.py`, `kis_adapter.py`, `kiwoom_adapter.py`, `order_manager.py` |
| `risk/` | `manager.py` — kill switch + exposure limits |
| `market/` | `regime.py` (market regime: strong/mixed/weak + weekly trend), `trading_rules.py` (KRX rules) |
| `ops/` | `scheduler.py` (APScheduler jobs), `notifications.py` (Slack), `order_state.py`, `order_preview.py` |
| `stock_report/` | `runner.py` — integrates external stock-report tool (`MAPS_STOCK_REPORT_PATH`); stores HTML results in `stock_report_runs` table |
| `api/` | One router per screen; `deps.py` provides `get_db()`; `schemas.py` holds Pydantic response models |
| `dashboard/` | `strategy_compare.py` |

### Daily scheduled pipeline (KST)

`16:10` collect OHLCV → `16:20` generate candidates → `16:40` run validation → `08:55` (next morning) place orders → `15:35` EOD sync

Enabled by `MAPS_SCHEDULER_ENABLED=true`. Off by default.

### Promotion stages

`research` → `alert_only` → `mock_candidate` → `live_candidate` → `live` (or `rejected`)

`PromotionGate` in `promotion/gate.py` evaluates all three validation checks and writes to `promotion_history`. Unknown strategy IDs or missing metrics must always result in `fail with reason` (never `KeyError`).

Per-stage gate conditions (all AND):

| Stage (current → next) | Min score | WFA | MC limit | Plateau | Sharpe | Other |
|---|---|---|---|---|---|---|
| research → alert_only | 0 | no | no | — | — | — |
| alert_only → mock_candidate | 60 | no | no | — | ≥ 0 | — |
| mock_candidate → live_candidate | 60 | yes | yes | C+ | ≥ 0.3 | 3 months mock or replay |
| live_candidate → live | 75 | yes | yes | B+ | ≥ 0.5 | 3 months mock or replay |

### Strategy-to-group mapping (`STRATEGY_GROUP_MAP`)

| Strategy ID | Group |
|---|---|
| `pullback_v3`, `pullback_v2` | `pullback_short` |
| `ath_breakout_v1`, `ath_breakout_v2` | `ath_outlier` |
| `multi_asset_trend_v1` | `multi_asset` |
| `donchian_v1`, `donchian_v2` | `donchian_research` |

This mapping drives MC MDD limit lookups in `PromotionGate`. Adding a new strategy ID requires updating `STRATEGY_GROUP_MAP` in `common/constants.py`.

### Database

SQLite by default (`maps.db`). Switch to PostgreSQL via `MAPS_DB_URL`. All tables are created at startup via `Base.metadata.create_all()`. Schema changes go through Alembic (`alembic/versions/`). All audit log tables (`promotion_history`, `universe_quality_log`, `order_log`, `kill_switch_log`) must exist from day 1.

## Critical design constraints

1. **DataQualityFilter is an as-of-date generator.** `generate(ref_date)` must never reference data after `ref_date` (no future delisting/halt info leakage).

2. **WalkForward pass requires all 3 conditions (AND):**
   - `sharpe_mean > 0`
   - negative folds ≤ 1
   - OOS/IS G2P ≥ 0.6
   The `std/|mean| ≤ 0.5` condition was removed (high-return/high-volatility strategies were unfairly rejected; MDD/MC already controls real risk).

3. **PromotionGate must never die with KeyError.** Unknown strategy ID or missing metric → `fail with reason`. Exception: `MOCK_CANDIDATE`+ stage with a strategy ID absent from `STRATEGY_GROUP_MAP` raises `UnknownStrategyError`.

4. **BrokerAdapter is abstract.** Only `MockBroker` is used through Phase 4. Real broker adapters (`KISAdapter`, `KiwoomAdapter`) are Phase 5 only.

5. **Kill Switch** auto-blocks new entries only. Liquidating existing positions requires explicit user approval.

6. **Live trading safety gate:** `MAPS_LIVE_TRADING_ENABLED=false` must be explicitly changed. `MAPS_BROKER_MODE=mock` disables real orders.

## Allowed MDD by strategy group

| Strategy | mc_p95_limit |
|---|---|
| pullback_short | 18% |
| ath_outlier | 35% |
| multi_asset | 22% |
| donchian_research | 30% |
| portfolio_total | 28% |

## Tradeability weights (balanced preset)

`robustness=0.30, risk=0.30, recovery=0.20, return=0.20`

Other presets: `conservative` (robustness 0.40, risk 0.35) and `growth` (return 0.35, robustness 0.20). All presets are in `common/constants.py:WEIGHT_PRESETS`.

Promotion thresholds: `mock_candidate=60`, `live_candidate=75` (fixed, independent of weights).

## Key env vars for testing / overrides

| Var | Default | Purpose |
|---|---|---|
| `MAPS_MARKET_REGIME_OVERRIDE` | `auto` | Force `strong`, `mixed`, or `weak` — bypasses live pykrx/yfinance analysis |
| `MAPS_WEEKLY_TREND_OVERRIDE` | `auto` | Force `pass` or `fail` — bypasses live MA calculation |
| `MAPS_CANDIDATE_MIN_SCORE` | `10.0` | Skip order for candidates with `final_score` below this |
| `MAPS_ORDER_SLIPPAGE_PCT` | `0.01` | Limit price = last close × (1 + slippage) |
| `MAPS_ORDER_MAX_GAP_PCT` | `0.02` | Cancel order if gap-up from signal price exceeds this |
| `MAPS_STOCK_REPORT_PATH` | `/opt/stock_report` | Path to external stock-report source tree |

## Production Server

| Item | Value |
|---|---|
| Provider | AWS Lightsail (ap-northeast-2 / Seoul) |
| Public IP | `3.37.117.246` |
| Domain / URL | `https://magable.kr` (also `www.magable.kr`) |
| SSH user | `ubuntu` |
| SSH key | `D:\maps\LightsailDefaultKey-ap-northeast-2.pem` |
| App root | `/opt/maps` |
| Service | `maps` (systemd) |

### Reverse proxy & HTTPS

Public traffic terminates at **nginx**, which reverse-proxies to uvicorn on
`127.0.0.1:8000`. HTTPS uses a **Let's Encrypt** cert (certbot, auto-renew via
`certbot.timer`). HTTP (80) 301-redirects to HTTPS (443). HSTS header set on 443.

- nginx site: `/etc/nginx/sites-enabled/maps` (server_name `magable.kr www.magable.kr`)
- cert: `/etc/letsencrypt/live/magable.kr/` — renew test: `sudo certbot renew --dry-run`
- Lightsail firewall must keep **80 + 443** open (80 = ACME challenge + redirect).
- Cert/domain depends on a Lightsail **static IP** and a DNS A record → `3.37.117.246`.

### Authentication (login)

The dashboard is behind a single-shared-password login (`maps/api/auth.py`,
session-cookie gate in `main.py`). Configured **only in the server `/opt/maps/.env`**
(never commit these). Default username `admin`. Relevant vars:

| Var | Purpose |
|---|---|
| `MAPS_AUTH_ENABLED` | `true` on prod; `false` (default) elsewhere. Off → no auth |
| `MAPS_AUTH_USERNAME` / `MAPS_AUTH_PASSWORD` | login credentials |
| `MAPS_SESSION_SECRET_KEY` | session-cookie signing key (random secret) |
| `MAPS_SESSION_HTTPS_ONLY` | `true` on prod → cookie `Secure` (HTTPS only) |

Tests force auth off via an autouse fixture (`tests/conftest.py`), so the server
`.env` enabling auth never breaks the suite. To change the password: edit
`/opt/maps/.env` `MAPS_AUTH_PASSWORD` then `sudo systemctl restart maps`.

> **Deploy note:** when `requirements.txt` changes (e.g. `itsdangerous` for auth),
> the deploy must run `pip install -r requirements.txt` before restart.

### SSH access

```powershell
ssh -i "D:\maps\LightsailDefaultKey-ap-northeast-2.pem" ubuntu@3.37.117.246
```

### Manual deploy (step-by-step on server)

```bash
cd /opt/maps
git pull origin master
source .venv/bin/activate
pip install -r requirements.txt   # requirements 변경 시에만
alembic upgrade head              # 마이그레이션 변경 시에만
sudo systemctl restart maps
sudo systemctl status maps        # 기동 확인
```

### One-liner deploy from local (PowerShell)

```powershell
ssh -i "D:\maps\LightsailDefaultKey-ap-northeast-2.pem" ubuntu@3.37.117.246 `
  "cd /opt/maps && git pull origin master && sudo systemctl restart maps && sudo systemctl status maps --no-pager"
```

### Check logs

```powershell
# 실시간 로그
ssh -i "D:\maps\LightsailDefaultKey-ap-northeast-2.pem" ubuntu@3.37.117.246 `
  "sudo journalctl -u maps -f"

# 최근 100줄
ssh -i "D:\maps\LightsailDefaultKey-ap-northeast-2.pem" ubuntu@3.37.117.246 `
  "sudo journalctl -u maps -n 100 --no-pager"
```

## Workflow Triggers

These keywords, when typed alone (or with an optional argument), activate a fixed automated workflow. Claude must follow the steps exactly — no skipping, no reordering.

### `!deploy` — Deploy to production server

When the user types `!deploy`, execute **in order**:

1. **Run tests** — `pytest --tb=short`
   - If **any test fails**: stop immediately and do **NOT** proceed.
2. **Confirm latest commit is pushed** — `git status` must show clean + up to date with remote.
3. **Deploy via SSH** — run the one-liner deploy command above (git pull + systemctl restart).
4. **Verify** — check `systemctl status maps` output shows `active (running)`.
5. Report the deployed commit hash and server status.

> **Safety rules:**
> - Never deploy if tests fail.
> - Never deploy a dirty working tree (uncommitted changes).
> - If `systemctl restart` fails, immediately run `sudo journalctl -u maps -n 50 --no-pager` and report the error.

### `!ship [commit message]` — Test → Commit → Push

When the user types `!ship` (optionally followed by a commit message), execute **in order**:

1. **Run tests** — `pytest --tb=short`
   - If **any test fails**: stop immediately, print the failure summary, and do **NOT** proceed to commit or push.
2. **Inspect working tree** — run `git status` and `git diff` in parallel.
3. **Stage changes** — `git add -u` (tracked files only; never `git add -A` to avoid committing secrets).
4. **Commit** — use the message provided after `!ship`. If no message was given, generate a concise one from the diff (imperative mood, ≤ 72 chars subject line), confirm it with the user, then commit.
5. **Push** — `git push` to the current branch's upstream.
6. Report the final commit hash and push result.

> **Safety rules that cannot be overridden by `!ship`:**
> - Never force-push (`--force`).
> - Never skip hooks (`--no-verify`).
> - If `git push` is rejected (non-fast-forward), stop and ask the user how to proceed.

**Usage examples:**
```
!ship
!ship fix: correct order cycle safety check
!ship feat: add maps_candidate_min_score env var
```

## Coding conventions

- All code requires type hints
- Docstrings on every function/class
- Use custom exceptions from `maps/common/exceptions.py`
- Load env vars via `maps.common.settings.get_settings()` (pydantic-settings, cached with `lru_cache`); never call `os.getenv` directly in feature modules
- `pytest` with `asyncio_mode = "auto"` (configured in `pyproject.toml`)
