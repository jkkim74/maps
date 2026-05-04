**MAPS 프로그램 설계서 v2.6.3**

MAPS — Market-Adaptive Profit Management System

**v2.6.3 — Implementation Readiness (구현 사양 통합본)**

| **항목** | **내용** |
| --- | --- |
| 문서 유형 | 프로그램 설계서 |
| 버전 | v2.6.3 (v2.6.2 + 구현 착수 준비 — ChatGPT 개발 검토 반영) |
| 핵심 추가 | DataCollector 클래스, as-of-date 유니버스 생성기, BrokerAdapter 인터페이스, MockBroker, RiskManager Kill Switch, 전략 기반 클래스, 폴더 구조, 테스트 케이스 |
| 대상 | 국내 주식 + ETF + 글로벌 자산군 추세 지표 |
| 운영 원칙 | 검증 체계가 먼저, 주문 기능은 마지막 |

*주의: 본 문서는 투자 수익을 보장하지 않으며 실거래 전 관련 법규와 증권사 API 약관 확인이 필수다.*

# **1. 시스템 아키텍처 (v2.6.3 변경)**

v2.6.3은 v2.6.2 파이프라인 위에 DataCollector 구체화, as-of-date 유니버스 생성기, BrokerAdapter 추상 계층, MockBroker, RiskManager Kill Switch를 추가한다.

DataCollector [v2.6.3: KRX API + 증권사 API 수집기]

→ DataQualityFilter [v2.6.3: as-of-date 생성기로 재정의]

→ MultiAssetTrendEngine / MarketRegimeEngine

→ FactorScreener / PullbackTiming / ATHBreakoutScreener

→ TrendStrengthEngine [5단계 분류]

→ WeeklyTrendConfirmation [× MarketRegime 매트릭스]

→ TradeScoringEngine

→ CostModel [거래세/수수료/슬리피지]

→ RobustnessTester

├─ ParameterPlateauTester

├─ WalkForwardAnalyzer [4조건 AND]

├─ MonteCarloSequenceTester

├─ BlockBootstrapTester

├─ CrossMarketTester

└─ StrategyComparisonEngine [Tradeability 프리셋]

→ PromotionGate [mc\_within\_limit 등 전수 검사]

→ EntryGate / ExitEngine

→ PositionSizingEngine

→ RiskManager [v2.6.3: Kill Switch 추가]

→ OrderManager

└─ BrokerAdapter [v2.6.3: 추상 인터페이스]

├─ MockBroker [v2.6.3: Phase 2~4 전용]

└─ KISBroker / KioomBroker [v2.6.3: Phase 5]

→ LiveBacktestMonitor

# **2. 프로젝트 폴더 구조 (v2.6.3 확정)**

maps/

common/

constants.py # RobustnessConstants, WEIGHT\_PRESETS 등

db.py # SQLAlchemy engine, Session

exceptions.py # DuplicateOrderError, KillSwitchError 등

data\_quality/

universe\_filter.py # DataQualityFilter (as-of-date 생성기)

data/

collector.py # DataCollector (KRX API + 증권사)

krx\_adapter.py # KRX Open API 어댑터

security\_repo.py # security\_metadata CRUD

strategy/

base.py # BaseStrategy 추상 클래스

pullback\_v3.py # 파일럿 전략 구현

ath\_outlier.py

backtest/

engine.py # BacktestEngine

cost\_model.py # CostModel

validation/

plateau.py # ParameterPlateauTester

walk\_forward.py # WalkForwardAnalyzer (4조건)

monte\_carlo.py # MonteCarloSequenceTester

block\_bootstrap.py # BlockBootstrapTester

cross\_market.py # CrossMarketTester

promotion/

gate.py # PromotionGate

execution/

broker\_adapter.py # BrokerAdapter 추상 클래스

mock\_broker.py # MockBroker

kis\_broker.py # KIS 어댑터 (Phase 5)

order\_manager.py # OrderManager

risk/

manager.py # RiskManager + Kill Switch

