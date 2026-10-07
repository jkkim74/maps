# execution/

브로커 연동 및 주문 실행 패키지. Phase 4까지는 MockBroker만 사용한다.

## Directory structure

```
execution/
├── safety.py          # 실행 모드·계좌별 단일 프로세스 잠금
├── safety_admin.py    # 근거와 버전을 확인하는 관리자 해결 작업
├── reconciliation.py # 브로커 체결·계좌 검증, 입출금 보정 손실 한도
├── __init__.py        # 빈 패키지 마커
├── broker_adapter.py  # BrokerAdapter (ABC) + 공통 데이터 클래스 + get_broker() 팩토리
├── mock_broker.py     # MockBroker — 인메모리 주문 시뮬레이터 (Phase 1~4)
├── kis_adapter.py     # KISAdapter — 한국투자증권 OpenAPI (Phase 5)
├── kis_request_stats.py # KIS 요청 시도 단위 집계 — 1분 요약 로그·일간 누적(장마감 리포트)
├── kiwoom_adapter.py  # KiwoomAdapter — 키움증권 OpenAPI (Phase 5)
└── order_manager.py   # OrderManager — 주문 제출 + 리스크 연동 + 감사 로그
```

## broker_adapter.py — 공통 인터페이스

### Enum 타입

| Enum | 값 |
|---|---|
| `OrderSide` | `BUY`, `SELL` |
| `OrderType` | `MARKET`, `LIMIT` |
| `OrderStatus` | `PENDING`, `FILLED`, `PARTIALLY_FILLED`, `CANCELLED`, `REJECTED`, `UNKNOWN`(제출 결과 불명 — 아래 참고) |

### 데이터 클래스

| 클래스 | 주요 필드 |
|---|---|
| `Order` | strategy_id, ticker, side, order_type, quantity, limit_price?, current_price?, memo |
| `OrderResult` | order_id, strategy_id, ticker, side, status, filled_quantity, avg_price |
| `Position` | ticker, quantity, avg_price, name, current_price?, evaluation_value? |
| `AccountBalance` | cash, positions_value, total_assets? → `total_value` 프로퍼티 |
| `PendingOrder` | order_id, ticker, side, quantity, remaining_quantity, order_price? |
| `SameDayBuy` | ticker, quantity, avg_price? |

### `BrokerAdapter` (ABC)

| 추상 메서드 | 설명 |
|---|---|
| `place_order(order)` | 주문 제출 |
| `cancel_order(order_id)` | 주문 취소 |
| `get_position(ticker)` | 특정 종목 포지션 조회 |
| `get_account_balance()` | 계좌 잔고 조회 |
| `is_market_open()` | 장 개장 여부 |

선택적 메서드 (기본 `NotImplementedError`):
`get_open_orders()`, `get_daily_order_results()`, `get_same_day_buys()`, `update_prices(prices)`

`get_position_snapshot(max_age_seconds) → PositionSnapshot(positions, balance, as_of)` —
**조회 전용 화면용**. `max_age_seconds` 이내의 잔고 캐시가 있으면 브로커를 부르지 않는다
(KIS 구현; 기본 구현은 항상 실조회). 라우터는 `screen_position_snapshot(broker, max_age)`
헬퍼로 부른다 — 테스트 대역처럼 메서드가 없는 객체도 받는다.
**주문 경로는 이걸 쓰지 않는다** — 주문 직후 판단에 낡은 잔고가 들어가면 안 된다.

> 🔴 **KIS REST 는 계정당 한 레인으로 직렬화된다** (`_pace_request`, 모의서버 0.55초(설정)·실서버
> 0.05초 간격, 프로세스 전역). 장중에는 상한가 엔진(지수 5초 또는 WS·스캔 5초·후보당 현재가)이
> 이 레인을 상시 점유해 **다른 호출은 줄을 선다** — 2026-09-22 실측으로 리스크·대시보드
> 화면의 잔고 조회가 장중 2~9초, 장외 0~1초였다. 화면이 실조회를 하게 만들지 말 것.

