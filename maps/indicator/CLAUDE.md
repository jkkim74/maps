# indicator/

기술적 지표 계산 패키지. 현재는 추세 강도(TrendStrength)만 포함.

## Directory structure

```
indicator/
├── __init__.py        # 빈 패키지 마커
└── trend_strength.py  # TrendStrengthCalculator + TsScore + TsUniverseResult
```

## trend_strength.py

### 점수 산식 (기본 가중치)

| 지표 | 가중치 | 범위 | 설명 |
|---|---|---|---|
| MA20 대비 현재가 위치 | 40% | 0~40점 | `(last - ma20) / ma20 × 200 + 20` 클램프 |
| RSI(14) | 30% | 0~30점 | `(RSI - 30) / 40 × 30` 클램프, 데이터 부족 시 15점 |
| 거래량 vs 20일 평균 | 30% | 0~30점 | `(vol_ratio - 0.5) / 1.5 × 30` 클램프, 데이터 부족 시 15점 |

합계 0~100점 → S1~S5 버킷 분류 (`TREND_STRENGTH_BUCKETS` in `common/constants.py`).

### 버킷 분류

| 버킷 | 점수 구간 | 의미 |
|---|---|---|
| S1 | [0, 20) | 매우 약한 추세 — 진입 제외 대상 |
| S2 | [20, 40) | 약한 추세 |
| S3 | [40, 60) | 중립 |
| S4 | [60, 80) | 강한 추세 |
| S5 | [80, 100] | 매우 강한 추세 |

### 주요 클래스

#### `TsScore` (dataclass)

| 필드 | 타입 | 설명 |
|---|---|---|
| `ticker` | `str` | 종목 코드 |
| `score` | `float` | 0~100 점수 |
| `bucket` | `str` | S1~S5 |
| `bucket_label` | `str` | 한국어 레이블 |
| `above_ma20` | `bool` | MA20 위 여부 |
| `rsi14` | `float?` | RSI 값 |
| `volume_ratio` | `float?` | 거래량 비율 |
| `as_of` | `date` | 기준일 |

#### `TsUniverseResult` (dataclass)

| 속성/메서드 | 설명 |
|---|---|
| `scores` | `list[TsScore]` |
| `missing` | 데이터 부족 ticker 목록 |
| `bucket_counts` | `dict[str, int]` — 버킷별 종목 수 |
| `s1_excluded_count` | S1(진입 제외) 종목 수 |

#### `TrendStrengthCalculator`

```python
TrendStrengthCalculator(ma_period=20, rsi_period=14, vol_period=20, min_bars=100)
```

| 메서드 | 설명 |
|---|---|
| `score_one(ticker, ohlcv, as_of)` | 단일 종목 점수 계산. 데이터 부족 시 `None` 반환. |
| `score_universe(ohlcv_map, ref_date)` | 유니버스 전체 점수 계산. |

`ohlcv`: date 인덱스, 컬럼 `open, high, low, close, volume` 필수.

## 후보 스냅샷에서의 사용

`ops/scheduler.py`의 `_save_candidate_snapshot()`에서 종목별로 호출:

```
final_score = 0.6 × factor_score (거래대금) + 0.4 × trend_strength
```

## 의존성

```
maps.common.constants → TREND_STRENGTH_BUCKETS
```