dashboard/

strategy\_compare.py # StrategyComparisonDashboard

scheduler/

jobs.py # APScheduler 일정 등록

tests/

test\_data\_quality.py

test\_walk\_forward.py

test\_promotion\_gate.py

test\_cost\_model.py

test\_mock\_broker.py

test\_risk\_manager.py

alembic/

versions/

0001\_baseline.py

main.py # FastAPI 앱

requirements.txt

.env # MAPS\_DB\_URL, KRX\_API\_KEY 등

# **3. 신규 및 변경 모듈 전체 목록**

| **파일** | **클래스** | **역할** | **v2.6.3 변경** |
| --- | --- | --- | --- |
| data/collector.py | DataCollector | KRX API + 증권사 API 일별 수집 | 신규 |
| data/krx\_adapter.py | KRXAdapter | KRX Open API 인증/호출 래퍼 | 신규 |
| data\_quality/universe\_filter.py | DataQualityFilter | as-of-date 유니버스 생성기 | 재설계 |
| strategy/base.py | BaseStrategy | 진입/청산/파라미터 추상 클래스 | 신규 |
| strategy/pullback\_v3.py | PullbackV3Strategy | 파일럿 전략 구현 | 신규 |
| execution/broker\_adapter.py | BrokerAdapter | 증권사 교환 가능 추상 인터페이스 | 신규 |
| execution/mock\_broker.py | MockBroker | 실주문 없는 검증용 브로커 | 신규 |
| execution/order\_manager.py | OrderManager | BrokerAdapter 기반 주문 관리 | BrokerAdapter 연동 |
| risk/manager.py | RiskManager | 한도 관리 + Kill Switch | Kill Switch 추가 |
| common/exceptions.py | 각종 Exception | 시스템 예외 정의 | 신규 |

# **4. DataCollector 설계 (신규)**

KRX Open API를 기본 원천으로 하고, 수정주가 등 KRX가 커버하지 못하는 항목은 증권사 API로 보완한다. 모든 수집은 비동기 재시도 로직을 포함한다.

# data/collector.py

class DataCollector:

def \_\_init\_\_(self, krx: KRXAdapter, broker: Optional[BrokerAdapter] = None):

self.krx = krx

self.broker = broker # 보완 소스 (Phase 4 이후)

def collect\_daily(self, ref\_date: date) -> CollectionResult:

"""일별 OHLCV + 종목 메타 수집"""

ohlcv = self.krx.get\_ohlcv(ref\_date)

meta = self.krx.get\_security\_meta(ref\_date)

halts = self.krx.get\_halt\_list(ref\_date)

mgmt = self.krx.get\_managed\_list(ref\_date)

# 수정주가가 누락된 경우 broker에서 보완

if self.broker and not ohlcv.has\_adjusted:

ohlcv = self.broker.get\_adjusted\_ohlcv(ref\_date)

return CollectionResult(ohlcv=ohlcv, meta=meta, halts=halts, mgmt=mgmt)

def collect\_range(self, start: date, end: date) -> List[CollectionResult]:

"""기간 배치 수집 (백테스트 준비용)"""

results = []

for d in business\_days(start, end):

try:

results.append(self.collect\_daily(d))

except APIError as e:

log\_collection\_failure(d, e)

# 단일 날짜 실패는 경고만 → 수집 계속

return results

| **수집 항목** | **KRX API 커버** | **보완 필요** | **우선순위** |
| --- | --- | --- | --- |
| 일별 OHLCV (원시 종가) | O | — | P0 |
| 수정주가 | 확인 필요 | 증권사 API 또는 유료 데이터 | P0 |
| 종목 메타 (상장일/폐지일/시장구분) | O | — | P0 |
| 거래정지 이력 | O (공시 기반) | 정지 기간 시작/종료 정밀도 확인 필요 | P0 |
| 관리종목 이력 | 일부 제공 | 지정/해제일 상세 확인 필요 | P1 |
| ETF/ETN/SPAC 구분 | O | — | P1 |
| 실시간 체결가 | X | 증권사 WebSocket (Phase 4) | P4 |