### KIS 재시도·timeout 규칙 (2026-09-23)

| 경로 | timeout (connect, read) | 재전송 |
|---|---|---|
| 조회 (잔고·시세·지수·순위·당일주문) | (`MAPS_KIS_CONNECT_TIMEOUT`=3, `MAPS_KIS_READ_TIMEOUT`=8) | 429/5xx·timeout 재시도 + 지터 |
| 반드시 성공해야 하는 조회 (`with kis_adapter.patient_reads():` — 현재 상한가 엔진 기동 복구만) | (3, `MAPS_KIS_TIMEOUT`=30) | 조회와 같음. ContextVar 라 감싼 스레드 안에서만 적용 |
| 주문 `place_order` (`idempotent=False`) | (3, `MAPS_KIS_TIMEOUT`=30) | **연결 실패·`EGW00201`/`EGW00215`·토큰 만료만.** 응답 전 timeout·그 밖의 5xx 는 `BrokerOrderUnknownError` |

> 🔴 **결과 불명 주문은 다시 보내지 않는다.** KIS 가 이미 접수했을 수 있다. 예전엔 어댑터 3회 ×
> `OrderManager` 3회로 **최대 9번** 재전송했고, 중복 가드는 우리 `order_log` 만 봐서 못 막았다.
> 매수·매도 모두 `UNKNOWN` intent와 예약을 유지한다. 유사한 주문이나 잔고 수량으로 연결하거나
> 체결을 추정하지 않는다. 관리자가 정확한 브로커 주문 식별자 또는 미접수 확인 자료를 제공해야 한다.
> 취소(`cancel_order`)도 `idempotent=False`다. 응답 전 단절은 자동 재전송하지 않으며,
> 취소 접수와 실제 취소 확정을 구분한다.

모의 REST 간격은 `MAPS_KIS_PAPER_MIN_INTERVAL_SECONDS`(기본 0.55초). KIS 는 **도착 시각**으로
세므로 한도에 딱 맞추지 않는다. 모의 계좌의 정확한 한도(초당 1건 vs 2건)는 공식 확인 전이다 —
`kis_request_stats` 의 `rate_limited` 시도 수로 판단한다. 시도마다 실패는 WARNING
(`KIS 요청 실패 시도 n/m: path tr_id=… msg_cd=… ms`), 1분마다 `KIS req summary` INFO 한 줄.

### `get_broker(mode?, **kwargs) → BrokerAdapter`

팩토리 함수. `mode`: `"mock"` | `"kis"` | `"kiwoom"`. 설정에서 자동 결정.

## mock_broker.py — MockBroker

인메모리 상태로 주문을 시뮬레이션한다. 테스트 및 Phase 4 실행에 사용.

```python
MockBroker(initial_cash=100_000_000, price_feed: dict[str, float] | None = None)
```

`price_feed`: `{ticker: price}` — 없으면 주문 limit_price를 체결가로 사용.

## order_manager.py — OrderManager

```python
OrderManager(broker: BrokerAdapter, risk: RiskManager, db: Session)
```

| 메서드 | 설명 |
|---|---|
| `submit(order, context=...)` | 계좌·전략·현금·미체결 노출 검증 후 영속 intent를 기록하고 제출 |
| `submit_exit(order, context=...)` | 실행 모드·최신 매도 가능 수량·해당 소스의 소유 수량 검증 후 제출 |
| `sync_broker_state()` | 정확한 주문 식별자 대조, 계좌 관측·손실 한도·알림 재시도 |
| `cancel(order_id)` | 계좌 소속 확인과 취소 요청 기록 후 전송. 실제 취소 내역으로 확정 |
| `expire_pending_orders()` | 항상 0. 시간 경과를 취소 증거로 사용하지 않음 |

`submit()` 흐름:
1. 계좌별 프로세스/스레드 잠금, 이벤트 중복 검사, 브로커 계좌 대조
2. 매수 자격·손실·보유 및 예약 노출, 또는 매도 소유 수량 검증
3. `PREPARED → SENDING` 영속 기록 후 단일 제출
4. 명확한 결과만 반영. 불명 응답은 `UNKNOWN`으로 보관하고 자동 재전송 금지

