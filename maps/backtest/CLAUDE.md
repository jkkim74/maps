# backtest/

이벤트 기반 백테스트 엔진과 거래 비용 모델 패키지.

## Directory structure

```
backtest/
├── __init__.py    # 빈 패키지 마커
├── cost_model.py  # CostModel + Trade — 거래 비용 계산
├── position_exit.py # 두 엔진 공용 R 목표·트레일링 청산 판정
└── engine.py      # BacktestEngine + PositionSizingEngine + 결과 데이터 클래스
```

## cost_model.py — CostModel

### 고정 상수

| 상수 | 값 | 설명 |
|---|---|---|
| `TRANSACTION_TAX_SELL` | 0.0018 | 코스피·코스닥 매도 거래세 |
| `TRANSACTION_TAX_ETF` | 0.0 | ETF 거래세 면제 |
| `BROKER_FEE_ROUNDTRIP` | 0.00015 | 편도 수수료 (매수+매도) |
| `SLIPPAGE_LARGE_CAP` | 0.0005 | 시총 ≥ 5천억 슬리피지 |
| `SLIPPAGE_SMALL_CAP` | 0.0015 | 시총 < 5천억 슬리피지 |
| `LARGE_CAP_THRESHOLD` | 5e11 | 대형주 시총 기준 (5천억 원) |

### `Trade` (dataclass)

`ticker, entry_price, exit_price, qty, is_etf=False, market_cap=0.0`

프로퍼티: `gross_pnl`, `trade_value`

### `CostModel`

```python
CostModel(broker_fee=0.00015, slippage_large=0.0005, slippage_small=0.0015, tax_sell=0.0018)
```

| 메서드 | 반환 | 설명 |
|---|---|---|
| `apply(trade)` | `float` | `net_pnl = gross_pnl - total_cost` |
| `sensitivity(trade, multiplier)` | `float` | 비용 × multiplier 시나리오 순손익 |
| `total_cost(trade)` | `float` | 총 거래 비용 (원) |

비용 항목: 브로커 수수료(편도) + 슬리피지(왕복) + 거래세(매도, ETF 면제)

## engine.py — BacktestEngine

### 포지션 사이징 상수

| 상수 | 값 |
|---|---|
| `ACCOUNT_RISK_PER_TRADE` | 0.005 (0.5%) |
| `MAX_SINGLE_EXPOSURE` | 0.10 (10%) |

### 데이터 클래스

| 클래스 | 주요 필드 |
|---|---|
| `TradeRecord` | ticker, entry/exit_date, entry/exit_price, qty, gross/net_pnl, exit_reason |
| `BacktestResult` | strategy_id, cagr, mdd, sharpe, gain_to_pain, win_rate, equity_curve, trade_list |

`exit_reason`: `"signal"` | `"strategy_exit"` | `"stop_loss"` |
`"trailing_stop"` | `"take_profit"` | `"end_of_period"`

### `PositionSizingEngine`

```python
calc_qty(equity, entry_price, stop_price) → int
```

수량 = `(equity × risk) / (entry_price - stop_price)`, 단일 종목 10% 상한 적용.

### `BacktestEngine`

```python
BacktestEngine(cost_model=None, initial_capital=100_000_000)
```

```python
run(strategy, params, data, universe=None, market_cap=0.0, is_etf=False) → BacktestResult
```

이벤트 루프 순서:
1. `generate_signals()` 호출
2. 날짜 순 반복: 손절/청산 체크 → 진입 체크
3. 지표 계산: CAGR, MDD, Sharpe, G2P, 승률

`BaseStrategy.position_exit_policy()`가 정책을 반환하면 두 엔진 모두
`position_exit.evaluate_position_exit()`로 R 목표·트레일링·전략 신호를 같은
우선순위로 판정한다. 기존 전략은 `None`이라 종전 신호 청산 동작을 유지한다.

**주의**: `data`가 비어있으면 `BacktestError` 발생.

## 의존성

```
maps.strategy.base    → BaseStrategy
maps.common.exceptions → BacktestError
```
