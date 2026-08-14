# data/

KRX OHLCV 및 종목 메타데이터 수집·조회 패키지.

## Directory structure

```
data/
├── __init__.py            # 빈 패키지 마커
├── collector.py           # DataCollector — 일별/기간 수집 오케스트레이터
├── krx_adapter.py         # KRXAdapterBase / KRXAdapter / MockKRXAdapter + 데이터 클래스
├── krx_auth.py            # KRX 로그인 회로차단기 (계정 잠금 방지)
├── naver_fundamental.py   # Naver 모바일 API 펀더멘털 수집 (KRX MDC 대체)
├── fundamental_repo.py    # 펀더멘털 as-of-date 조회 + 안전마진 provider
├── ohlcv_repo.py          # HistoricalOHLCVRepository — OHLCV 조회 전용 레포
└── security_repo.py       # SecurityRepository — 종목 메타 조회 전용 레포
```

## 🔴 krx_auth.py — pykrx 를 쓰기 전에 반드시 통과할 관문

**pykrx 를 호출하는 모든 경로는 먼저 `ensure_krx_login_guard()` 를 부른다.**
pykrx 는 요청마다 재로그인을 시도해서, 자격증명이 만료되면 재시도가 누적돼 KRX 계정이
잠긴다(2026-07-27 실제 사고, 하루 158회).

| 함수 | 설명 |
|---|---|
| `ensure_krx_login_guard()` | 사용 직전 호출하는 얇은 래퍼 (설치 실패도 삼킨다) |
| `install_krx_login_guard()` | 로그인 진입점에 회로차단기 설치 (멱등) |
| `get_breaker()` / `set_breaker()` | 전역 차단기 조회 / 교체(테스트 전용) |
| `krx_login_status()` | 운영 관측용 상태 |
| `describe_error_code()` | KRX 오류 코드 → 사람이 읽는 문자열 |

`KRXLoginBreaker` 는 연속 실패 카운터 + 지수 백오프다. 임계치·쿨다운은
`MAPS_KRX_LOGIN_*` 설정을 읽는다(숫자는 여기 옮겨 적지 않는다). 치명 코드는 1회에 즉시 차단.

## krx_adapter.py — 데이터 클래스 및 어댑터

### 데이터 클래스

| 클래스 | 설명 |
|---|---|
| `OHLCVData` | 일별 OHLCV (date, ticker, open, high, low, close, volume, adj_close) |
| `FundamentalData` | 일별 펀더멘털 (pykrx `get_market_fundamental` 기준) |
| `InvestorFlowData` | 종목별 외국인·기관·개인 순매수 금액 (정확한 기준일) |
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
| `get_sector_classifications(ref_date)` | 업종 분류 |
| `get_fundamental(ref_date)` | `list[FundamentalData]` |
| `get_investor_flows(ref_date)` | `list[InvestorFlowData]` — 점수 실측 커버리지의 입력 |

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
| `collect_investor_flow_history(...)` | 수급 백필 → `investor_flow_snapshot` |
| `collect_fundamental_history(...)` / `collect_fundamental_snapshot(...)` | 펀더멘털 백필·스냅샷 |

> 수급은 **장 마감 후에야** 채워진다. 장중에 부르면 비어 있는 게 정상이고, 그 사실이
> `ops/score_readiness.py` 의 커버리지 미달로 이어져 신규 매수가 막힌다.
>
> 수급 수집 실패는 예외를 삼키고 넘어간다 — **OHLCV 는 살려야** 하기 때문이다. 다만 0건이면
> `collection_log.status='partial'` + `CollectionResult.investor_flow_count=0` +
> `logger.error` 로 드러난다. `data_collection` 잡 details 에도 실린다. 조용히 `success` 로
>끝나면 다음 거래일 신규 매수가 전량 막히는데도 아무 신호가 없다.

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

추가 조회 메서드: `top_tickers_by_trading_value()`, `list_tickers_on_date()`,
`list_tickers_with_counts()`, `recent_dataframes()`.

> ⚠️ `recent_dataframes()` 는 전체 이력을 정렬하지 말고 **필요한 캘린더 구간만** 읽는다.
> 586만 행 전수 정렬로 시장폭 계산이 장시간 걸린 적이 있다(2026-08-07).

## security_repo.py — SecurityRepository

`security_metadata` 테이블 CRUD. `DataQualityFilter` 에서 사용한다. `Security` 도메인 객체가
as-of-date 판정(`is_halted_on`, `is_managed_on`, `has_live_ohlcv_as_of`,
`avg_turnover_20d_as_of` 등)을 제공하며, **`ref_date` 이후 정보를 보면 안 된다.**

## fundamental_repo.py / naver_fundamental.py

| 이름 | 설명 |
|---|---|
| `FundamentalRepository` | `security_fundamental` as-of-date 조회 (`get_as_of`, `historical_avg`, `historical_band`) |
| `FundamentalValuationProvider` | 안전마진·가치목표가용 데이터 provider (`ai/valuation_margin.py`, `strategy/price_calculator.py` 에 주입) |
| `PriceFundamentals` | `KostolanyPriceCalculator` 입력 묶음 |
| `NaverFundamentalAdapter` | KRX MDC 가 막힐 때 쓰는 대체 수집 소스 (`get_one`, `get_many`) |

> `security_fundamental` 은 **운영 PostgreSQL 에만** 채워져 있다. 로컬에서 안전마진 게이트가
> 빈손으로 나오면 데이터를 복사해 와야 한다.

## 의존성

```
pykrx                  → 시세·펀더멘털·수급 (호출 전 ensure_krx_login_guard())
maps.common.models     → HistoricalOHLCV, SecurityMetadata, CollectionLog,
                         SecurityFundamental, InvestorFlowSnapshot
maps.common.exceptions → DataCollectionError
```
