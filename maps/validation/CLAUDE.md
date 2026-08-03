# validation/

전략 검증 3종 세트: Walk-Forward Analysis, Parameter Plateau, Monte Carlo.

## Directory structure

```
validation/
├── __init__.py      # 빈 패키지 마커
├── walk_forward.py  # WalkForwardAnalyzer — 5-fold WFA
├── plateau.py       # ParameterPlateauTester — 파라미터 견고성 그리드 탐색
└── monte_carlo.py   # MonteCarloValidator — MDD p95 시뮬레이션
```

## walk_forward.py — WalkForwardAnalyzer

5-fold rolling window 방식으로 전략의 In-Sample / Out-of-Sample 성과를 검증한다.

### 통과 조건 (AND 4개)

| 조건 | 기준 | 상수 |
|---|---|---|
| `sharpe_mean > 0` | 평균 Sharpe 양수 | `WF_SHARPE_MEAN_MIN = 0.0` |
| `negative_folds ≤ 1` | 5개 fold 중 음수 fold 최대 1개 | `WF_NEGATIVE_FOLD_MAX = 1` |
| `mean_g2p ≥ 0.6` **이고 유한** | OOS/IS G2P 비율 평균 | `WF_OOS_IS_G2P_MIN = 0.6` |
| `no_trade_folds ≤ 1` | OOS 무거래 fold 최대 1개 | `WF_NO_TRADE_FOLD_MAX = 1` |

> **비유한 `mean_g2p` 는 실패로 처리한다.** `inf < 0.6` 도 `NaN < 0.6` 도 False라
> 그냥 두면 조건이 조용히 통과한다 (2026-08-03 발견, 8전략 중 4개가 이 상태였음).
> 근본 원인은 `engine._gain_to_pain` 이 무거래와 무손실을 모두 `inf` 로 낸 것이며,
> 지금은 무거래=0.0 / 무손실=`GAIN_TO_PAIN_CAP`(3.0) 으로 구분한다.

> `std/|mean| ≤ 0.5` 조건은 제거됨. 사유: 임계값 근거 없음, 나머지 3개 조건이 이미 일관성·과적합을 충분히 통제.

### 주요 클래스

#### `WalkForwardResult` (dataclass)

| 필드 | 설명 |
|---|---|
| `sharpe_mean` | fold별 OOS Sharpe 평균 |
| `sharpe_std` | Sharpe 표준편차 |
| `negative_folds` | 음수 Sharpe fold 수 |
| `mean_g2p` | OOS/IS G2P 비율 평균 |
| `passed` | 통과 여부 |
| `fail_reasons` | 실패 사유 목록 |
| `folds` | `list[WalkForwardFold]` — fold별 상세 |

#### `WalkForwardAnalyzer`

```python
run(strategy, data, param_grid) → WalkForwardResult
```

## plateau.py — ParameterPlateauTester

파라미터 그리드에서 양수 수익 비율로 전략의 견고성(robustness)을 평가한다.

### 등급 기준

| 등급 | 양수 비율 | 의미 |
|---|---|---|
| A | ≥ 80% | 견고 |
| B | ≥ 60% | 양호 |
| C | ≥ 40% | 보통 |
| D | ≥ 20% | 취약 |
| F | < 20% | 실패 |

### 주요 클래스

#### `PlateauResult` (dataclass)

| 필드 | 설명 |
|---|---|
| `grade` | `"robust"` \| `"moderate"` \| `"fragile"` |
| `score` | 0~100 점수 |
| `passing_neighbors` | 양수 수익 파라미터 조합 수 |
| `best_combo` | 최고 성과 파라미터 딕셔너리 |

#### `ParameterPlateauTester`

```python
run(backtest_rows, param_keys) → PlateauResult
```

`backtest_rows`: `[{"param1": v, ..., "sharpe": f, "mdd": f}, ...]` 형식.

## monte_carlo.py — MonteCarloValidator

일별 수익률 시퀀스를 무작위 순열(Sequence Shuffle)로 1,000회 시뮬레이션해 MDD p95를 검증한다.

### 주요 클래스

#### `MonteCarloResult` (dataclass)

| 필드 | 설명 |
|---|---|
| `strategy_id` | 전략 ID |
| `strategy_group` | 전략군 |
| `mdd_p95` | 시뮬레이션 MDD 95분위수 |
| `mdd_limit` | 허용 MDD 한도 (`ALLOWED_MDD[group]["mc_p95_limit"]`) |
| `passed` | `abs(mdd_p95) ≤ mdd_limit` |
| `n_simulations` | 시뮬레이션 횟수 (기본 1,000) |

#### `MonteCarloValidator`

```python
MonteCarloValidator(n_simulations=1000)
validate(strategy_id, strategy_group, daily_returns) → MonteCarloResult
```

`daily_returns`가 30개 미만이면 `ValidationError` 발생.

## 의존성

```
maps.common.constants  → ALLOWED_MDD, WF_* 상수
maps.common.exceptions → ValidationError
maps.strategy.base     → BaseStrategy
maps.backtest.engine   → BacktestEngine
```
