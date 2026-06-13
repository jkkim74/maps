# dashboard/

대시보드 데이터 집계 패키지. 전략 비교 스냅샷 생성 및 순위 산출.

## Directory structure

```
dashboard/
├── __init__.py          # 빈 패키지 마커
└── strategy_compare.py  # StrategyCompare + StrategySnapshot
```

## strategy_compare.py

### `StrategySnapshot` (dataclass)

대시보드 표시용 전략 종합 스냅샷.

| 필드 | 타입 | 설명 |
|---|---|---|
| `strategy_id` | `str` | 전략 ID |
| `strategy_group` | `str` | 전략군 |
| `stage` | `PromotionStage` | 현재 승격 단계 |
| `promotion_score` | `float` | Tradeability 점수 |
| `sharpe_ratio` | `float` | 백테스트 Sharpe |
| `max_drawdown` | `float` | 백테스트 MDD |
| `mc_mdd_p95` | `float` | MC MDD 95분위 |
| `wf_passed` | `bool` | WFA 통과 여부 |
| `wf_fail_reasons` | `list[str]` | WFA 실패 사유 |
| `promotion_fail_reasons` | `list[str]` | 승격 실패 사유 |

### `StrategyCompare`

| 메서드 | 설명 |
|---|---|
| `build_snapshot(strategy_id, strategy_group, backtest, wf_result, mc_result, promotion)` | 개별 전략 종합 스냅샷 생성 |
| `rank(snapshots)` | `promotion_score` 기준 내림차순 정렬 |
| `filter_by_stage(snapshots, stage)` | 특정 승격 단계 전략만 필터링 |

## 의존성

```
maps.backtest.engine     → BacktestResult
maps.promotion.gate      → PromotionDecision, PromotionStage
maps.validation.monte_carlo  → MonteCarloResult
maps.validation.walk_forward → WalkForwardResult
```
