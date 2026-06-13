# data_quality/

as-of-date 원칙 기반 유니버스 필터링 패키지.

## Directory structure

```
data_quality/
├── __init__.py        # 빈 패키지 마커
└── universe_filter.py # DataQualityFilter — 거래 가능 유니버스 생성기
```

## universe_filter.py

### 핵심 원칙

`generate(ref_date)` 는 `ref_date` 이후의 정보를 **절대 참조하지 않는다** (생존자 편향 / 미래 데이터 누출 방지).

### 주요 클래스

#### `DataQualityFilter`

```python
DataQualityFilter(db: Session, mode: str = "backtest")
```

`mode`: `"backtest"` (기본) | `"live"` — 라이브 모드에서는 당일 수집 데이터 기준.

| 메서드 | 반환 | 설명 |
|---|---|---|
| `generate(ref_date, candidates?)` | `UniverseResult` | ref_date 기준 거래 가능 유니버스 생성 |

필터 조건 (AND):
- 상장 종목 (`delisting_date is None` 또는 `> ref_date`)
- 거래정지 없음 (halt_periods 미포함)
- 관리종목 아님 (managed_periods 미포함)
- OHLCV 데이터 존재 (`has_adjusted_price` or `latest_ohlcv_date`)
- 최소 거래대금 기준 (20일 평균 거래대금)

#### `UniverseResult`

| 필드 | 타입 | 설명 |
|---|---|---|
| `ref_date` | `date` | 기준일 |
| `universe` | `list[Security]` | 거래 가능 종목 목록 |
| `rejected` | `list[Security]` | 필터링 탈락 종목 |
| `rejection_ratio` | `float` | 탈락 비율 |

## 의존성

```
maps.common.models   → UniverseQualityLog
maps.data.security_repo → Security, HaltPeriod, ManagedPeriod
```
