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

### 데이터 클래스

#### `RegimeResult`

| 필드/속성 | 설명 |
|---|---|
| `regime` | `RegimeLabel` |
| `weekly_trend` | `WeeklyTrendLabel` |
| `kospi_ts` | KOSPI 추세 강도 점수 (0~100, None 가능) |
| `assets` | `list[AssetTrendInfo]` |
| `entry_limit_ratio` (프로퍼티) | STRONG+PASS=1.0, MIXED+PASS=0.5, WEAK+PASS=0.25, *+FAIL=0.0 |

#### `AssetTrendInfo`

`name, direction("up"/"down"/"flat"), value, above_ma5w`

### 분석 자산군

KOSPI, KOSDAQ (pykrx) + S&P 500, NASDAQ, USD/KRW, 금(GC=F), WTI(CL=F), 구리(HG=F) (yfinance)

### 장세 분류 기준

- **STRONG**: 8개 자산 중 ≥ 70% 가 5주 이동평균 위
- **MIXED**: ≥ 40% 가 MA5W 위
- **WEAK**: < 40%

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
| `round_up_krx_price(price, market, security_type)` | 매수 지정가를 다음 호가 단위로 올림 |
| `parse_closed_dates(value)` | `MAPS_KRX_CLOSED_DATES` 파싱 (콤마 구분 YYYY-MM-DD) |

### 고정 비거래일

5월 1일(노동절), 12월 31일(연말 마감일). 한국 공휴일은 `holidays` 패키지로 확인.

### 호가 단위 (ETF·ETN·ELW 제외 → 5원 고정)

| 가격대 | 단위 |
|---|---|
| < 1,000 | 1 |
| 1,000~4,999 | 5 |
| 5,000~9,999 | 10 |
| 10,000~49,999 | 50 |
| 50,000~99,999 | 100 |
| KOSDAQ 100,000+ | 100 |
| KOSPI 100,000~499,999 | 500 |
| KOSPI 500,000+ | 1,000 |

## 의존성

```
pykrx   → 국내 지수 주봉 데이터
yfinance → 해외 지수 주봉 데이터
holidays → 한국 공휴일 목록 (optional, 미설치 시 경고)
maps.common.settings → get_settings()
```
