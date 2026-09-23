# execution/

브로커 연동 및 주문 실행 패키지. Phase 4까지는 MockBroker만 사용한다.

## Directory structure

```
execution/
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
> 0.05초 간격, 프로세스 전역). 장중에는 상한가 엔진(지수 1초·스캔 5초·후보당 현재가)이
> 이 레인을 상시 점유해 **다른 호출은 줄을 선다** — 2026-09-22 실측으로 리스크·대시보드
> 화면의 잔고 조회가 장중 2~9초, 장외 0~1초였다. 화면이 실조회를 하게 만들지 말 것.

### KIS 재시도·timeout 규칙 (2026-09-23)

| 경로 | timeout (connect, read) | 재전송 |
|---|---|---|
| 조회 (잔고·시세·지수·순위·당일주문) | (`MAPS_KIS_CONNECT_TIMEOUT`=3, `MAPS_KIS_READ_TIMEOUT`=8) | 429/5xx·timeout 재시도 + 지터 |
| 주문 `place_order` (`idempotent=False`) | (3, `MAPS_KIS_TIMEOUT`=30) | **연결 실패·`EGW00201`/`EGW00215`·토큰 만료만.** 응답 전 timeout·그 밖의 5xx 는 `BrokerOrderUnknownError` |

> 🔴 **결과 불명 주문은 다시 보내지 않는다.** KIS 가 이미 접수했을 수 있다. 예전엔 어댑터 3회 ×
> `OrderManager` 3회로 **최대 9번** 재전송했고, 중복 가드는 우리 `order_log` 만 봐서 못 막았다.
> `OrderManager._resolve_unknown_order` 가 `get_daily_order_results()` 에서 같은 종목·방향·제출
> 시각 이후·미기록 주문을 **정확히 하나** 찾으면 그 주문으로 확정한다. 못 찾거나 여럿이거나 조회가
> 실패하면 **매수는 `unknown` 행을 남겨 당일 같은 종목 재매수를 막고**(`_raise_if_duplicate_active_order`,
> `order_state.claimed_candidate_tickers`), 이후 `broker_sync` 가 잔고의 당일 매수수량으로 체결 확정한다.
> **매도는 막지 않는다** — 막으면 보유가 청산 없이 방치된다(중복 매도는 KIS 가 주문가능수량 부족으로 거절).
> 취소(`cancel_order`)는 재전송해도 노출이 생기지 않아 기존 재시도를 유지한다.

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
| `submit(order, daily_pnl=0.0)` | 매수 주문 제출. RiskManager 사전 체크 → 주문 → order_log 기록 |
| `submit_exit(order)` | 매도 주문 제출 (리스크 체크 없이 직접 실행) |
| `sync_broker_state()` | 브로커 잔고·미결주문 동기화, portfolio_snapshot 갱신 |
| `expire_pending_orders()` | 당일 미체결 주문 만료 처리 |

`submit()` 흐름:
1. `RiskManager.check_before_order()` — Kill Switch · 손실한도 · 노출한도 체크
2. `broker.place_order()` — 주문 제출
3. `RiskManager.on_order_success/failure()` — 연속 실패 카운터 갱신
4. `order_log` 감사 기록

## 안전 제약

- `MAPS_LIVE_TRADING_ENABLED=false`이면 실주문 제출 없음 (mock 시뮬레이션만).
- `MAPS_BROKER_MODE=mock`이면 `MockBroker`만 사용.
- KISAdapter / KiwoomAdapter는 Phase 5 전용 — Phase 4까지는 연결 불가.

## 의존성

```
maps.common.models     → OrderLog, PortfolioSnapshot
maps.common.exceptions → KillSwitchError, DuplicateOrderError, BrokerAdapterError
maps.risk.manager      → RiskManager
maps.common.settings   → get_settings()
```
