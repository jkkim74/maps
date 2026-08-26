# ops/

운영 자동화 패키지. 스케줄러, 알림, 주문 상태·미리보기, 그리고 **자동 주문을 막는 게이트**를
포함한다.

## Directory structure

```
ops/
├── __init__.py             # 빈 패키지 마커
├── candidate_selection.py  # AI 모드별 후보 주문 자격 SQL 식
├── daily_digest.py         # 하루치 매매 기록 결정적 조립 (블로그 입력)
├── notifications.py        # Slack / Telegram / FCM 알림
├── order_preview.py        # 다음 거래일 예정 주문 미리보기
├── order_state.py          # claimed_candidate_tickers — 중복 주문 방지 헬퍼
├── pick_freshness.py       # 분석 픽 신선도 판정 (파생 계산)
├── reconciliation.py       # 주문 정산·미체결 진단 리포트
├── report_generator.py     # 코스톨라니 일일 투자 판단 리포트
├── scheduler.py            # OperationalPipeline + MapsOperationalScheduler
├── score_readiness.py      # 실측 점수 100% 게이트 (fail-closed)
└── strategy_trade_plan.py  # 전략매매 안전한도·주문계획 검증 (순수 함수)
```

## scheduler.py — 핵심 오케스트레이터

### 상수

