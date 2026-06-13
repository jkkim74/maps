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
| `strategy/` | `base.py` abstract; `pullback_v3.py`, `ath_breakout_v1/v2.py`, `donchian_v1/v2.py`, `multi_asset_trend_v1.py` concrete strategies |
| `backtest/` | `engine.py` + `cost_model.py` — runs strategy backtests |
| `validation/` | `walk_forward.py`, `plateau.py`, `monte_carlo.py` — validation suite |
| `promotion/` | `gate.py` — decides strategy stage advancement and writes audit log |
| `execution/` | `broker_adapter.py` (abstract), `mock_broker.py`, `kis_adapter.py`, `kiwoom_adapter.py`, `order_manager.py` |
| `risk/` | `manager.py` — kill switch + exposure limits |
| `market/` | `regime.py` (market regime), `trading_rules.py` (KRX rules) |
| `ops/` | `scheduler.py` (APScheduler jobs), `notifications.py` (Slack), `order_state.py`, `order_preview.py` |
| `api/` | One router per screen; `deps.py` provides `get_db()`; `schemas.py` holds Pydantic response models |
| `dashboard/` | `strategy_compare.py` |

### Daily scheduled pipeline (KST)

`16:10` collect OHLCV → `16:20` generate candidates → `16:40` run validation → `08:55` (next morning) place orders → `15:35` EOD sync

Enabled by `MAPS_SCHEDULER_ENABLED=true`. Off by default.

### Promotion stages

`research` → `alert_only` → `mock_candidate` → `live_candidate` → `live` (or `rejected`)

`PromotionGate` in `promotion/gate.py` evaluates all three validation checks and writes to `promotion_history`. Unknown strategy IDs or missing metrics must always result in `fail with reason` (never `KeyError`).

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

Promotion thresholds: `mock_candidate=60`, `live_candidate=75` (fixed, independent of weights).

## Coding conventions

- All code requires type hints
- Docstrings on every function/class
- Use custom exceptions from `maps/common/exceptions.py`
- Load env vars via `maps.common.settings.get_settings()` (pydantic-settings, cached with `lru_cache`); never call `os.getenv` directly in feature modules
- `pytest` with `asyncio_mode = "auto"` (configured in `pyproject.toml`)