# **5. DataQualityFilter as-of-date 생성기 (재설계)**

v2.6.2의 DataQualityFilter는 apply(universe) 형태의 단순 필터였다. v2.6.3은 generate(ref\_date) 형태의 기준일 관점 유니버스 생성기로 재설계한다. WFA 각 fold마다 독립적인 유니버스를 생성하여 look-ahead bias를 완전히 차단한다.

# data\_quality/universe\_filter.py

class DataQualityFilter:

"""as-of-date 유니버스 생성기 (v2.6.3 재설계)"""

MIN\_LISTING\_DAYS = 100

MIN\_TURNOVER\_KRW = {'KOSPI': 5e8, 'KOSDAQ': 3e8}

EXCLUDED\_TYPES = ['SPAC']

def \_\_init\_\_(self, mode: str = 'backtest'):

self.mode = mode # 'backtest' | 'live'

def generate(self, ref\_date: date, candidates: List[Security]) -> UniverseResult:

"""

ref\_date 기준으로 실제 매수 가능했던 종목 집합을 반환.

ref\_date 이후의 정보(폐지, 정지)는 참조하지 않는다.

"""

kept, rejected = [], []

for stock in candidates:

reason = self.\_check\_as\_of(stock, ref\_date)

(kept if reason is None else rejected).append(

stock if reason is None else {'ticker': stock.ticker, 'reason': reason}

)

self.\_log(ref\_date, len(candidates), len(kept), rejected)

return UniverseResult(universe=kept, rejected=rejected)

def \_check\_as\_of(self, stock: Security, ref\_date: date) -> Optional[str]:

# 1. 수정주가

if not stock.has\_adjusted\_price\_as\_of(ref\_date):

return 'unadjusted\_price'

# 2. 상장 여부 (ref\_date 기준)

if stock.listing\_date > ref\_date:

return 'not\_listed\_yet'

# 3. 상장폐지

# - backtest: 폐지일 이전까지 포함 (생존자 편향 방지)

# - live: 이미 폐지된 종목 제외

if stock.delisting\_date is not None:

if self.mode == 'live' and stock.delisting\_date <= ref\_date:

return 'delisted'

if self.mode == 'backtest' and stock.delisting\_date < ref\_date:

return 'delisted\_before\_ref'

# 4. 거래정지 (ref\_date 당일 정지 중인지 조회)

if stock.is\_halted\_on(ref\_date): # 정지 기간 이력 테이블 조회

return 'trading\_halted'

# 5. 관리종목 (ref\_date에 지정 중인지)

if stock.is\_managed\_on(ref\_date):

return 'managed\_stock'

# 6. 거래대금 (ref\_date 기준 과거 20일 평균)

min\_t = self.MIN\_TURNOVER\_KRW.get(stock.market, 5e8)

if stock.avg\_turnover\_20d\_as\_of(ref\_date) < min\_t:

return 'low\_turnover'

# 7. 신규상장

if (ref\_date - stock.listing\_date).days < self.MIN\_LISTING\_DAYS:

return 'recently\_listed'

# 8. 제외 유형

if stock.security\_type in self.EXCLUDED\_TYPES:

return 'excluded\_type'

return None

def \_log(self, ref\_date, total, kept, rejected):

# universe\_quality\_log에 기록

ratio = 1 - kept/total if total > 0 else 0

if ratio >= 0.05:

send\_alert(f'[DQ] {ref\_date} 거부율 {ratio:.1%} >= 5%')

## **5.1 WFA 연동 방식**

# validation/walk\_forward.py (WFA fold별 유니버스 생성 예시)

dq\_filter = DataQualityFilter(mode='backtest')

for fold\_idx, (is\_start, is\_end, oos\_start, oos\_end) in enumerate(folds):

# IS 구간 시작일 기준 유니버스 생성

is\_universe = dq\_filter.generate(is\_start, all\_candidates).universe

