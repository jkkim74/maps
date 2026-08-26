# common/

MAPS 전체의 공통 기반 인프라 패키지. DB 연결, 설정, ORM 모델, 예외, 로깅을 제공한다.

## Directory structure

```
common/
├── __init__.py        # 빈 패키지 마커
├── account_history.py # 계좌 교체 기준일의 KST 날짜·UTC created_at 경계
├── constants.py       # 전역 상수 (ALLOWED_MDD, STRATEGY_GROUP_MAP, PROMOTION_GATES 등)
├── db.py              # SQLAlchemy 엔진 · 세션 팩토리
├── exceptions.py      # MAPS 커스텀 예외 계층
├── logging_config.py  # 콘솔 + 로테이팅 파일 로그 설정
├── models.py          # 전체 DB 스키마 (ORM 모델)
├── passwords.py       # scrypt 비밀번호 해시·검증 (stdlib만)
├── settings.py        # pydantic-settings 기반 환경변수 관리
├── sizing.py          # risk_based_qty — 계좌 위험 기반 주문 수량
└── user_prefs.py      # 개인 설정 해석 + 일일 AI 분석 한도
```

## passwords.py — 계정 비밀번호

| 함수 | 설명 |
|---|---|
| `hash_password(plain)` | `scrypt$n$r$p$salt$hash` 문자열 생성. 빈 비밀번호는 `ValueError` |
| `verify_password(plain, stored)` | 상수 시간 비교. **형식이 깨져도 예외 없이 `False`** (fail-closed) |
| `needs_rehash(stored)` | 저장된 파라미터가 현재 기준보다 약하면 참 — 로그인 성공 시 갱신 |

> `hashlib.scrypt` 는 표준 라이브러리다. `passlib`/`bcrypt` 를 새 의존성으로 넣지 않는다.
> 파라미터가 저장값 안에 들어 있으므로 세기를 올려도 기존 해시가 계속 검증된다.

## user_prefs.py — 개인 설정과 분석 한도

| 함수 | 설명 |
|---|---|
| `resolve(user, settings)` | 저장된 설정을 검증해 반환. 없거나 깨졌으면 `UserPreferences` 스키마 기본값 |
| `daily_analysis_limit(user)` | 계정별 하루 분석 허용 횟수. 관리자는 `-1`(무제한) |
| `analysis_used_today(db, user)` | 오늘(KST) 사용량 |
| `analysis_quota_exceeded(db, user)` | 한도 초과 여부 |

> ⚠️ **전역 `.env` 값으로 폴백하지 않는다.** `candidate_min_score` 를 전역
> `MAPS_CANDIDATE_MIN_SCORE` 로 채우면 설정한 적 없는 사용자에게도 화면 필터가 걸리고
> **화면 필터와 주문 게이트가 한 값으로 묶인다.** 미설정 = 필터 없음이다.
> `ops/order_preview.py`·`ops/scheduler.py` 의 전역값 사용은 주문 게이트라 별개다.

> ⚠️ 한도 확인은 **Bedrock 을 호출하기 전에** 해야 한다. 비용은 호출 순간 발생한다.
> 사용량은 `stock_analysis_history` 행 수로 세며 경계는 KST 자정이다
> (`created_at` 은 UTC naive 라 그대로 비교하면 하루씩 어긋난다).

## sizing.py

`risk_based_qty(...)` 하나뿐이다. 계좌 위험과 손절폭으로 주문 수량을 계산한다.

> ⚠️ 손절가는 반드시 `strategy/live_rules.effective_stop_price()` 결과를 넣는다.
> 고정%만 넣으면 ATR 손절이 넓은 종목의 포지션이 2배로 잡힌다(2026-07-29 실제 사고).

## constants.py — 주요 상수

| 상수 | 타입 | 설명 |
|---|---|---|
| `ALLOWED_MDD` | `dict[str, dict]` | 전략군별 expected/mc_p95_limit MDD 허용 한도 |
| `TREND_STRENGTH_BUCKETS` | `list[dict]` | S1~S5 버킷 [lo, hi) 구간 정의 |
| `PLATEAU_GRADES` | `dict[str, dict]` | A~F 등급별 양수 수익 비율 기준 |
| `STRATEGY_GROUP_MAP` | `dict[str, str]` | 전략 ID → 전략군 매핑 |
| `PROMOTION_GATES` | `dict[str, dict]` | 단계별 승격 조건 |
| `TRADEABILITY_THRESHOLDS` | `dict[str, int]` | mock_candidate=60, live_candidate=75 |
| `WEIGHT_PRESETS` | `dict[str, dict]` | conservative / balanced / growth 가중치 |
| `WF_SHARPE_MEAN_MIN` | `float` | 0.0 — WFA sharpe_mean 최솟값 |
| `WF_NEGATIVE_FOLD_MAX` | `int` | 1 — 음수 fold 최대 허용 수 |
| `WF_OOS_IS_G2P_MIN` | `float` | 0.6 — OOS/IS G2P 최솟값 |

## db.py — 데이터베이스

