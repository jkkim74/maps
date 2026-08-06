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
├── models.py          # 전체 DB 스키마 (ORM 모델 16개)
└── settings.py        # pydantic-settings 기반 환경변수 관리
```

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

## logging_config.py

`configure_logging(settings?)` — 콘솔 + RotatingFileHandler를 루트 로거에 등록. 멱등(기존 MAPS 핸들러 교체). SQLAlchemy 엔진 로그는 WARNING으로 제한.