# OOS 구간 시작일 기준 유니버스 생성 (독립적으로)

oos\_universe = dq\_filter.generate(oos\_start, all\_candidates).universe

# IS/OOS가 서로 다른 종목 집합이어도 정상

is\_result = backtest(strategy, best\_param, is\_start, is\_end, is\_universe, cost\_model)

oos\_result = backtest(strategy, best\_param, oos\_start, oos\_end, oos\_universe, cost\_model)

# **6. BaseStrategy 추상 클래스 (신규)**

모든 전략은 BaseStrategy를 상속하여 진입/청산/파라미터 범위를 정의한다. BacktestEngine과 PromotionGate가 이 인터페이스만 알면 된다.

# strategy/base.py

from abc import ABC, abstractmethod

class BaseStrategy(ABC):

strategy\_id: str # 'pullback\_v3'

strategy\_group: str # 'pullback\_short' (STRATEGY\_GROUP\_MAP 참조)

@abstractmethod

def generate\_signals(self, data: DataFrame, params: dict) -> DataFrame:

"""

Returns DataFrame with columns:

entry\_signal (bool), exit\_signal (bool), stop\_price (float)

"""

@abstractmethod

def param\_grid(self) -> List[dict]:

"""Plateau 그리드 탐색 공간 반환"""

@property

@abstractmethod

def default\_params(self) -> dict:

"""기본 파라미터"""

# strategy/pullback\_v3.py (파일럿 전략)

class PullbackV3Strategy(BaseStrategy):

strategy\_id = 'pullback\_v3'

strategy\_group = 'pullback\_short'

def generate\_signals(self, data: DataFrame, params: dict) -> DataFrame:

rsi\_threshold = params.get('rsi\_threshold', 10)

ma\_short = params.get('ma\_short', 5)

ma\_long = params.get('ma\_long', 20)

data['ma\_s'] = data['close'].rolling(ma\_short).mean()

data['ma\_l'] = data['close'].rolling(ma\_long).mean()

data['rsi2'] = compute\_rsi(data['close'], period=2)

entry = (

(data['ma\_s'] > data['ma\_l']) & # 단기 추세 확인

(data['rsi2'] < rsi\_threshold) & # 눌림목

(data['low'] < data['low'].shift(1)) # 추가 눌림

)

exit\_ = (data['close'] >= data['ma\_s']) & (data.index != data.index[0])

stop = data['close'] \* 0.95 # 진입가 대비 -5%

data['entry\_signal'] = entry

data['exit\_signal'] = exit\_

data['stop\_price'] = stop

return data

def param\_grid(self) -> List[dict]:

return [

{'rsi\_threshold': rsi, 'ma\_short': ms, 'ma\_long': ml}

for rsi in [5, 10, 15]

for ms in [5]

for ml in [20, 30, 40]

]

@property

def default\_params(self) -> dict:

return {'rsi\_threshold': 10, 'ma\_short': 5, 'ma\_long': 20}

# **7. BrokerAdapter 인터페이스 + MockBroker (신규)**

## **7.1 BrokerAdapter 추상 클래스**

# execution/broker\_adapter.py

from abc import ABC, abstractmethod

class BrokerAdapter(ABC):

"""증권사 교환 가능 추상 인터페이스"""

@abstractmethod

def place\_order(self, order: Order) -> OrderResult: ...

@abstractmethod

def cancel\_order(self, order\_id: str) -> bool: ...

@abstractmethod

def get\_position(self, ticker: str) -> Position: ...

@abstractmethod

def get\_account\_balance(self) -> AccountBalance: ...

@abstractmethod

def is\_market\_open(self) -> bool: ...

def subscribe\_realtime(self, ticker: str, callback) -> None:

"""실시간 구독 (WebSocket). Phase 4 이전은 구현 불필요."""

raise NotImplementedError('subscribe\_realtime is Phase 4+')

## **7.2 MockBroker 구현**

# execution/mock\_broker.py

