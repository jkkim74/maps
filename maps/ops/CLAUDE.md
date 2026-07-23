# ops/

운영 자동화 패키지. 스케줄러, Slack 알림, 주문 상태, 주문 미리보기를 포함한다.

## Directory structure

```
ops/
├── __init__.py       # 빈 패키지 마커
├── notifications.py  # SlackNotifier — Slack Webhook 알림
├── order_preview.py  # 다음 거래일 예정 주문 미리보기
├── order_state.py    # claimed_candidate_tickers — 중복 주문 방지 헬퍼
└── scheduler.py      # OperationalPipeline + MapsOperationalScheduler
```

## scheduler.py — 핵심 오케스트레이터

### 상수

| 상수 | 설명 |
|---|---|
| `_RUNNABLE_STRATEGIES` | 파이프라인에 등록된 전략 ID → 클래스 딕셔너리 (7개) |
| `_VALIDATION_SAMPLE_TICKERS` | Plateau/MC 백테스트 샘플 종목 수 (5) |
| `_WFA_PREFERRED_TICKERS` | WFA 우선 선택 대형주 목록 (삼성전자, SK하이닉스 등) |
| `_MIN_FRESH_TICKERS` | OHLCV 데이터 신선도 판단 최소 종목 수 (50) |

### `JobRun` (dataclass)

`name, status, started_at, finished_at, message, details`

### `OperationalPipeline`

```python
OperationalPipeline(settings=None, session_factory=SessionLocal, notifier=None)
```

KST 실행 시간은 운영 실제값(서버 `.env` 오버라이드) 기준이며, 괄호는 코드 기본값(`settings.py`).

| 메서드 | KST 실행 시간 | 설명 |
|---|---|---|
| `collect_data(ref_date?)` | 16:40 (기본 16:10) | OHLCV + 메타 수집 |
| `generate_candidates(ref_date?)` | 16:50 (기본 16:20) | 유니버스 필터 → 후보 스냅샷 저장 + 시황 분석 |
| `run_validation(ref_date?)` | 17:10 (기본 16:40) | WFA/Plateau/MC 생성 → 승격 평가 |
| `run_order_cycle(ref_date?)` | 08:55 | 브로커 동기화 → 매도/매수 주문 제출 |
| `sync_broker_state(ref_date?)` | 60초 간격 | 장중 포지션·잔고 동기화, 손절 모니터링 |
| `run_eod_cleanup(ref_date?)` | 15:35 | 미체결 주문 취소, 브로커 EOD 처리 |
| `backfill_ohlcv(start, end)` | 수동 | 기간 OHLCV 백필 |

#### `run_order_cycle` 안전 체크 (순서 중요)

1. `MAPS_LIVE_TRADING_ENABLED=false` → 주문 제출 없음
2. 시황 분석 → `entry_limit_ratio=0.0` → 매수 전량 스킵
3. OHLCV 신선도 검사 (`_is_data_fresh`) → 5일 이상 오래된 데이터면 매수 스킵
4. Kill Switch 발동 → 당일 매수 중단 (포지션 청산은 별도 승인)

#### `_save_candidate_snapshot` final_score 계산

```
final_score = 0.6 × factor_score(거래대금) + 0.4 × trend_strength
```

#### `_order_candidates` 필터 조건

- `weekly_pass = True`
- `final_score ≥ MAPS_CANDIDATE_MIN_SCORE`
- 전략 승격 단계: `mock_candidate`, `live_candidate`, `live` 중 하나
- 당일 이미 주문된 ticker 제외 (`claimed_candidate_tickers`)
- ticker당 최고 score 전략 1개만 사용

### `MapsOperationalScheduler`

```python
MapsOperationalScheduler(settings=None, pipeline=None)
```

APScheduler(`BackgroundScheduler`) 래퍼.

| 메서드 | 설명 |
|---|---|
| `start()` / `shutdown()` | 스케줄러 시작/종료 |
| `status()` | 등록 잡 목록 + 마지막 실행 결과 |
| `run_once(job_name)` | 잡 수동 실행 |
| `backfill_ohlcv(start, end)` | OHLCV 백필 수동 실행 |

KRX 거래일이 아니면 잡 실행을 건너뛴다 (`_is_krx_market_day()` 캐시 사용).

`broker_sync`는 `IntervalTrigger`(60초 간격), 나머지는 `CronTrigger`(월~금 지정 시각).

### 전역 함수

| 함수 | 설명 |
|---|---|
| `get_operational_scheduler()` | 싱글턴 `MapsOperationalScheduler` 반환 |
| `start_operational_scheduler_if_enabled()` | `MAPS_SCHEDULER_ENABLED=true`이면 시작 |
| `shutdown_operational_scheduler()` | 스케줄러 종료 |

## order_state.py

`claimed_candidate_tickers(db, since) → set[str]`

`since` 날짜 이후 `PENDING`, `PARTIALLY_FILLED`, `FILLED` 상태의 BUY 주문 ticker를 반환한다. 동일 종목 중복 주문 방지용.

## notifications.py — SlackNotifier

`SlackNotifier(settings?)` — `slack_webhook_url`이 비어있으면 no-op.

| 메서드 | 설명 |
|---|---|
| `send_kill_switch(strategy_id, event_type, reason, detail, approved_by)` | Kill Switch 이벤트 알림 |
| `send_job_failed(job_name, error)` | 스케줄러 잡 실패 알림 |

## order_preview.py

다음 거래일 예정 매수/매도 주문을 미리보기. API 라우터에서 호출.

## 의존성

```
maps.common.*         → 전체 모델·설정·예외
maps.data.*           → DataCollector, HistoricalOHLCVRepository
maps.data_quality.*   → DataQualityFilter
maps.execution.*      → OrderManager, get_broker
maps.market.*         → create_regime_analyzer, is_krx_closed_date
maps.promotion.*      → PromotionGate
maps.risk.*           → RiskManager
maps.strategy.*       → 모든 전략 클래스
maps.validation.*     → WalkForwardAnalyzer, ParameterPlateauTester, MonteCarloValidator
maps.indicator.*      → TrendStrengthCalculator
maps.stock_report.*   → run_all_reports_if_idle
apscheduler           → BackgroundScheduler, CronTrigger, IntervalTrigger
```
