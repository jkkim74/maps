# market/

시장 분석 패키지. 장세(Regime) 분류, 시장폭, 업종 선정, 실측 피드, KRX 거래 규칙을 제공한다.

## Directory structure

```
market/
├── __init__.py         # 빈 패키지 마커
├── breadth.py          # 시장폭 — MA 위 종목 비율
├── feeds.py            # 실측 유동성·심리 피드 (수급 + 뉴스 심리)
├── regime.py           # MarketRegimeAnalyzer — 장세 × 주간추세 매트릭스
├── regime_history.py   # 히스테리시스 — buffer band·전일 유지·floor 2일 확인
├── sector_selector.py  # 업종 강도·국면별 업종 선정
└── trading_rules.py    # KRX 거래일 판단 + 호가 단위 계산
```

## regime.py — 장세 분석

### Enum

| Enum | 값 |
|---|---|
| `RegimeLabel` | `STRONG`, `MIXED`, `WEAK` |
| `WeeklyTrendLabel` | `PASS`, `FAIL` |
| `VolRegimeLabel` | `LOW`, `NORMAL`, `HIGH` |

### 데이터 클래스

#### `RegimeResult`

| 필드/속성 | 설명 |
|---|---|
| `regime` | `RegimeLabel` |
| `weekly_trend` | `WeeklyTrendLabel` |
| `vol_regime` | `VolRegimeLabel` (기본 NORMAL) — KOSPI 20주 실현변동성 기반 |
| `kospi_ts` | KOSPI 추세 강도 점수 (0~100, None 가능) |
| `assets` | `list[AssetTrendInfo]` |
| `entry_limit_ratio` (프로퍼티) | vol_regime=HIGH 시 1단계 하향; WEAK+HIGH+PASS=0.0 (완전 중단) |

#### entry_limit_ratio 매트릭스 (weekly_trend=FAIL → 항상 0.0)

| regime / vol_regime | LOW/NORMAL | HIGH |
|---|---|---|
| STRONG | 1.0 | 0.5 |
| MIXED | 0.5 | 0.25 |
| WEAK | 0.25 | 0.0 |

#### `AssetTrendInfo`

`name, direction("up"/"down"/"flat"), value, above_ma5w`

### 분석 자산군

KOSPI, KOSDAQ (pykrx) + S&P 500, NASDAQ, USD/KRW, 금(GC=F), WTI(CL=F), 구리(HG=F) (yfinance)

### 장세 분류 기준

- **STRONG**: 8개 자산 중 ≥ 70% 가 5주 이동평균 위
- **MIXED**: ≥ 40% 가 MA5W 위
- **WEAK**: < 40%

투표 후 보정 2종 (모두 `market_regime_log`에 플래그 기록):
- **KOSPI 플로어** (weak→mixed 상향): KOSPI가 5·10주선 모두 상회 + weekly PASS → `floor_applied`
- **Korea weak guard** (mixed→weak 하향, `regime_history.apply_hysteresis`에서 적용):
  KOSPI 5·10주선 모두 하회 + (추세강도 ≤ `MAPS_KOREA_WEAK_TS_THRESHOLD`(35) 또는
  breadth WEAK) → `korea_weak_guard_applied`. `MAPS_KOREA_WEAK_GUARD_ENABLED`(기본 true).
  buffer band 유지보다 우선한다.

종합점수(kostolany composite)는 **실측 팩터만 가중 재정규화**한다 — 피드 미연결
팩터(유동성·심리)는 점수에서 제외되고 reason에 `미측정 제외: ...`로 표기된다.

### WeeklyTrend 판단

KOSPI 40주 데이터 기반: `MA10W > MA20W > MA40W` → PASS, 아니면 FAIL

### 주요 클래스

#### `MarketRegimeAnalyzer`

```python
MarketRegimeAnalyzer(provider=None, override_regime=None, override_trend=None)
```

`analyze() → RegimeResult`

- override 설정 시 즉시 반환 (pykrx/yfinance 호출 없음)
- provider 없으면 MIXED+PASS stub 반환
- `MAPS_MARKET_REGIME_OVERRIDE` / `MAPS_WEEKLY_TREND_OVERRIDE` 환경변수로 제어

#### `CombinedWeeklyProvider`

`get_weekly_closes(asset_name, n_weeks) → list[float]`

pykrx(국내) + yfinance(해외) 통합 주봉 종가 제공.

#### `create_regime_analyzer(settings) → MarketRegimeAnalyzer`

설정 기반 팩토리. override가 "auto"이면 `CombinedWeeklyProvider` 사용.

## feeds.py — 실측 피드 (없으면 없는 대로 남긴다)

| 이름 | 설명 |
|---|---|
| `DatabaseKostolanyDataProvider.enrich()` | **정확한 기준일**의 영속 피드로 종합 점수 입력을 채운다 |
| `collect_market_news_sentiment()` | 하루치 뉴스 심리 스냅샷 upsert. 실패는 명시적 관측으로 남긴다 |

뉴스는 Naver 검색 → Bedrock 구조화 심리 점수 순서다. Bedrock 이 돌려준 긍정·중립·부정
건수 합이 기사 수와 다르면 **재정규화**해서 맞추고, 그래도 불가능하면 실패로 기록한다.