class MockBroker(BrokerAdapter):

"""실주문 없는 검증용 브로커 (Phase 2~4 전용)"""

def \_\_init\_\_(self, initial\_cash: float = 100\_000\_000):

self.cash = initial\_cash

self.positions = {} # ticker -> Position

self.orders = {} # order\_id -> Order

self.order\_log = [] # 전체 주문 이력

self.\_kill = False # Kill Switch

def place\_order(self, order: Order) -> OrderResult:

if self.\_kill:

raise KillSwitchError('Kill Switch active')

if order.ticker in self.orders: # 중복 주문 방지

raise DuplicateOrderError(order.ticker)

# 즉시 체결 시뮬레이션 (Mock: 시장가 기준)

fill\_price = order.price or self.\_get\_market\_price(order.ticker)

cost = fill\_price \* order.qty \* (1 + self.\_commission\_rate(order))

if order.side == 'BUY' and cost > self.cash:

return OrderResult(status='REJECTED', reason='insufficient\_cash')

self.\_apply\_fill(order, fill\_price)

self.order\_log.append({'order': order, 'fill\_price': fill\_price})

return OrderResult(status='FILLED', fill\_price=fill\_price, qty=order.qty)

def cancel\_order(self, order\_id: str) -> bool:

if order\_id in self.orders:

del self.orders[order\_id]

return True

return False

def get\_position(self, ticker: str) -> Position:

return self.positions.get(ticker, Position(ticker=ticker, qty=0))

def get\_account\_balance(self) -> AccountBalance:

total = self.cash + sum(p.market\_value for p in self.positions.values())

return AccountBalance(cash=self.cash, total=total, positions=list(self.positions.values()))

def is\_market\_open(self) -> bool:

now = datetime.now(tz=KST)

return now.weekday() < 5 and time(9, 0) <= now.time() <= time(15, 30)

def eod\_cleanup(self):

"""장 종료 후 미체결 주문 전량 취소"""

for order\_id in list(self.orders.keys()):

self.cancel\_order(order\_id)

def activate\_kill\_switch(self):

self.\_kill = True

def deactivate\_kill\_switch(self, approved\_by: str):

log\_kill\_switch\_deactivation(approved\_by)

self.\_kill = False

# **8. RiskManager Kill Switch 확장 (신규)**

v2.6.2의 LiveBacktestMonitor 자동 대응에 더해, RiskManager가 일 손실 한도, 연속 주문 실패, API 장애, 포지션 불일치를 감지해 Kill Switch를 자동 발동한다.

# risk/manager.py

class RiskManager:

DAILY\_LOSS\_LIMIT = 0.015 # 1.5%

MAX\_CONSEC\_FAILURES = 5

API\_TIMEOUT\_SEC = 10

def \_\_init\_\_(self, broker: BrokerAdapter):

self.broker = broker

self.\_consec\_failures = 0

def check\_before\_order(self, order: Order, account: AccountBalance) -> None:

"""주문 전 위험 체크. 위반 시 KillSwitchError 발생."""

# 1. 일 손실 한도

daily\_loss = self.\_calc\_daily\_loss(account)

if daily\_loss >= self.DAILY\_LOSS\_LIMIT:

self.\_trigger\_kill('daily\_loss\_limit', daily\_loss)

# 2. 포지션 불일치 감지

if self.\_detect\_reconciliation\_mismatch():

self.\_trigger\_kill('position\_mismatch', None)

# 3. 단일 종목 노출 한도

if order.estimated\_exposure > 0.10:

raise ExposureCapError(order.ticker)

def on\_order\_failure(self, error: Exception) -> None:

"""주문 실패 시 호출. 연속 실패 횟수 누적."""

self.\_consec\_failures += 1

if self.\_consec\_failures >= self.MAX\_CONSEC\_FAILURES:

self.\_trigger\_kill('consecutive\_failures', self.\_consec\_failures)

def on\_order\_success(self) -> None:

self.\_consec\_failures = 0

