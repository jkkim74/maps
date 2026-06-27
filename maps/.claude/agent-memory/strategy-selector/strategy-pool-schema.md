---
name: strategy-pool-schema
description: config/strategy_pool.yaml 실제 경로, 스키마 구조, 각 전략의 그룹·MDD·regime_fit 실측값
metadata:
  type: reference
---

파일 경로: `D:\workspace\maps\maps\config\strategy_pool.yaml`

NOTE: 워킹 디렉토리는 `D:\workspace\maps\maps\maps`이지만 strategy_pool.yaml은 한 단계 위인 `D:\workspace\maps\maps\config\`에 위치한다. Glob으로 `**/strategy_pool.yaml`을 `D:\workspace\maps` 루트에서 검색해야 찾을 수 있음.

파일이 2026-06-23 기준 존재함.

## 스키마 구조

각 전략 항목 필드:
- `id`: 전략 식별자 (CLAUDE.md STRATEGY_GROUP_MAP과 일치)
- `group`: pullback_short / ath_outlier / multi_asset / donchian_research / kostolany
- `mc_p95_limit`: 그룹 MDD 한도 (CLAUDE.md 권위값 따름)
- `stop_loss_pct`: 전략별 손절 비율
- `regime_fit`: strong/mixed/weak 각각 high/medium/low 등급
- `historical_mdd`: 과거 최대낙폭 (보수적 seed 값, 실측 교체 예정)
- `win_rate`: 승률
- `trend_dependent`: 추세 의존 여부 (bool)

## 전략별 실측값 (2026-06-23 스냅샷)

| id | group | mc_p95_limit | historical_mdd | win_rate | regime_fit(strong/mixed/weak) | trend_dependent |
|---|---|---|---|---|---|---|
| pullback_v3 | pullback_short | 0.18 | 0.14 | 0.56 | medium/high/medium | false |
| pullback_v2 | pullback_short | 0.18 | 0.15 | 0.54 | medium/high/low | false |
| ath_breakout_v2 | ath_outlier | 0.35 | 0.28 | 0.50 | high/low/low | true |
| ath_breakout_v1 | ath_outlier | 0.35 | 0.30 | 0.48 | high/low/low | true |
| multi_asset_trend_v1 | multi_asset | 0.22 | 0.16 | 0.53 | medium/high/medium | true |
| donchian_v2 | donchian_research | 0.30 | 0.22 | 0.49 | high/medium/low | true |
| donchian_v1 | donchian_research | 0.30 | 0.24 | 0.47 | high/medium/low | true |
| contrarian_quality_accumulation_v1 | kostolany | 0.28 | 0.20 | 0.55 | low/medium/high | false |

## 주요 패턴

- mixed regime: pullback_v3/v2, multi_asset_trend_v1 → high fit. ath_breakout은 low fit(제외).
- strong regime: ath_breakout, donchian → high fit.
- weak regime: contrarian_quality_accumulation_v1 → high fit; pullback_v3 medium.
- kostolany 그룹(contrarian_quality_accumulation_v1)의 mc_p95_limit는 yaml에 0.28로 명시 (portfolio_total 한도와 동일).