> ⚠️ 미연결·실패 피드를 **중립 50 으로 채우지 않는다.** 빠진 채로 남겨야
> `ops/score_readiness.py` 가 커버리지 미달을 보고 신규 매수를 막는다.
> 채워 넣는 순간 게이트가 조용히 열린다.
>
> 🔴 **`investor_flow_snapshot` 의 NULL 은 수집 실패가 아니다.** pykrx 가 그 종목·투자자
> 유형을 결과에 넣지 않았다는 뜻이고(우선주·저유동성 종목에 흔하다), 집계에서 **0 으로
> 더한다**. `_flow_observations()` 가 `None` 을 돌려주는 경우는 **그 날짜 행이 0건**이거나
> **세 필드 중 하나가 전 행에서 결측**일 때뿐이다.
> "행마다 하나라도 NULL 이면 그날 포기" 로 되돌리지 말 것 — 2026-08-13 실측 기준 2,622행
> 중 기관 NULL 이 538행(20.5%)이라 **매일 발동해 커버리지가 0.65 에 고정되고 신규 매수가
> 전면 차단된다**(2026-08-12~14 실제 사고). 같은 의미론을 `ops/scheduler.py` 의
> `_build_ticker_contexts` 수급 합산도 공유한다.
>
> ⚠️ 뉴스 검색은 **Naver API Hub**(`naverapihub.apigw.ntruss.com`, `X-NCP-APIGW-*` 헤더)를
> 쓴다. 설정 변수명은 `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET` 이지만 값은 **NCP API Hub 키**다.
> 구 `openapi.naver.com` 키는 동작하지 않는다.

## breadth.py / regime_history.py / sector_selector.py

| 모듈 | 함수·클래스 | 설명 |
|---|---|---|
| `breadth.py` | `compute_pct_above_ma()`, `classify_breadth()` | `ref_date` 기준 MA 위 종목 비율 → 시장폭 라벨 |
| `regime_history.py` | `apply_hysteresis()`, `latest_applied_regime()` | raw 판정에 히스테리시스·Korea weak guard 적용 후 이력 upsert |
| `sector_selector.py` | `SectorSelector.select_strong_sectors()`, `SectorRegimeSelector.select()` | 최근 N거래일 모멘텀 상위 업종 + 국면별 선호/제외 |

`apply_hysteresis()` 가 **최종 장세의 정본**이다. `MarketRegimeAnalyzer.analyze()` 결과를
그대로 쓰면 하루 단위로 라벨이 튀고 buffer band 가 무시된다.

## trading_rules.py — KRX 거래 규칙

### 함수

| 함수 | 설명 |
|---|---|
| `is_krx_closed_date(target, extra_closed_dates=())` | 주말·고정 휴장일·`holidays.KR` 기준 비거래일 판단 |
| `previous_trading_day(ref_date)` | `ref_date` 직전 거래일 |
| `trading_days_ago(ref_date, n)` | KRX 거래일 기준 n일 전 (픽 신선도 계산에 쓰인다) |
| `krx_tick_size(price, market="KOSPI", security_type="stock")` | KRX 호가 단위 반환 |
| `round_up_krx_price(price, market, security_type)` | **매수 지정가**를 다음 호가로 올림 |
| `round_down_krx_price(price, market, security_type)` | **손절가**를 아래 호가로 내림 |
| `round_to_krx_tick(price, market, security_type)` | 가까운 호가로 반올림 (표시·목표가) |
| `parse_closed_dates(value)` | `MAPS_KRX_CLOSED_DATES` 파싱 (콤마 구분 YYYY-MM-DD) |

> 손절가에는 **반드시 내림**을 쓴다. 반올림하면 32,487 이 32,500 으로 올라가
> 손절이 조여지고, 백테스트·사이징이 가정한 손절폭보다 좁아진다.
> `live_rules.effective_stop_price()` 가 내부에서 적용하므로 호출부는 따로 정렬하지 않는다.

### 고정 비거래일

5월 1일(노동절), 12월 31일(연말 마감일). 한국 공휴일은 `holidays` 패키지로 확인.

### 호가 단위 (ETF·ETN·ELW 는 5원 고정)

2023-01-25 개편으로 **코스피·코스닥 주식의 호가단위가 통일**됐다. `market` 인자는
주식에서 더 이상 분기하지 않는다(호출부 시그니처 호환성 유지용).

| 가격대 | 단위 |
|---|---|
| < 2,000 | 1 |
| 2,000~4,999 | 5 |
| 5,000~19,999 | 10 |
| 20,000~49,999 | 50 |
| 50,000~199,999 | 100 |
| 200,000~499,999 | 500 |
| 500,000 이상 | 1,000 |

> ⚠️ 이 표는 2026-07-31 에 코드와 맞췄다. 그전까지 세 구간(1,000~1,999,
> 10,000~19,999, 100,000~199,999)이 개편 전 값으로 남아 있었다.
> 값이 필요하면 표를 믿지 말고 `krx_tick_size()` 를 호출할 것.

## 의존성

```
pykrx   → 국내 지수 주봉 데이터
yfinance → 해외 지수 주봉 데이터
holidays → 한국 공휴일 목록 (optional, 미설치 시 경고)
maps.common.settings → get_settings()
```