def \_trigger\_kill(self, reason: str, value) -> None:

log\_kill\_switch\_trigger(reason, value)

send\_alert(f'[KILL] {reason} = {value}')

self.broker.activate\_kill\_switch()

raise KillSwitchError(f'{reason}: {value}')

def deactivate(self, approved\_by: str) -> None:

"""관리자 승인 후 Kill Switch 해제"""

self.broker.deactivate\_kill\_switch(approved\_by)

self.\_consec\_failures = 0

log\_kill\_switch\_deactivation(approved\_by)

## **8.1 Kill Switch 트리거 요약**

| **트리거** | **임계** | **자동 여부** | **재개 조건** |
| --- | --- | --- | --- |
| 일 손실 한도 | 계좌 1.5% | 자동 | 사용자 승인 |
| 연속 주문 실패 | 5회 | 자동 | 사용자 승인 |
| API 응답 지연 | 10초 | 자동 | 정상 복구 확인 후 승인 |
| 포지션 불일치 | 감지 즉시 | 자동 | 수동 reconciliation 후 승인 |
| 사용자 수동 | SCR-05 버튼 | 수동 | 관리자 승인 필수 |
| MDD 한도 도달 | 허용 MDD (전략군별) | 자동 | 비중 50% 축소 후 승인 |

# **9. 공통 예외 클래스 (신규)**

# common/exceptions.py

class MAPSError(Exception): """MAPS 기본 예외"""

class KillSwitchError(MAPSError): """Kill Switch 발동 시 주문 차단"""

class DuplicateOrderError(MAPSError): """중복 주문 시도"""

class ExposureCapError(MAPSError): """단일 종목 노출 한도 초과"""

class ResearchStrategyError(MAPSError): """Research 전략 자동주문 시도"""

class DataQualityError(MAPSError): """데이터 품질 기준 미충족"""

class PromotionGateError(MAPSError): """PromotionGate fail with reason"""

class BrokerAdapterError(MAPSError): """브로커 어댑터 오류"""

class UnknownStrategyError(MAPSError): """STRATEGY\_GROUP\_MAP에 없는 전략 ID"""

# **10. DB 스키마 추가 (v2.6.3)**

v2.6.2에서 정의한 스키마에 v2.6.3이 추가하는 테이블. 모든 표기는 SQLite 기준. PostgreSQL 변환은 설계서 §9(v2.6.2) 매핑을 따른다.

-- 데이터 수집 이력

CREATE TABLE collection\_log (

id INTEGER PRIMARY KEY AUTOINCREMENT,

ref\_date DATE NOT NULL,

source TEXT, -- 'krx' | 'broker' | 'manual'

status TEXT, -- 'success' | 'partial' | 'failed'

items INTEGER, -- 수집 종목 수

note TEXT,

created\_at TIMESTAMP DEFAULT CURRENT\_TIMESTAMP

);

-- 주문 이력 (Mock + Live 공통)

CREATE TABLE order\_log (

id INTEGER PRIMARY KEY AUTOINCREMENT,

order\_id TEXT UNIQUE NOT NULL,

strategy\_id TEXT,

ticker TEXT,

side TEXT, -- 'BUY' | 'SELL'

qty INTEGER,

order\_price REAL,

fill\_price REAL,

fill\_qty INTEGER,

status TEXT, -- 'FILLED' | 'CANCELLED' | 'REJECTED' | 'PARTIAL'

broker TEXT, -- 'mock' | 'kis' | 'kiwoom'

mode TEXT, -- 'mock' | 'live\_small' | 'live'

created\_at TIMESTAMP DEFAULT CURRENT\_TIMESTAMP

);

-- Kill Switch 이력

CREATE TABLE kill\_switch\_log (

id INTEGER PRIMARY KEY AUTOINCREMENT,

event\_type TEXT, -- 'trigger' | 'deactivate'

reason TEXT, -- 'daily\_loss\_limit' | 'consecutive\_failures' | ...

value TEXT, -- 트리거 당시 측정값

approved\_by TEXT, -- 재개 승인자 (deactivate 시)

created\_at TIMESTAMP DEFAULT CURRENT\_TIMESTAMP

);

