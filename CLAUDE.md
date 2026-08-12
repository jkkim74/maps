# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MAPS (Market-Adaptive Profit Management System) is a validation-first Korean stock auto-trading platform. The core philosophy is **blocking bad strategies from reaching live accounts**, not running promising-looking strategies quickly.

### 코드를 찾을 때 (읽기 순서)

루트 **`index.md`** → 해당 패키지 **`CLAUDE.md`** → 소스. 저장소 전수 `grep`/`glob` 은
색인에서 못 찾았을 때만 한다. 문서와 코드가 어긋나면 **코드가 정본**이고, 발견한 자리에서
문서를 고친다 — `tests/test_docs_index.py` 가 패키지 문서의 트리 정합을 강제한다.

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
| `market/` | `regime.py` (market regime: strong/mixed/weak + weekly trend), `regime_history.py` (hysteresis — final label), `breadth.py`, `sector_selector.py`, `feeds.py` (measured liquidity/news feeds), `trading_rules.py` (KRX rules) |
| `ops/` | `scheduler.py` (APScheduler jobs), `notifications.py` (Slack/Telegram/FCM), `score_readiness.py` (fail-closed BUY gate), `pick_freshness.py`, `strategy_trade_plan.py`, `daily_digest.py`, `order_state.py`, `order_preview.py` |
| `ai/` | Bedrock adapters — `scoring_service.py` (bounded candidate AI pass), `technical_scorer.py`, `trade_planner.py` (fail-closed prices), `valuation_margin.py` |
| `stock_analysis/` | `analyzer.py` (pykrx + DART + SSE analysis), `history.py` (immutable analysis history, price overlay only) |
| `stock_report/` | `runner.py` — integrates external stock-report tool (`MAPS_STOCK_REPORT_PATH`); stores HTML results in `stock_report_runs` table |
| `api/` | One router per screen; `deps.py` provides `get_db()`; `schemas.py` holds Pydantic response models; `auth.py` has no prefix |
| `dashboard/` | `strategy_compare.py` |

각 패키지의 상세 계약은 `maps/<package>/CLAUDE.md`, 진입점은 루트 `index.md` 다.

### Daily scheduled pipeline (KST)

Production times (set in server `.env`; **code defaults in parentheses**):

`16:40` collect OHLCV (16:10) → `16:50` generate candidates (16:20) → `17:10` run validation (16:40) → `08:55` (next morning) place orders → `15:35` EOD sync. Stock report at `18:00` (default 15:00; moved past KRX EOD-data settle so the Market Supply/수급 report stops intermittently failing).

Times are settings-driven — override via `MAPS_DATA_COLLECTION_TIME`, `MAPS_CANDIDATE_TIME`,
`MAPS_VALIDATION_TIME`, `MAPS_ORDER_TIME`, `MAPS_EOD_TIME`, `MAPS_STOCK_REPORT_TIME`
(defaults in `maps/common/settings.py`). Enabled by `MAPS_SCHEDULER_ENABLED=true`. Off by default.

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
| `contrarian_quality_accumulation_v1` | `contrarian_quality` |

This mapping drives MC MDD limit lookups in `PromotionGate`. Adding a new strategy ID requires updating `STRATEGY_GROUP_MAP` in `common/constants.py`.

새 전략을 추가할 때는 `strategy/catalog.py` 의 `STRATEGY_PROSE` / `STRATEGY_CLASSES` 에도
등록하고 `docs/strategy_guides/` 에 가이드 원고를 둬야 한다. 빠뜨리면
`tests/test_strategy_catalog.py` 가 실패한다 — 전략관리 화면에 식별자만 뜨는 상태를 막는 장치다.
카탈로그에는 **산문만** 넣는다. 손절률·파라미터·선호 장세·MDD 는 코드에서 읽어 오므로
값을 복사해 두면 조용히 어긋난다.

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

