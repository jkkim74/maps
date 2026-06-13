# data/

KRX OHLCV 및 종목 메타데이터 수집·조회 패키지.

## Directory structure

```
data/
├── __init__.py        # 빈 패키지 마커
├── collector.py       # DataCollector — 일별/기간 OHLCV 수집 오케스트레이터
├── krx_adapter.py     # KRXAdapterBase / KRXAdapter / MockKRXAdapter + 데이터 클래스
├── ohlcv_repo.py      # HistoricalOHLCVRepository — OHLCV 조회 전용 레포
└── security_repo.py   # SecurityRepository — 종목 메타 조회 전용 레포
```

## krx_adapter.py — 데이터 클래스 및 어댑터

### 데이터 클래스

| 클래스 | 설명 |
|---|---|
| `OHLCVData` | 일별 OHLCV (date, ticker, open, high, low, close, volume, adj_close) |
| `SecurityMeta` | 종목 메타 (ticker, name, market, security_type, listing_date, delisting_date) |
| `CollectionResult` | 수집 결과 묶음 (ref_date, ohlcv, meta, halts, managed) |

### 어댑터 계층

```
KRXAdapterBase (ABC)
├── KRXAdapter          — pykrx 기반 실 데이터 (API 키 불필요)
└── MockKRXAdapter      — 테스트용 더미 어댑터
```

#### `KRXAdapterBase` 추상 메서드

| 메서드 | 반환 |
|---|---|
| `get_ohlcv(ref_date)` | `list[OHLCVData]` |
| `get_security_meta(ref_date)` | `list[SecurityMeta]` |
| `get_halt_list(ref_date)` | `list[str]` — 거래정지 ticker |
| `get_managed_list(ref_date)` | `list[str]` — 관리종목 ticker |

#### `KRXAdapter` 구현 주의사항

- `get_halt_list`: 거래량 0 heuristic + `MAPS_HALTED_TICKERS` 환경변수 override. Phase 5에서 KRX 공시 API로 교체 예정.
- `get_managed_list`: pykrx 미지원. `MAPS_MANAGED_TICKERS` override만 반영.
- OHLCV 컬럼명이 버전에 따라 한글/영문이 혼재 → `_OHLCV_COL_MAP`으로 한글 통일.

#### `MockKRXAdapter` 주입 메서드

| 메서드 | 설명 |
|---|---|
| `set_halts(date, tickers)` | 날짜별 거래정지 override |
| `set_managed(date, tickers)` | 날짜별 관리종목 override |
| `set_meta(ticker, meta)` | 종목 메타 override |

## collector.py — DataCollector

```python
DataCollector(krx: KRXAdapterBase, db: Session, broker=None)
```

| 메서드 | 설명 |
|---|---|
| `collect_daily(ref_date)` | 하루치 OHLCV + 메타 수집 → DB 적재. 수정주가 누락 시 broker 폴백(Phase 4 미구현). |
| `collect_range(start, end)` | 기간 배치 수집. 단일 날짜 실패는 경고만 하고 계속 진행. |
| `collect_ohlcv_history(start, end)` | 검증/WFA/MC용 OHLCV 백필. 메타 재수집 없이 가격만 upsert. |

내부 헬퍼:

| 메서드 | 설명 |
|---|---|
| `_upsert_meta(meta_list, adjusted_by_ticker)` | `security_metadata` upsert |
| `_upsert_ohlcv(rows)` | `historical_ohlcv` upsert — 유효성 검사(양수 OHLCV) 포함 |
| `_write_log(ref_date, status, items, note, source)` | `collection_log` 감사 기록 |

## ohlcv_repo.py — HistoricalOHLCVRepository

`historical_ohlcv` 테이블 조회 전용. 백테스트·검증·WFA에서 사용.

| 주요 메서드 | 설명 |
|---|---|
| `to_dataframe(ticker, end?, start?)` | date 인덱스 OHLCV DataFrame 반환 |
| `list_tickers_with_history(end, min_bars)` | 최소 `min_bars` 일치 이상의 ticker 목록 |

## security_repo.py — SecurityRepository

`security_metadata` 테이블 조회 전용. `DataQualityFilter`에서 사용.

## 의존성

```
maps.common.models   → HistoricalOHLCV, SecurityMetadata, CollectionLog
maps.common.exceptions → DataCollectionError
```
