# strategy/

매매 전략 정의 패키지. 추상 베이스 클래스와 7개의 구체 전략 구현을 포함한다.

## Directory structure

```
strategy/
├── __init__.py             # 빈 패키지 마커
├── base.py                 # BaseStrategy — 추상 베이스 클래스
├── live_rules.py           # 전략별 실거래 손절 비율
├── pullback_v2.py          # PullbackV2Strategy
├── pullback_v3.py          # PullbackV3Strategy (주력)
├── ath_breakout_v1.py      # ATHBreakoutV1Strategy
├── ath_breakout_v2.py      # ATHBreakoutV2Strategy
├── donchian_v1.py          # DonchianV1Strategy
├── donchian_v2.py          # DonchianV2Strategy
└── multi_asset_trend_v1.py # MultiAssetTrendV1Strategy
```

## base.py — BaseStrategy (ABC)

모든 전략의 추상 베이스. 구체 클래스에서 반드시 구현해야 하는 인터페이스:

| 멤버 | 종류 | 설명 |
|---|---|---|
| `strategy_id` | 클래스 변수 | 고유 전략 ID (e.g. `"pullback_v3"`) |
| `strategy_group` | 클래스 변수 | `STRATEGY_GROUP_MAP` 참조 (e.g. `"pullback_short"`) |
| `generate_signals(data, params)` | 추상 메서드 | entry_signal, exit_signal, stop_price 컬럼을 추가해 반환 |
| `param_grid()` | 추상 메서드 | Plateau 그리드 탐색용 파라미터 조합 목록 |
| `default_params` | 추상 프로퍼티 | 기본 파라미터 딕셔너리 |
| `required_bars(params)` | 구체 메서드 | 신호 생성에 필요한 최소 OHLCV 바 수 (기본 1) |

`generate_signals()` 입력 DataFrame 최소 컬럼: `open, high, low, close, volume`.

## live_rules.py — 손절 비율

| 전략 ID | 손절 비율 |
|---|---|
| `pullback_v3` | 5% |
| `pullback_v2` | 6% |
| `ath_breakout_v1` | 10% |
| `ath_breakout_v2` | 12% |
| `multi_asset_trend_v1` | 8% |
| `donchian_v1` | 8% |
| `donchian_v2` | 10% |

`stop_loss_price(strategy_id, entry_price)` → `entry_price × (1 - stop_loss_pct)` 반환. 알 수 없는 전략 ID나 유효하지 않은 진입가에는 `None` 반환.

## 전략 그룹 매핑

| 전략 ID | 그룹 | 특징 |
|---|---|---|
| `pullback_v2`, `pullback_v3` | `pullback_short` | 단기 되돌림 매수 |
| `ath_breakout_v1`, `ath_breakout_v2` | `ath_outlier` | 신고가 돌파 |
| `donchian_v1`, `donchian_v2` | `donchian_research` | 돈치안 채널 |
| `multi_asset_trend_v1` | `multi_asset` | 다중 자산 추세 |

## 구체 전략 등록

`ops/scheduler.py`의 `_RUNNABLE_STRATEGIES` 딕셔너리에 등록된 전략만 일별 파이프라인에서 실행된다.

새 전략 추가 시 체크리스트:
1. `strategy/` 아래 구체 클래스 파일 생성
2. `live_rules.py`의 `_STOP_LOSS_PCTS`에 손절 비율 추가
3. `common/constants.py`의 `STRATEGY_GROUP_MAP`에 전략 ID → 그룹 매핑 추가
4. `ops/scheduler.py`의 `_RUNNABLE_STRATEGIES`에 등록

## 의존성

```
maps.common.constants → STRATEGY_GROUP_MAP
maps.backtest.engine  → BacktestEngine (generate_signals 호출)
```
