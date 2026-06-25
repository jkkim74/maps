---
name: screening-scoring-logic
description: 종목 스크리닝 복합 점수 산정 로직, 전략별 진입조건 검증 공식
metadata:
  type: reference
---

## TrendStrength 점수 (maps/indicator/trend_strength.py 동일 공식)

| 지표 | 가중치 | 범위 | 산식 |
|---|---|---|---|
| MA20 대비 현재가 | 40% | 0~40 | `(last-ma20)/ma20 × 200 + 20` 클램프 |
| RSI(14) | 30% | 0~30 | `(rsi-30)/40 × 30` 클램프, 부족시 15 |
| 거래량/20d평균 | 30% | 0~30 | `(vol_ratio-0.5)/1.5 × 30` 클램프, 부족시 15 |

## 스크리닝 복합 점수 (4-factor)

```
final_score = liq_score × 0.30 + trend_score × 0.30 + strat_score × 0.25 + ctx_score × 0.15
```

- **liq_score**: log-scale 정규화, 하한=50억, 상한=100조
- **trend_score**: RSI 구간 점수(50에서 피크) × 0.5 + MA20 위치(상단=70/하단=30) × 0.5
- **strat_score**: 전략별 진입조건 점수 (아래 참고)
- **ctx_score**: 국면 컨텍스트 보정 (ATR 페널티 + 대형주 보너스 + vol_ratio 보너스)

## 전략별 진입조건 점수 (pullback_v3 기준)

```python
pullback_score = 0
if above_ma20: pullback_score += 30          # 추세 방향 확인
if 35 <= rsi14 <= 65: pullback_score += 30   # 눌림목 RSI 구간
if -10 <= pct_from_20d_high <= -1:           # 20일 고점 대비 1~10% 조정
    pullback_score += 40
elif -15 <= pct_from_20d_high < -10:
    pullback_score += 20
```

## 국면별 컨텍스트 조정 (ctx_score)

- base: 50점
- ATR > 12%: -20점 (고변동 종목 페널티)
- ATR 8~12%: -5점 (중간)
- avg_tv >= 1000억원: +20점 (대형주 보너스, KOSPI 선호 국면)
- vol_ratio >= 1.2: +10점 (거래량 동반 상승)
- 최종 클램프: [0, 100]

## 전략-진입조건 매핑 요약

| 전략 | 주요 조건 |
|---|---|
| pullback_v3/v2 | MA20 상단, RSI 35~65, 20일고 대비 -1~-10% |
| multi_asset_trend_v1 | MA20+MA60 상단, RSI > 55 |
| donchian_v2 | 52주 고점 근접 (-5% 이내) |
| contrarian_quality_accumulation_v1 | RSI < 35, MA20 하단, 퀄리티 펀더멘털 |

## 관리종목/거래정지 필터

현재 구현:
1. 해당 날짜 거래량 == 0 → 거래정지 추정, 제외
2. `MAPS_HALTED_TICKERS` 환경변수 manual override
3. DART 관리종목 API 미연동 (Phase 5 예정) → `MAPS_MANAGED_TICKERS` manual override
