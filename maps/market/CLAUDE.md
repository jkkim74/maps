# market/

시장 분석 패키지. 장세(Regime) 분류와 KRX 거래 규칙을 제공한다.

## Directory structure

```
market/
├── __init__.py       # 빈 패키지 마커
├── regime.py         # MarketRegimeAnalyzer — 장세 × 주간추세 매트릭스
└── trading_rules.py  # KRX 거래일 판단 + 호가 단위 계산
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

## trading_rules.py — KRX 거래 규칙

### 함수

| 함수 | 설명 |
|---|---|
| `is_krx_closed_date(target, extra_closed_dates=())` | 주말·고정 휴장일·`holidays.KR` 기준 비거래일 판단 |
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