-- 전략 파라미터 이력 (어떤 파라미터로 실거래했는지 기록)

CREATE TABLE strategy\_param\_log (

id INTEGER PRIMARY KEY AUTOINCREMENT,

strategy\_id TEXT,

params\_json TEXT,

effective\_at DATE,

reason TEXT, -- 'initial' | 'wfa\_update' | 'manual'

created\_at TIMESTAMP DEFAULT CURRENT\_TIMESTAMP

);

# **11. 스케줄러 (v2.6.3 보강)**

단일 서버 개인 운용에는 APScheduler로 시작한다. WFA/MC 병렬 처리 배치 작업이 늘어나면 Celery + Redis로 전환을 검토한다.

| **시각/조건** | **작업** | **비고** |
| --- | --- | --- |
| 매일 08:35 | DataCollector 일별 수집 + DataQualityFilter 갱신 | DQ 거부율 5% 초과 시 알림 |
| 매일 08:40 | TrendStrengthScore 5단계 계산 | 결측 처리 포함 |
| 매일 16:15 | StrategyComparisonDashboard 갱신 | Tradeability 재계산 |
| 매일 16:30 | LiveBacktestMonitor + RiskManager 일 손실 점검 | Kill Switch 자동 발동 가능 |
| 매주 금요일 16:20 | WeeklyTrendConfirmation 갱신 | MarketRegime 매트릭스 재산정 |
| 매주 일요일 02:00 | PromotionGate 자동 평가 | 승격 후보 큐 등록 |
| 월 1일 03:00 | RobustnessTester 풀 스위트 (Plateau+WFA+MC+BBoot) | SLA: 전략당 3시간 이내 |
| 분기 1일 04:00 | CostModel 가정 리뷰 (실측 vs 가정) | 편차 15% 이상 시 사용자 알림 |

# **12. 테스트 케이스 (v2.6.3 추가)**

| **테스트** | **검증 내용** | **위험도** |
| --- | --- | --- |
| test\_dq\_generate\_as\_of\_date | generate(ref\_date) 호출 시 ref\_date 이후 폐지 종목 포함하지 않음 | HIGH |
| test\_dq\_backtest\_include\_delisted | 백테스트 모드: 폐지 종목이 폐지일 이전까지 유니버스에 포함됨 | HIGH |
| test\_dq\_wfa\_no\_lookahead | WFA fold별 ref\_date에서 미래 정보 미참조 확인 | HIGH |
| test\_dq\_listing\_date\_check | listing\_date > ref\_date 종목은 유니버스에 포함 안 됨 | HIGH |
| test\_strategy\_generate\_signals | pullback\_v3 진입 신호가 3조건 AND로 생성됨 | HIGH |
| test\_strategy\_param\_grid | param\_grid() 반환값이 예상 조합 수와 일치 | MEDIUM |
| test\_mock\_broker\_place\_order | 정상 주문 FILLED 처리 | HIGH |
| test\_mock\_broker\_duplicate\_order | 동일 종목 중복 주문 시 DuplicateOrderError | HIGH |
| test\_mock\_broker\_kill\_switch | Kill Switch 활성 시 KillSwitchError 발생 | HIGH |
| test\_mock\_broker\_eod\_cleanup | 장 종료 후 미체결 주문 전량 취소 | HIGH |
| test\_risk\_daily\_loss\_trigger | 일 손실 1.5% 도달 시 Kill Switch 자동 발동 | HIGH |
| test\_risk\_consec\_failure\_trigger | 연속 5회 실패 시 Kill Switch 자동 발동 | HIGH |
| test\_risk\_deactivate\_requires\_approval | approved\_by 없이 deactivate 불가 | HIGH |
| test\_unknown\_strategy\_id | STRATEGY\_GROUP\_MAP에 없는 ID → UnknownStrategyError | MEDIUM |
| test\_research\_strategy\_order\_block | stage='alert\_only' 전략 주문 → ResearchStrategyError | HIGH |
| test\_collector\_adjusted\_price\_fallback | KRX 수정주가 없으면 broker로 폴백 | MEDIUM |
| test\_broker\_adapter\_mock\_interface | MockBroker가 BrokerAdapter 인터페이스 완전 구현 | MEDIUM |