7. **손절가는 `live_rules.effective_stop_price()` 하나로만 구한다.** 고정%와 ATR 손절 중
   **넓은(가격이 낮은) 쪽**이 정본이다. ATR 은 고정% 하한선을 느슨하게만 만들 수 있고
   조이지는 못한다. 청산 판정·**사이징**·화면 표시가 모두 이 함수를 거쳐야 한다.
   경로마다 다르면 백테스트와 실거래가 체계적으로 어긋난다(2026-07-29 수정: 사이징이
   고정%만 써서 ATR 이 넓은 종목의 포지션이 2배로 잡혔다).
   `backtest/portfolio_replay._resolve_stop` 만 별도 구현을 유지한다 — 전략 신호의
   `stop_price` 와 미등록 전략용 폴백이라는 백테스트 전용 입력이 있기 때문이다.

8. **pykrx 를 쓰기 전에 `ensure_krx_login_guard()` 를 호출한다.** pykrx 는 요청마다
   재로그인을 시도해서, 자격증명이 만료되면 재시도 누적이 KRX 계정을 잠근다
   (2026-07-27 실제 사고, 하루 158회). `maps/data/krx_auth.py` 참고.

9. **분석 워치리스트 픽의 신선도는 `ops/pick_freshness` 로만 판정한다.**
   `ref_date` 가 `MAPS_ANALYSIS_PICK_MAX_AGE_TRADING_DAYS`(기본 5거래일)보다 오래되면
   무장(`arm`)과 브래킷 진입이 차단된다. 이건 **파생 계산이지 상태 전이가 아니다** —
   만료 잡에 의존하면 잡이 멈춘 사이 오래된 픽이 다시 실주문을 낸다
   (2026-07-30 실제 사고: 6/30 픽 무장 17초 만에 진입, 그사이 주가 -39%).
   신선도는 반드시 `ref_date`(KST `Date`)로 계산한다. `created_at` 은 UTC naive 라
   09:00 KST 이전에 하루씩 어긋난다.
   > ⚠️ **`BOUGHT` 픽에는 만료를 적용하지 않는다.** 실제 보유 주식이고 익절·손절을
   > `_process_strategy_trades` 가 단독 관리한다 — 제외하면 청산 없이 방치된다.

## Allowed MDD by strategy group

| Strategy | mc_p95_limit |
|---|---|
| pullback_short | 18% |
| ath_outlier | 35% |
| multi_asset | 22% |
| donchian_research | 30% |
| contrarian_quality | 25% |
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
| `MAPS_ANALYSIS_PICK_MAX_AGE_TRADING_DAYS` | `5` | 분석 워치리스트 픽의 유효 기간(KRX 거래일). `ref_date` 가 이보다 오래되면 무장·진입 차단 |
| `MAPS_KRX_LOGIN_GUARD_ENABLED` | `true` | KRX 로그인 회로차단기. 끄면 자격증명 만료 시 재시도 누적으로 계정이 잠긴다 |
| `MAPS_KRX_LOGIN_MAX_FAILURES` | `3` | 연속 실패 몇 회에 회로를 열지 (치명 코드는 1회에 즉시 차단) |
| `MAPS_KRX_LOGIN_COOLDOWN_SECONDS` | `1800` | 최초 차단 시간. 재차단마다 2배 |
| `MAPS_KRX_LOGIN_MAX_COOLDOWN_SECONDS` | `21600` | 차단 시간 상한 |

## Production Server

