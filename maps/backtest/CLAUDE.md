# backtest/

이벤트 기반 백테스트 엔진과 거래 비용 모델 패키지.

## Directory structure

```
backtest/
├── __init__.py               # 빈 패키지 마커
├── cost_model.py             # CostModel + Trade — 거래 비용 계산
├── engine.py                 # BacktestEngine + PositionSizingEngine + 결과 데이터 클래스
├── position_exit.py          # 두 엔진 공용 R 목표·트레일링 청산 판정
├── portfolio_replay.py       # 다종목 포트폴리오 리플레이 (공유 현금·슬롯)
├── kostolany_driver.py       # 코스톨라니 vs 레거시 전략단위 실행 드라이버
└── kostolany_comparison.py   # 시나리오 × 모드 비교 리포트
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

## portfolio_replay.py — 다종목 리플레이

`PortfolioReplayEngine.run()` 이 공유 현금·슬롯 제약 아래 여러 종목을 동시에 굴린다
(`PortfolioConfig`, `PortfolioTrade`, `PortfolioResult`).

> ⚠️ 손절가는 원칙적으로 `strategy/live_rules.effective_stop_price()` 하나로만 구한다.
> **`_resolve_stop` 만 유일한 예외로 별도 구현을 유지한다** — 전략 신호의 `stop_price` 와
> 미등록 전략 폴백이라는 백테스트 전용 입력이 있기 때문이다. 다른 곳에 복제하지 않는다.

## kostolany_driver.py / kostolany_comparison.py — 전환 효과 검증

| 이름 | 설명 |
|---|---|
| `run_comparison()` | 시나리오 × 모드 전략단위 백테스트 실행 |
| `mode_strategy_ids()` | 모드 설정에 따라 돌릴 전략 ID 목록 |
| `KostolanyComparisonRunner` | 환경변수 오버라이드 구성·집계·리포트 (`ComparisonResult.to_text()`) |

레거시 대비 코스톨라니 전환의 CAGR·Sharpe·MDD 를 같은 시나리오에서 비교하기 위한
연구 도구다. 운영 파이프라인은 이걸 부르지 않는다.

## 의존성

```
maps.strategy.base    → BaseStrategy
maps.common.exceptions → BacktestError
```
