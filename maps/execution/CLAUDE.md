# execution/

브로커 연동 및 주문 실행 패키지. Phase 4까지는 MockBroker만 사용한다.

## Directory structure

```
execution/
├── __init__.py        # 빈 패키지 마커
├── broker_adapter.py  # BrokerAdapter (ABC) + 공통 데이터 클래스 + get_broker() 팩토리
├── mock_broker.py     # MockBroker — 인메모리 주문 시뮬레이터 (Phase 1~4)
├── kis_adapter.py     # KISAdapter — 한국투자증권 OpenAPI (Phase 5)
├── kiwoom_adapter.py  # KiwoomAdapter — 키움증권 OpenAPI (Phase 5)
└── order_manager.py   # OrderManager — 주문 제출 + 리스크 연동 + 감사 로그
```

## broker_adapter.py — 공통 인터페이스

### Enum 타입

| Enum | 값 |
|---|---|
| `OrderSide` | `BUY`, `SELL` |
| `OrderType` | `MARKET`, `LIMIT` |
| `OrderStatus` | `PENDING`, `FILLED`, `PARTIALLY_FILLED`, `CANCELLED`, `REJECTED` |

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