| Item | Value |
|---|---|
| Provider | AWS Lightsail (ap-northeast-2 / Seoul) |
| Public IP | `3.37.117.246` |
| Domain / URL | `https://maps.magable.kr` |
| SSH user | `ubuntu` |
| SSH key | **PC마다 경로가 다르다** — 회사 PC `D:\ssh_maps\`, 집 PC `D:\maps\` (`LightsailDefaultKey-ap-northeast-2.pem`) |
| App root | `/opt/maps` |
| Service | `maps` (systemd) |

### Reverse proxy & HTTPS

Public traffic terminates at **nginx**, which reverse-proxies to uvicorn on
`127.0.0.1:8000`. HTTPS uses a **Let's Encrypt** cert (certbot, auto-renew via
`certbot.timer`). HTTP (80) 301-redirects to HTTPS (443).

- nginx site: `/etc/nginx/sites-enabled/maps.magable.kr` (server_name `maps.magable.kr`)
- cert: `/etc/letsencrypt/live/maps.magable.kr/` — renew test: `sudo certbot renew --dry-run`
- Lightsail firewall must keep **80 + 443** open (80 = ACME challenge + redirect).
- Cert/domain depends on a Lightsail **static IP** and a DNS A record → `3.37.117.246`.
- DNS는 Lightsail이 아니라 **외부 등록업체**에서 관리한다 (`ns1~4.hosting.co.kr`).

#### 2026-07-29 도메인 이전 (`magable.kr` → `maps.magable.kr`)

루트 도메인을 블로그(WordPress, `54.180.179.20`)에 넘기고 MAPS는 서브도메인으로 옮겼다.
`magable.kr` / `www.magable.kr` 은 **더 이상 이 서버를 가리키지 않는다.**
구 vhost `/etc/nginx/sites-available/maps` 는 비활성 상태로 남아 있다(참고용).

> ⚠️ **HSTS 헤더가 새 vhost에 없다.** 구 설정에는 있었으나 certbot이 만든 새 파일에는
> 빠졌다. 필요하면 443 블록에 `add_header Strict-Transport-Security "max-age=31536000" always;`
> 를 다시 넣을 것.

도메인을 또 바꾼다면 **재배포로는 갱신되지 않는 것들**을 반드시 함께 처리해야 한다:

| 항목 | 확인·조치 |
|---|---|
| 텔레그램 웹훅 | URL이 텔레그램 서버에 저장된다. `python scripts/setup_telegram_webhook.py` 재실행 후 `--info` 의 **`ip_address`** 가 우리 서버인지 확인 (URL 문자열만 보면 못 잡는다) |
| 모바일 앱 | `apps/mobile/src/config.ts` 의 `PROD_DEFAULT`. 이미 설치된 APK는 구 도메인에 고정되므로 재빌드·재설치가 필요하다 |
| 세션 쿠키 | host-only 라서 호스트가 바뀌면 전원 재로그인 (설정 변경은 불필요) |
| AdSense | `ads.txt` 는 구 vhost의 `location = /ads.txt` 에 있었다. 새 도메인에서 광고를 쓰려면 다시 등록·배치 |

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
ssh -i "D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem" ubuntu@3.37.117.246
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
ssh -i "D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem" ubuntu@3.37.117.246 `
  "cd /opt/maps && git pull origin master && sudo systemctl restart maps && sudo systemctl status maps --no-pager"
```

### Check logs

```powershell
# 실시간 로그
ssh -i "D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem" ubuntu@3.37.117.246 `
  "sudo journalctl -u maps -f"

# 최근 100줄
ssh -i "D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem" ubuntu@3.37.117.246 `
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
> - **마이그레이션이 있으면 `alembic upgrade head` 를 반드시 포함**한다. 위 원라이너에는
>   빠져 있다 — 빠뜨리면 새 컬럼 없이 기동해 런타임에서 깨진다.
> - 🔴 **16:00~16:45 KST 에는 배포하지 않는다.** `/etc/cron.d/maps-analyze` 가 매 거래일
>   16:00 에 `/analyze` 파이프라인을 45분 상한으로 돌린다. 이 시간에 `git pull` 하면
>   **에이전트가 읽는 작업 트리가 실행 중에 바뀌고** `systemctl restart` 까지 겹친다
>   (2026-08-03 실제 발생: 16:25 배포가 진행 중이던 analyze 와 겹쳤고 그 회차는 타임아웃).
>   급하면 배포 전에 실행 여부를 확인한다:
>   ```bash
>   flock -n /tmp/maps_analyze.lock true && echo "analyze 미실행 — 배포 가능" \
>     || echo "analyze 실행 중 — 대기"
>   ```

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