## 안전 제약

분석 워치리스트(`source="analysis_pick"`) 단일·분할 매수는 픽의 존재·종목·활성 상태·
유효기간 검증 후에만 업종·테마 분류/비중 검사를 생략한다. 상한가 V1(`source="limit_up"`)
매수도 엔진 활성·자동 모드와 DB 세션의 존재·종목 일치·자동 실행 모드를 검증한 뒤
같은 예외를 적용한다. 전략 이름만으로는 예외가 적용되지 않는다. 정책은 `OrderManager`에서
선택하며, 외부 API에 우회 플래그를 노출하지 않는다. 현금·종목 비중·총노출·손실·
중복 주문·계좌 대사와 매도 소유권 검사는 유지한다. 주문 intent의 기존 `source`와
`source_id`로 적용 경로를 감사한다. 설정·스키마 변경은 없다.

기존 ARMED 픽에도 배포 후 적용되므로 가격과 나머지 안전 조건이 충족되면 다음 주기에
주문이 제출될 수 있다. 상한가 V1도 기존 감시 세션의 진입 신호와 나머지 조건이 충족되면
적용된다. 배포 전 활성 픽과 상한가 세션을 확인한다.

- `MAPS_LIVE_TRADING_ENABLED=false` 또는 dry-run이면 mock을 포함해 매수·매도·취소 금지.
- `MAPS_BROKER_MODE=mock`이면 `MockBroker`만 사용.
- Kiwoom은 새 계좌 검증 계약을 지원할 때까지 실행 금지.
- 운영 복구·검증 자격·마이그레이션 절차: [계좌 실행 안전 안내](../../docs/execution_safety.md).

## 의존성

```
maps.common.models     → OrderLog, PortfolioSnapshot
maps.common.exceptions → KillSwitchError, DuplicateOrderError, BrokerAdapterError
maps.risk.manager      → RiskManager
maps.common.settings   → get_settings()
```


### KIS gate and attempt diagnostics (2026-10-01)

The process-wide gate is shared by account and environment, including adapters
with different app keys. Each caller locks, checks current monotonic time, grants
only when due, or unlocks, sleeps and rechecks. No future slots are reserved and
neither sleep nor HTTP holds the gate lock. This guarantees grant spacing, not
HTTP/server arrival spacing; intervals, timeouts and order retry rules are unchanged.

Trading/query HTTP attempts emit secret-free DEBUG diagnostics: PID, endpoint,
TR ID, attempt, gate wait, local HTTP start gap, latency and outcome. Existing
minute summaries include endpoint outcomes, gate-wait p95, minimum observed start
gap and interval violations. Initial gaps are unknown. HTTP 200 business rejections
count as failed attempts (`api_error` or `rate_limited`), without changing retries.
Authentication/hash requests share the gate but remain outside trading/query
attempt totals. Totals are process-local attempts, not failed scheduler jobs.

## Fujimoto source and actual costs

OrderManager validates `source=fujimoto`, cycle source_id and reservation event
on both BUY and SELL; a Fujimoto strategy name cannot use the mock/catalog source
to bypass this guard. Classification limits still apply. Unbound committed BUY
reservations are visible to all strategies and linked broker/intent/reservation
exposure is counted once. Reconciliation binds the exact reservation and invokes
the common cumulative fill ledger; more Fujimoto-owned shares than actual broker
shares block reconciliation. No old position adoption exists.
OrderResult adds optional cumulative_gross/tax and costs_complete (default false).
KIS history total consideration is read when present; missing per-order fees/taxes
remain unknown. MockBroker supplies confirmed cost fields. Terminal quantity truth
allows consented protective exits with provisional cost, while new BUY and
net-profit book exits wait for settlement. Exact audited settlement is documented
in `maps/fujimoto/CLAUDE.md`; ambiguous late cost reconstruction remains blocked.