| 상수 | 설명 |
|---|---|
| `_RUNNABLE_STRATEGIES` | 파이프라인에 등록된 전략 ID → 클래스 딕셔너리 |
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
final_score = 0.6 × factor_score(거래대금) + 0.4 × trend_strength   # legacy (기본)
```

`MAPS_STRATEGY_AWARE_SCORING_ENABLED=true`면 `StrategyAwareScoreCalculator`가 전략
유형별 가중치를 대신 쓴다 (pullback/contrarian은 유동성 가중치 없음, contrarian은
밸류마진 60 미만 시 excluded_reason 기록). factor_score는 유니버스 1위 거래대금 대비
상대값이라 legacy 식에서는 초대형주가 항상 최상단에 온다.

#### `_order_candidates` 필터 조건

- `weekly_pass = True`
- `final_score ≥ MAPS_CANDIDATE_MIN_SCORE` (모드별 비교 대상은 `candidate_selection.py`)
- 전략 승격 단계: `mock_candidate`, `live_candidate`, `live` 중 하나
- 당일 이미 주문된 ticker 제외 (`claimed_candidate_tickers`)
- ticker당 최고 score 전략 1개만 사용
- **시장·후보 점수가 100% 실측** (`score_readiness.py`)

준비도로 막힌 후보는 **후보마다 WARNING 로그**를 남기고 사유별로 집계돼
`order_cycle` 잡 결과의 `blocked_by_readiness` 로 나간다(`skipped_buy_orders` 에도 더해진다).
조용히 `continue` 하면 10건이 막혀도 잡 결과가 `"skipped_buy_orders": 0` 으로 보인다 —
2026-08-12~14 사고에서 원인 규명이 이틀 늦어진 직접적인 이유다.

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

broker sync는 기존 손절·익절·전략 청산을 먼저 처리한 뒤 보유 장세 shadow 감사를 별도
경계에서 기록한다. 감사 오류는 rollback·로그만 남기고 기존 청산을 막지 않는다. 오버레이는
`source=candidate_generation` 장마감 행만 읽고 주문을 제출하지 않으며, 동일 입력의 당일 감사는
다시 쓰지 않는다. `BOUGHT AnalysisPick`은 전략매매 설정과 무관하게 오버레이에서 제외한다.

### 전역 함수

| 함수 | 설명 |
|---|---|
| `get_operational_scheduler()` | 싱글턴 `MapsOperationalScheduler` 반환 |
| `start_operational_scheduler_if_enabled()` | `MAPS_SCHEDULER_ENABLED=true`이면 시작 |
| `shutdown_operational_scheduler()` | 스케줄러 종료 |

## order_state.py

`claimed_candidate_tickers(db, since) → set[str]`

`since` 날짜 이후 `PENDING`, `PARTIALLY_FILLED`, `FILLED` 상태의 BUY 주문 ticker를 반환한다. 동일 종목 중복 주문 방지용.

## score_readiness.py — 자동 매수·승격을 막는 게이트

| 함수 | 설명 |
|---|---|
| `market_score_ready(db, ref_date)` | 그 **정확한 관측일**의 시장 점수가 100% 실측인가 |
| `current_market_score_ready(db, order_date)` | 주문일 직전 **완료된 세션** 기준으로 판정 |
| `candidate_score_ready(db, ...)` | 시장 + 후보 관측이 모두 정확한 날짜에 완비됐는가 |

> ⚠️ **fail-closed.** 모든 자동 신규 BUY(후보 주문, 단일·분할 전략매매)와 전략 승격은
> 여기를 통과해야 한다. SELL·손절·익절·기존 포지션 청산에는 **적용하지 않는다** —
> 걸면 보유 종목이 청산 없이 방치된다. 불완전한 점수는 화면에 보이되 주문에는 쓰이지 않는다.
> 커버리지·상태·출처는 `market_regime_log` 와 후보 스냅샷에 함께 저장된다.

## pick_freshness.py — 분석 픽 신선도

| 함수 | 설명 |
|---|---|
| `pick_cutoff_date()` | 신선하다고 인정되는 가장 오래된 `ref_date` |
| `is_pick_stale()` | 만료 여부 (`ref_date == cutoff` 는 아직 신선) |
| `pick_stale_reason()` | 만료 사유 코드 (신선하면 `None`) |
| `pick_age_trading_days()` | 기준일이 몇 거래일 지났는지 |

> ⚠️ 이건 **파생 계산이지 상태 전이가 아니다.** 만료 잡에 의존하면 잡이 멈춘 사이 오래된
> 픽이 다시 실주문을 낸다(2026-07-30: 6/30 픽 무장 17초 만에 진입, 그사이 주가 -39%).
> 신선도는 반드시 `ref_date`(KST `Date`)로 계산한다 — `created_at` 은 UTC naive 라
> 09:00 KST 이전에 하루씩 어긋난다. **`BOUGHT` 픽에는 만료를 적용하지 않는다.**

## candidate_selection.py — 모드별 자격 SQL

| 함수 | 설명 |
|---|---|
| `candidate_min_score_expression()` | AI 모드별로 최소 점수 게이트에 쓸 점수 컬럼 |
| `candidate_recommendation_eligible_expression()` | `replace` 모드에서 AI 후보군에 못 든 행만 제외 |

주문 경로와 화면이 같은 식을 공유하도록 SQL 표현식을 한곳에 둔다.

## strategy_trade_plan.py — 전략매매 안전 한도 (순수 함수)

| 함수 | 설명 |
|---|---|
| `calculate_trade_limits()` | 예산 입력 **없이** 계좌 기준 안전 최대금액·최소 주문금액 |
| `validate_trade_plan()` | 총액과 회차 수량을 얹어 최종 검증 |

안전 최대금액 = 주문가능 현금 · 단일 종목 노출 · 포트폴리오 잔여 용량 · 손절 위험 한도의
**최솟값**. `TradePlanBlocker` 로 차단 사유를 돌려준다. preview 는 참고일 뿐이고 최종
`arm` 은 최신 잔고·게이트·중복·한도를 **다시** 검증한다.

## notifications.py — 알림 3종

| 클래스 | 진입점 | 비고 |
|---|---|---|
| `SlackNotifier` | `get_notifier()` | webhook URL 비면 no-op. `send_kill_switch()`, `send_job_failed()`, `send_order_alert()` |
| `TelegramNotifier` | `get_telegram_notifier()` | 편입 알림의 `[무장]/[무장해제]` 인라인 버튼. 콜백은 `api/telegram.py` |
| `FcmNotifier` | `get_fcm_notifier()` | 모바일 네이티브 푸시 (HTTP v1) |

> ⚠️ 텔레그램 **웹훅 URL 은 텔레그램 서버에 저장된다.** 도메인을 바꾸면 재배포로 갱신되지
> 않는다 — `python scripts/setup_telegram_webhook.py` 재실행 후 `--info` 의 `ip_address`
> 를 확인한다(URL 문자열만 보면 못 잡는다).

## order_preview.py / order_state.py / reconciliation.py

| 모듈 | 함수 | 설명 |
|---|---|---|
| `order_preview.py` | `next_trading_day()`, `build_order_preview()` | 브로커 호출 없이 DB+설정만으로 다음 거래일 예정 주문 시뮬레이션 |
| `order_state.py` | `claimed_candidate_tickers()` | `since` 이후 `PENDING`/`PARTIALLY_FILLED`/`FILLED` BUY ticker |
| `reconciliation.py` | `build_reconciliation()`, `format_reconciliation_text()` | KST 일자 기준 체결률 집계 + 미체결 도달 가능성 진단 |

## daily_digest.py / report_generator.py

| 모듈 | 설명 |
|---|---|
| `daily_digest.py` | `build_daily_digest(ref_date)` — 하루치 매매 기록을 **결정적으로** 조립. 블로그 원고와 SCR-20 의 유일한 사실 출처 |
| `report_generator.py` | `KostolanyReportGenerator.generate()` → `DailyReport.to_text()` — 시장·섹터·전략·후보·리스크 요약 |

> ⚠️ 다이제스트의 `price_source=rule` 값을 AI 결론처럼 표현하면 안 된다.
>
> ⚠️ 다이제스트는 **결정 시점에 저장된 값만** 읽고 재계산하지 않는다.
> `scripts/backfill_market_score.py` 로 과거 `market_regime_log` 를 복구한 날짜의
> 다이제스트·블로그는 **재생성하지 않는다** — 재생성하면 결정 시점이 아니라 복구 후 값을
> 설명하게 된다. 그래서 그 스크립트는 `score_reason` 에 결정 시점 커버리지를 함께 남긴다.

포트폴리오의 `regime_overlay`는 해당 날짜 `holding_regime_audit` 중 실제 최신 BUY의
`position_key=order:<id>`와 일치하는 행만 연결한다. `action=exit`도 v1에서는 실제 매도가 아니라
shadow 후보라는 뜻이다.

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