| 이름 | 설명 |
|---|---|
| `engine` | 모듈 레벨 기본 엔진 (앱 런타임용) |
| `SessionLocal` | `sessionmaker` 팩토리 (autocommit=False, autoflush=False) |
| `Base` | SQLAlchemy 선언적 베이스 |
| `get_db()` | FastAPI 의존성 주입용 제너레이터 |
| `get_engine(url?)` | url 없으면 settings → 기본값 순 |
| `make_session(url)` | 테스트·스크립트용 독립 세션 생성 |

SQLite: 기본. PostgreSQL: `pool_pre_ping=True`, `pool_recycle=1800` 적용.

## account_history.py

`MAPS_ACCOUNT_HISTORY_START_DATE`가 설정되면 그 KST 날짜 이전의 주문·브로커 스냅샷은
감사용으로 DB에 남기되 현재 계좌의 성과, MDD, 슬리피지, 거래 리뷰, `mock_months`에서
제외한다. 계좌를 재생성하거나 교체할 때 반드시 새 계좌 시작일로 갱신한다.

## exceptions.py — 예외 계층

```
MAPSError (base)
├── KillSwitchError
│   └── UnauthorizedLiquidationError
├── DuplicateOrderError(ticker)
├── ExposureCapError(ticker, exposure?)
├── ResearchStrategyError(strategy_id, stage)
├── DataQualityError
├── DataCollectionError
├── PromotionGateError(strategy_id, reasons)
│   ├── UnknownStrategyError(strategy_id)
│   └── MissingMetricError(metric, strategy_id)
├── BrokerAdapterError
├── ValidationError
│   └── PlateauDetectedError
├── BacktestError
└── StrategyConfigError
```

## models.py — ORM 테이블 (16개)

| 테이블 | 클래스 | 주요 용도 |
|---|---|---|
| `security_metadata` | `SecurityMetadata` | 종목 기본 정보 (상장·폐지·정지) |
| `universe_quality_log` | `UniverseQualityLog` | as-of-date 유니버스 생성 감사 |
| `candidate_snapshot` | `CandidateSnapshot` | 일별 전략 후보 종목 (factor_score, trend_strength, ts_bucket, final_score) |
| `historical_ohlcv` | `HistoricalOHLCV` | 일봉 OHLCV 히스토리 |
| `collection_log` | `CollectionLog` | 데이터 수집 이력 |
| `portfolio_snapshot` | `PortfolioSnapshot` | 일별 계좌 잔고 히스토리 |
| `parameter_plateau_results` | `ParameterPlateauResults` | Plateau 그리드 결과 |
| `walk_forward_results` | `WalkForwardResults` | WFA 요약 결과 |
| `walk_forward_fold_results` | `WalkForwardFoldResults` | WFA fold별 상세 결과 |
| `monte_carlo_sequence_results` | `MonteCarloSequenceResults` | MC 시뮬레이션 결과 |
| `promotion_history` | `PromotionHistory` | 승격 결정 감사 로그 |
| `tradeability_weight_log` | `TradeabilityWeightLog` | 가중치 프리셋 변경 이력 |
| `order_log` | `OrderLog` | Mock+Live 주문 감사 로그 |
| `holding_regime_audit` | `HoldingRegimeAudit` | 보유 종목 장세 오버레이의 일자·진입주문별 shadow 판정 |
| `app_user` | `AppUser` | 로그인 계정 · 역할(admin/user) · 개인 설정 JSON · 요금제 |
| `stock_report_runs` | `StockReportRun` | Stock Report 생성 이력 |
| `kill_switch_log` | `KillSwitchLog` | Kill Switch 이벤트 감사 |
| `strategy_param_log` | `StrategyParamLog` | 실거래 파라미터 변경 이력 |
| `cost_model_assumptions` | `CostModelAssumptions` | CostModel 가정값 변경 이력 |

> **감사 로그 4종** (`promotion_history`, `universe_quality_log`, `order_log`, `kill_switch_log`)은 Day 1부터 존재해야 한다.

## settings.py — 환경변수 설정

`MapsSettings(BaseSettings)` — pydantic-settings로 `.env` 및 프로세스 환경변수를 로드한다.

| 핵심 함수 | 설명 |
|---|---|
| `get_settings()` | `@lru_cache` — 앱 전체에서 공유되는 싱글턴 |
| `reload_settings()` | 캐시를 지우고 재로드 (테스트용) |
| `get_config_status(settings?)` | 통합 설정 준비 상태를 섹션별로 반환 |
| `get_missing_required_settings(settings?)` | 필수 env var 누락 목록 반환 |

**규칙**: 피처 모듈에서 `os.getenv()` 직접 호출 금지 — 반드시 `get_settings()` 사용.

보유 장세 오버레이는 `MAPS_HOLDING_REGIME_OVERLAY_MODE=off|shadow`만 허용한다.
`enforce`는 v1 설정값이 아니며 실제 매도 코드도 없다. 장세 신선도와 두 관측 사이의 최대
달력 간격은 `MAPS_HOLDING_REGIME_MAX_AGE_DAYS`(기본 3일) 하나로 관리한다.

## logging_config.py

`configure_logging(settings?)` — 콘솔 + RotatingFileHandler를 루트 로거에 등록. 멱등(기존 MAPS 핸들러 교체). SQLAlchemy 엔진 로그는 WARNING으로 제한.
