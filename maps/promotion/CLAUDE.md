# promotion/

전략 승격 게이트 패키지. 5단계 승격 결정 및 감사 로그 기록.

## Directory structure

```
promotion/
├── __init__.py  # 빈 패키지 마커
└── gate.py      # PromotionGate, PromotionStage, PromotionDecision
```

## gate.py

### 핵심 원칙

- **절대 `KeyError`로 죽으면 안 된다.** 알 수 없는 전략 ID 또는 누락 메트릭 → "fail with reason"으로 처리.
- 예외: `MOCK_CANDIDATE` 이상 단계에서 `STRATEGY_GROUP_MAP`에 없는 `strategy_id` → `UnknownStrategyError` 발생.

### `PromotionStage` (str Enum)

`RESEARCH` → `ALERT_ONLY` → `MOCK_CANDIDATE` → `LIVE_CANDIDATE` → `LIVE` (또는 `REJECTED`)

### `PromotionDecision` (dataclass)

| 필드 | 설명 |
|---|---|
| `strategy_id` | 전략 ID |
| `score` | Tradeability 점수 (0~100) |
| `current_stage` | 현재 단계 |
| `target_stage` | 승격 대상 단계 (실패 시 REJECTED) |
| `passed` | 승격 여부 |
| `reasons` | 실패 사유 목록 |

### `PromotionGate`

```python
PromotionGate(db: Session, weight_preset: dict | None = None)
```

기본 가중치: `WEIGHT_PRESETS["balanced"]` (robustness 0.30, risk 0.30, recovery 0.20, return 0.20)

#### `evaluate(strategy_id, metrics, current_stage, strategy_group=None) → PromotionDecision`

평가 순서:
1. **UnknownStrategyError 검사** (MOCK_CANDIDATE 이상 단계)
2. **즉시 실패 가드** — `is_cagr ≤ 0` 또는 `mock_sharpe ≤ 0`
3. **MC 한도 검증** — `abs(mc_mdd_p95) > ALLOWED_MDD[group]["mc_p95_limit"]`
4. **Live Small 진입 조건** — Mock 3개월 또는 동등 리플레이 검증 (MOCK_CANDIDATE → LIVE_CANDIDATE)
5. **점수 계산** — 가중치 합산 × 100
6. **임계값 비교** — 실패 사유 기록
7. **감사 로그** — `promotion_history` 테이블 기록

#### 단계별 임계값

| 현재 단계 | 임계값 | 목표 단계 |
|---|---|---|
| RESEARCH | 60 | MOCK_CANDIDATE |
| ALERT_ONLY | 60 | MOCK_CANDIDATE |
| MOCK_CANDIDATE | 75 | LIVE_CANDIDATE |
| LIVE_CANDIDATE | 75 | LIVE |

#### `metrics` 딕셔너리 필드

| 키 | 용도 | 범위 |
|---|---|---|
| `robustness` | Plateau 양수 비율 | 0~1 |
| `risk` | MC MDD 비율 역수 (`1 - mdd_p95/limit`) | 0~1 |
| `recovery` | WFA mean_g2p / 2.0 | 0~1 |
| `return` | WFA sharpe_mean / 2.0 | 0~1 |
| `mc_mdd_p95` | MC MDD 95분위 (절대값) | float |
| `mock_months` | Mock 거래 기간 (개월) | float |
| `replay_equivalent_passed` | 리플레이 검증 통과 | bool |
| `replay_trading_days` | 리플레이 거래일 수 | int |
| `is_cagr` | IS CAGR (즉시 실패 가드) | float |
| `mock_sharpe` | Mock Sharpe (즉시 실패 가드) | float |

## 의존성

```
maps.common.constants  → ALLOWED_MDD, STRATEGY_GROUP_MAP, TRADEABILITY_THRESHOLDS, WEIGHT_PRESETS
maps.common.exceptions → UnknownStrategyError
maps.common.models     → PromotionHistory
```