# **13. 구현 착수 체크리스트 (v2.6.3)**

| **단계** | **작업** | **완료 기준** |
| --- | --- | --- |
| PRE-0 | KRX Open API 인증키 + 활용 신청 | test API 호출 성공 |
| PRE-1 | 수정주가 커버리지 확인 → 보완 소스 결정 | 6개월 이상 수정주가 수집 성공 |
| PRE-2 | 증권사 1개 선택 + 개발 API 신청 | 개발 서버 테스트 환경 확보 |
| PRE-3 | 알고리즘 계좌 규정 확인 (법적) | 증권사 공식 확인 문서 보관 |
| P0.1 | maps/ 폴더 구조 생성 + common/constants.py | 모든 import 오류 없음 |
| P0.2 | common/db.py + Alembic baseline | alembic upgrade head 성공 |
| P0.3 | pytest 프레임워크 + conftest.py | pytest 실행 성공 |
| P1.1 | DataCollector + KRXAdapter | collect\_daily() 단위 테스트 통과 |
| P1.2 | security\_metadata 테이블 + CRUD | 종목 메타 1000건 적재 성공 |
| P1.3 | DataQualityFilter as-of-date 생성기 | test\_dq\_generate\_as\_of\_date 통과 |
| P2.1 | CostModel | test\_cost\_model 전부 통과 |
| P2.2 | PullbackV3Strategy + BacktestEngine | 10년 백테스트 30분 이내 완료 |
| P3.1 | WalkForwardAnalyzer 4조건 | test\_wfa\_negative\_mean\_blocked 통과 |
| P3.2 | ParameterPlateauTester + MC + BBoot | 테스트 전부 통과 |
| P3.3 | PromotionGate | test\_promotion\_mc\_within\_limit 통과 |
| P4.1 | MockBroker + OrderManager | test\_mock\_broker 전부 통과 |
| P4.2 | RiskManager Kill Switch | test\_risk 전부 통과 |
| P4.3 | Mock 3개월 시뮬레이션 | PromotionGate Mock→Live Small 통과 |

# **14. 운영상 주의사항 (v2.6.2 계승 + v2.6.3 추가)**

- 실계좌 주문 전에 알고리즘 계좌 규정을 반드시 확인한다. 이는 기술 완성도와 무관한 법적 선결 조건이다.
- Phase 4 이전까지는 BrokerAdapter로 MockBroker만 사용한다. KISBroker/KioomBroker 구현체는 Phase 5 이전에 실계좌와 연결하지 않는다.
- DataQualityFilter generate() 호출은 WFA fold별로 독립적으로 수행한다. 동일 fold 내에서 IS와 OOS의 유니버스가 달라도 정상이다.
- Kill Switch는 신규 진입 차단까지 자동 적용. 보유 포지션 강제 청산은 반드시 사용자 명시적 승인 후에만 실행한다.
- audit 로그 (promotion\_history, universe\_quality\_log, order\_log, kill\_switch\_log)는 Day 1부터 스키마가 존재해야 한다. 나중에 붙이면 초기 데이터가 유실된다.
- DataCollector가 수정주가를 제대로 가져오는지 Phase 1 첫 주에 반드시 확인한다. 수정주가가 없으면 모든 백테스트의 신호가 왜곡된다.
- APScheduler는 단일 서버 개인 운용에 충분하지만, WFA/MC 병렬 배치가 3개 전략 이상 동시 실행되면 Celery 전환을 검토한다.

**MAPS v2.6.3 | Implementation Readiness (구현 사양 통합본)**
