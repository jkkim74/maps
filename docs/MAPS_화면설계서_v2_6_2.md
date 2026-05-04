MAPS

Screen Design

v2.6.2 / CODE-SPEC SYNC

표지

000화면 인덱스

메인 워크플로

SCR-01대시보드 홈

SCR-02전략 관리

SCR-03장세/팩터

SCR-04종목 후보

SCR-05주문/체결

SCR-06리스크/모니터

SCR-07백테스트

v2.6.1 신규

SCR-08견고성NEW

SCR-09추세 강도NEW

SCR-10연구 전략NEW

SCR-11WFA 리포트NEW

SCR-12비용 민감도NEW

SCR-13실거래 모니터NEW

v2.6.2 신규

SCR-14데이터 품질NEW

부록

A. 권한 매트릭스

B. 화면 전이

C. 디자인 시스템

MAPS · Screen Design Document v2.6.2

# 파라미터가 살아남는 자리에서 *전략은 비로소 화면이 된다*

MAPS v2.6.2의 14개 화면은 단일 최적값이 아니라 견고성 영역을 시각화한다. 모든 화면은 정량 임계값, 단계별 승격 KPI, 데이터 품질 게이트에 묶여 있다.

버전

v2.6.2

화면 수

14 (기존 7 + 신규 7)

대상 사용자

개인 운용자 / 연구자

디바이스

데스크탑 (1280+) 우선

SCR-01EXISTING

대시보드 홈

계좌·전략·장세·자동대응 상태를 한 화면에 요약. 모든 워크플로의 진입점.
SCR-02EXISTING

전략 관리

전략 라이프사이클 (Research → Alert → Mock → Live Small → Live) + PromotionGate.
SCR-03EXISTING

장세 · 팩터 분석

MarketRegime × WeeklyTrend 매트릭스. 진입 정책 자동 적용.
SCR-04EXISTING

종목 후보 풀

Factor + TrendStrength 게이트 통과 종목. S5 자동 제외 가시화.
SCR-05EXISTING

주문 · 체결

실시간 주문 큐 + 체결 슬리피지 실측. CostModel 가정과 자동 비교.
SCR-06EXISTING

리스크 · 모니터

전략군별 위험·노출 한도 관제. Exposure Cap·총위험 실시간 게이지.
SCR-07EXISTING

백테스트 콘솔

단일 전략 백테스트 + Plateau/Walk-Forward/MC 자동 동반 실행.
SCR-08NEW

Trend Robustness

Plateau heatmap, MC vs Block Bootstrap MDD 분포, Tradeability 점수.
SCR-09NEW

TrendStrength Monitor

S1~S5 5단계 분포 + 주봉 확인 + 결측 종목 가시화.
SCR-10NEW

Research Strategies

Donchian/Bollinger/Pyramiding 신호 + PromotionGate 통과 현황.
SCR-11NEW

Walk-Forward Report

5-fold WFA fold별 Sharpe·Gain-to-Pain. 변동계수 기준 통과 판정.
SCR-12NEW

Cost Sensitivity

슬리피지 ±50% 시나리오 Net CAGR 변화. 거래비용 가정 거버넌스.
SCR-13NEW

Live Monitor

실측 MDD/슬리피지 vs 가정. 자동 대응 발동 이력 + 사용자 승인 큐.
SCR-14v2.6.2

Data Quality

DataQualityFilter 거부 종목 현황. 생존자 편향 차단 확인. 6개 기준별 거부 통계 + as-of-date 유니버스 검증.

SCR-01
EXISTING · 추정 사양

## 대시보드 홈

계좌 자산곡선, 활성 전략 상태, 장세 지표, 자동 대응 알림을 한 화면에 묶는 진입점. 사용자는 평일 16:30 이후 이 화면만 확인하면 그날 시스템이 정상 작동했는지 즉시 판단할 수 있어야 한다.

와이어프레임

maps.local / dashboard

기간
2026-04-01 ~ 2026-05-02자동 대응
정상 운영마지막 갱신
2026-05-02 16:31:04
새로고침
SCR-13 모니터

총 자산

₩142,308,500

+1.2% MoM

YTD CAGR

+18.4%

목표 20% 대비

현재 MDD

−4.8%

한도 28% 대비

Sharpe (1Y)

1.42

목표 1.0 ↑

활성 전략

4 / 7

2 Live · 2 Mock

계좌 자산곡선

EQUITY CURVE · 12M · with MDD shading

전략별 기여도

| 전략 | 기여 | 상태 |
| --- | --- | --- |
| Pullback | +8.2% | LIVE |
| ATH Trend | +6.4% | LIVE |
| Multi-Asset | +3.1% | MOCK |
| Donchian R | ±0.0% | ALERT |

장세 / 자산군 추세

MULTI-ASSET TREND · 5M MA

최근 알림 (24h)

|  |  |  |
| --- | --- | --- |
| WARN | 슬리피지 실측 +28% (소형주) | 14:22 |
| INFO | WeeklyTrend 갱신 완료 | 16:20 |
| PASS | 일일 검증 정상 종료 | 16:31 |

컴포넌트 사양

| ID | 타입 | 데이터 소스 | 액션 |
| --- | --- | --- | --- |
| HEAD-TOOLBAR | Toolbar | system\_status, last\_run\_log | 새로고침 / SCR-13 이동 |
| KPI-PORTFOLIO | KPI Card × 5 | account\_summary view | 클릭 시 SCR-06 이동 |
| CHART-EQUITY | Line + Area | daily\_equity (12M) | 호버 툴팁, 기간 변경 |
| TBL-CONTRIB | DataTable | strategy\_pnl\_summary | 행 클릭 시 SCR-02 상세 |
| CHART-REGIME | Multi-line | multi\_asset\_trend | SCR-03 이동 |
| TBL-ALERTS | DataTable | alerts (last 24h) | 승인 대기 항목 → SCR-13 |

주요 시나리오

S1 · 정상 종가 확인 (평일 16:35)

1. 사용자가 SCR-01 진입 → 토스트 "16:31 검증 정상 종료" 확인
2. KPI 5개 모두 녹색/골드 → 이상 없음
3. 알림 영역의 INFO 메시지를 한 번 훑고 종료

S2 · MDD 경고 발생

1. 자산곡선 KPI 카드 빨강 표시 (현재 MDD > 허용 한도 0.8배)
2. 사용자가 카드 클릭 → SCR-06 (리스크/모니터) 이동
3. 자동 대응 큐에 "신규 진입 일시 중단" 발동 이력 → SCR-13에서 사용자 승인

EX1 · 데이터 갱신 실패

조건: last\_run\_log 상태가 'failed' 또는 갱신 시각이 17:00 이후
표시: 토스트 "데이터 갱신 실패. 어제 데이터 기준 표시 중" + 모든 KPI 회색 처리
대응: 우측 상단 [재실행] 버튼 → DataCollector 수동 트리거

화면 ID

SCR-01 · /dashboard

우선순위

P0 · 필수

연결 화면

- SCR-02 전략 관리
- SCR-03 장세/팩터
- SCR-06 리스크/모니터
- SCR-13 실거래 모니터

데이터 갱신

- 16:15 자산곡선
- 16:20 전략 PNL
- 16:30 자동대응 평가

권한

운용자 R/W
연구자 R
관리자 R/W

SCR-02
EXISTING · 추정 사양 (PromotionGate 연동)

## 전략 관리

전략 라이프사이클 (Research → Alert → Mock → Live Small → Live) 전체를 한 화면에서 관리한다. PromotionGate가 자동 평가한 승격 후보를 사용자가 승인/거부한다.

와이어프레임

maps.local / strategies

상태 필터
전체 (7)
Live (2)
Mock (2)
Alert (2)
Research (1)승격 대기
2건
+ 새 전략

| 전략 ID | 이름 | 단계 | Tradeability | Plateau | MC MDD p95 | WFA | 승격 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| str\_001 | Pullback v3 | LIVE | 82 | 78 | 15.2% | PASS | — |
| str\_002 | ATH Outlier | LIVE | 76 | 71 | 28.4% | PASS | — |
| str\_003 | Multi-Asset | MOCK | 71 | 74 | 19.8% | PASS | → Live Small |
| str\_004 | Pullback v4 (β) | MOCK | 63 | 68 | 17.1% | CV 0.6 | — |
| str\_005 | Donchian 100/50 | ALERT | 58 | 65 | 26.3% | PASS | → Mock |
| str\_006 | Bollinger Squeeze | ALERT | 42 | 48 | 31.5% | FAIL | — |
| str\_007 | Pyramiding (R) | RESEARCH | — | — | — | — | — |

단계별 분포

SANKEY · Stage Flow

선택 전략 상세 패널

SELECTED STRATEGY · KPI / Equity / Logs

컴포넌트 사양

| ID | 타입 | 데이터 소스 | 액션 |
| --- | --- | --- | --- |
| FILTER-STAGE | Toggle Group | strategies.stage | 리스트 필터 |
| TBL-STRATEGIES | DataTable + Sort | strategy\_comparison\_summary | 행 클릭 → 상세 패널 |
| BTN-PROMOTE | Action Button | promotion\_history | 승격 승인 → 모달 → DB 기록 |
| PNL-DETAIL | Side Panel | strategy\_metrics 통합 | SCR-08 / SCR-11 이동 링크 |

주요 시나리오

S1 · 승격 후보 확인 → 승인

1. 일요일 02:30. 사용자가 SCR-02 진입 → 상단 "승격 대기 2건" 클릭
2. PromotionGate가 자동 통과 처리한 str\_003 (Mock → Live Small) 행 강조
3. [→ Live Small] 클릭 → 모달: 통과 체크리스트 + 사용자 메모
4. 승인 → promotion\_history 기록 + str\_003.stage 업데이트

EX1 · WFA 변동계수 임계 근접 (CV 0.6)

조건: WFA Sharpe std/mean 비율이 0.5 < CV ≤ 0.7 구간
표시: WFA 컬럼에 노란 "CV 0.6" 배지, 행 자체에는 경고 없음
정책: 자동 승격 차단. 사용자가 SCR-11에서 fold별 결과 확인 후 수동 결정

화면 ID

SCR-02 · /strategies

단계 흐름

Research
└─ Alert Only
└─ Mock
└─ Live Small
└─ Live

승격 게이트

- Plateau ≥ 70
- WFA CV ≤ 0.5
- 신호 ≥ 100건
- 관찰 ≥ 6개월

권한

운용자 R/W
연구자 R
관리자 R/W

승격 승인은 운용자/관리자만 가능

SCR-03
EXISTING · v2.6.1 매트릭스 추가

## 장세 · 팩터 분석

MarketRegime × WeeklyTrend 매트릭스에 따라 진입 정책이 자동 적용되는 화면. 5개월 자산군 추세, Factor 점수 분포, 현재 셀의 한도 비율을 한눈에 보여준다.

와이어프레임

maps.local / market

Market Regime

MIXED

5M MA 5/8 통과

Weekly Trend

PASS

10/20 ↑ 20/40 ↑

현재 한도 비율

50%

전략군별 한도의 절반

코스피 TS

62.5

S2 약한 강세

MarketRegime × WeeklyTrend 매트릭스

|  | Weekly Pass | Weekly Fail |
| --- | --- | --- |
| Strong | 진입 100% | 차단 |
| ▶ Mixed (현재) | 진입 50% | 차단 |
| Weak | ATH/Pullback 25% | 전면 차단 |

현재 (Mixed × Pass) 셀이 활성화되어 있으며 모든 전략군이 한도의 50%로 자동 적용된다.

자산군 추세 (5M MA)

MULTI-ASSET TREND OVERLAY · 8 assets

|  |  |  |
| --- | --- | --- |
| KOSPI | + ↑ | 코스닥 +↑ |
| S&P 500 | + ↑ | NASDAQ +↑ |
| USD/KRW | −↓ | 금 +↑ |
| WTI | ± | 구리 −↓ |

Factor 점수 분포 (코스피200)

FACTOR DISTRIBUTION · Value / Momentum / Quality / Liquidity

주요 시나리오

S1 · 매트릭스 셀 변경 감지

1. 매주 금요일 16:20 WeeklyTrendConfirmation 갱신
2. 셀이 (Strong, Pass) → (Mixed, Pass)로 이동
3. 우측 상단 토스트 "한도 100% → 50% 자동 적용" 표시
4. 사용자가 매트릭스 영역 클릭 → SCR-06으로 이동, 한도 게이지 확인

화면 ID

SCR-03 · /market

갱신 주기

- Regime: 매일 08:40
- Weekly: 금요일 16:20
- Factor: 매일 16:00

권한

운용자 R
연구자 R
관리자 R/W

SCR-04
EXISTING · TrendStrength 게이트 추가

## 종목 후보 풀

Factor + TrendStrength 게이트를 통과한 후보를 전략별로 보여준다. S5 자동 제외 종목 수와 결측 종목 수도 가시화하여 후보 풀의 품질을 즉시 판단한다.

와이어프레임

maps.local / candidates

전략
Pullback
ATH Trend
Multi-Asset유니버스
KOSPI200 + KOSDAQ150 (350)
CSV 내보내기

유니버스

350

S5 제외

−42

TS < 20

결측

−8

상장 100일 미만

최종 후보

28

Factor + TS 통과

| 티커 | 종목명 | Factor | TS | 구간 | 최종점수 | 주봉 | 예상수량 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 005930 | 삼성전자 | 87.2 | 90.0 | S1 | 92.2 (+5) | ↑↑ | 42 |
| 000660 | SK하이닉스 | 82.1 | 80.0 | S1 | 87.1 (+5) | ↑↑ | 15 |
| 035720 | 카카오 | 79.4 | 70.0 | S2 | 79.4 | ↑→ | 22 |
| 051910 | LG화학 | 76.8 | 60.0 | S2 | 76.8 | ↑→ | 8 |
| 005380 | 현대차 | 71.5 | 50.0 | S3 | — 제외 | →→ | — |

S3 (mixed) 이하는 신규 진입 제한 정책에 따라 후보에서 자동 제외된다. Factor 점수가 높아도 차단된다.

주요 시나리오

S1 · 후보 확인 → 주문 큐 등록

1. 16:00 Factor 갱신 후 사용자 진입
2. 28개 최종 후보 중 상위 5개 선택 (체크박스)
3. [주문 큐 등록] → SCR-05 (주문/체결) 자동 이동
4. 다음 거래일 시가에 진입

EX1 · 결측 비율 과다 (5% 초과)

조건: 유니버스 대비 insufficient\_data 종목 비율 ≥ 5%
표시: 결측 KPI 카드 빨강 + 토스트 "유니버스 데이터 품질 점검 필요"
대응: 데이터 갱신 로그 확인, 필요 시 DataCollector 재실행

화면 ID

SCR-04 · /candidates

게이트 순서

1. 유니버스 (KOSPI200+KOSDAQ150)
2. 유동성 필터
3. TS Bucket ≥ S2 게이트
4. Factor 점수 ≥ 60
5. 최종 + 가점 정렬

권한

운용자 R/W
연구자 R

SCR-05
EXISTING · 슬리피지 실측 비교 추가

## 주문 · 체결

실시간 주문 큐와 체결 이력을 관리한다. 모든 체결 결과는 CostModel 가정과 자동 비교되어 슬리피지 실측치가 SCR-13으로 흘러간다.

와이어프레임

maps.local / orders

상태
대기 (3)
체결 (5)
취소 (1)자동주문
활성
긴급 정지

주문 큐 (시가 진입 대기)

| 전략 | 티커 | 방향 | 수량 | 스탑 | 상태 |
| --- | --- | --- | --- | --- | --- |
| Pullback | 005930 | BUY | 42 | 68,500 | PENDING |
| Pullback | 000660 | BUY | 15 | 132,000 | PENDING |
| ATH Trend | 035720 | BUY | 22 | 42,300 | PENDING |

금일 슬리피지 실측

대형주 평균

0.04%

가정 0.05% (−20%)

중소형주 평균

0.22%

가정 0.15% (+47%)

중소형 슬리피지가 가정 +50% 임계 근접 → SCR-13 모니터에서 추세 확인 필요

금일 체결 이력

| 시각 | 티커 | 방향 | 체결가 | VWAP 대비 | 슬립 | 상태 |
| --- | --- | --- | --- | --- | --- | --- |
| 09:00:12 | 005380 | SELL | 198,500 | −0.03% | +0.03% | FILLED |
| 09:00:18 | 051910 | BUY | 412,000 | +0.21% | −0.21% | FILLED |

주요 시나리오

EX1 · 긴급 정지 (Kill Switch)

조건: 사용자가 [긴급 정지] 클릭 또는 LiveBacktestMonitor 트리거 발동
처리: 모든 PENDING 주문 즉시 취소 + 자동주문 비활성 + 보유는 유지
복구: 사용자가 명시적으로 [자동주문 재개] 승인 시까지 정지
권한: 운용자/관리자만 가능

화면 ID

SCR-05 · /orders

실시간 갱신

WebSocket 1초 주기

권한

운용자 R/W
연구자 X
관리자 R/W

SCR-06
EXISTING · 한도 게이지 보강

## 리스크 · 모니터

전략군별 위험 한도, Exposure Cap, 총위험 게이지를 실시간으로 본다. 매트릭스 셀에 따른 한도 비율이 즉시 반영된다.

와이어프레임

maps.local / risk

단기 총위험

1.4%

한도 2% × 0.5 = 1.0%

장기 총위험

1.8%

한도 3% × 0.5 = 1.5%

최대 노출 종목

9.2%

Cap 10%

동시 보유 수

12

전략군 위험 게이지

Pullback1.4% / 1.0% (×140%)

ATH Trend1.8% / 1.5%

Multi-Asset0.5% / 1.0%

Pullback 한도 초과 → SCR-04에서 신규 진입 자동 제한

상관관계 매트릭스

CORRELATION HEATMAP · 12 holdings

현재 보유 (12)

| 티커 | 전략 | 진입가 | 현재가 | 손익 | 노출 | 스탑 |
| --- | --- | --- | --- | --- | --- | --- |
| 005930 | Pullback | 71,200 | 73,500 | +3.2% | 9.2% | 68,500 |
| 000660 | Pullback | 135,000 | 142,000 | +5.2% | 7.4% | 132,000 |
| 035420 | ATH Trend | 182,000 | 175,500 | −3.6% | 5.1% | 170,000 |

화면 ID

SCR-06 · /risk

한도 정의

- 단기 총위험 ≤ 2%
- 장기 총위험 ≤ 3%
- 단일 종목 ≤ 10%
- 매트릭스 셀별 비율 적용

권한

운용자 R/W
연구자 R
관리자 R/W

SCR-07
EXISTING · Plateau/WFA/MC 자동 동반

## 백테스트 콘솔

단일 전략 백테스트 실행 시 Plateau, Walk-Forward, Monte Carlo, Block Bootstrap이 자동으로 동반 실행된다. 결과는 SCR-08, SCR-11로 분기되어 표시된다.

와이어프레임

maps.local / backtest

실행 설정

|  |  |
| --- | --- |
| 전략 | Pullback v3 |
| 기간 | 2015-01 ~ 2026-04 (IS 70% / OOS 30%) |
| 유니버스 | KOSPI200 + KOSDAQ150 |
| 비용 모델 | CostModel 2026Q2 기본값 |
| 동반 검증 | Plateau ✓ · WFA ✓ · MC ✓ · Block Bootstrap ✓ |
| 예상 시간 | ~ 2시간 30분 |

▶ 실행
대기 큐에 등록

실행 진행률

1/5 단일 백테스트완료

2/5 Plateau 그리드73%

3/5 Walk-Forward대기

4/5 Monte Carlo대기

5/5 Block Bootstrap대기

결과 요약 (1/5 완료)

Net CAGR

+22.4%

MDD

−14.8%

Sharpe

1.62

거래 수

348

G2P

2.18

전체 검증 완료 후 SCR-08 (Robustness)에 자동 등록됩니다.

화면 ID

SCR-07 · /backtest

SLA

- 전체 ≤ 3시간
- 병렬 처리 (multiprocessing)
- 오프타임 배치 권장

권한

운용자 R/W
연구자 R/W

SCR-08
NEW · v2.6.1

## Trend Robustness

전략의 견고성을 한 화면에 모은다. Plateau heatmap, Monte Carlo vs Block Bootstrap MDD 분포, Tradeability 점수가 같이 표시되어 "이 전략을 실계좌에 보내도 되는가"의 판정 근거가 된다.

와이어프레임

maps.local / robustness?strategy=str\_001

전략
Pullback v3
ATH Outlier
Multi-Asset가중치 프리셋
Conservative
Balanced
Growth
v2.6.2 재실행

Tradeability

82

≥ 75 LIVE 후보

Plateau

78

robust 등급

MC MDD p95

15.2%

한도 18% 대비 84%

BBoot MDD p95

17.8%

MC 대비 1.17×

OOS / IS

0.82

≥ 0.6 통과

Cross-Market

75

3/4 차원

Parameter Plateau Heatmap

HEATMAP · fast (X) × slow (Y) · color=Gain-to-Pain

|  |  |  |
| --- | --- | --- |
| 최적점 | fast=40, slow=120 | G2P 2.18 |
| 이웃 9개 | Manhattan d=1 | 7/9 통과 = 78점 |

MC vs Block Bootstrap MDD 분포

HISTOGRAM × 2 overlay · 1000 simulations

Block Bootstrap의 분포가 오른쪽으로 밀려 있다 — 군집성 보존이 MDD를 14% 더 보수적으로 잡았다. 1.17× 비율은 임계 1.5× 미만으로 통과.

Tradeability 서브스코어 분해

Robustness (×0.30)

76

Risk (×0.30)

85

Recovery (×0.20)

82

Return (×0.20)

88

최종 = 0.30×76 + 0.30×85 + 0.20×82 + 0.20×88 = 81.9 → 82

컴포넌트 사양

| ID | 타입 | 데이터 소스 | 액션 |
| --- | --- | --- | --- |
| FILTER-STRATEGY | Tab Group | strategy\_comparison\_summary | 전략 전환 시 전체 재로드 |
| FILTER-PRESET | Toggle Group | WEIGHT\_PRESETS | 변경 시 Tradeability 즉시 재계산 + audit 로그 |
| KPI-ROBUSTNESS | KPI Card × 6 | parameter\_plateau\_results, monte\_carlo\_sequence\_results | 호버 시 임계값 비교 |
| HEATMAP-PLATEAU | 2D Heatmap | parameter\_plateau\_results (전체 그리드) | 셀 클릭 시 해당 파라미터 백테스트 결과 |
| HIST-MCBOOT | Overlay Histogram | method='monte\_carlo' / 'block\_bootstrap' | p95 임계선 표시 |
| BREAKDOWN-SUB | KPI Card × 4 | tradeability\_score 분해 | 가중치 변경 즉시 반영 |

주요 시나리오

S1 · 백테스트 후 견고성 확인 (정상)

1. SCR-07에서 풀 스위트 완료 → SCR-08 자동 등록 알림
2. Tradeability 82 (LIVE 후보) 확인
3. Plateau heatmap에서 최적점이 가운데 위치 → 가장자리 과적합 아님 확인
4. MC vs BBoot 비율 1.17 (안전) → SCR-02에서 승격 후보 처리

S2 · 가중치 프리셋 비교 (의사결정)

1. Balanced에서 Tradeability 82 → Conservative 클릭
2. 자동 재계산: Risk 가중치 0.35로 ↑ → 82 → 79
3. Live 임계 75 유지 통과 → 두 프리셋 모두 LIVE 후보
4. 사용자 메모 "Conservative 기준에서도 통과" 입력 → audit 로그 기록

EX1 · MC 통과 / Block Bootstrap 실패

조건: MC MDD p95 = 17%, BBoot MDD p95 = 28% → 비율 1.65 (임계 1.5 초과)
표시: BBoot KPI 카드 빨강 + 토스트 "군집성 위험 높음"
정책: Tradeability와 별개로 PromotionGate에서 mock→live\_small 차단
의미: 평균적으론 견디지만 연속 손실이 몰리면 무너지는 전략

EX2 · Cross-Market 데이터 누락

조건: 일본 시장 데이터 라이선스 만료 등으로 4차원 중 2차원만 검증
처리: Cross-Market KPI에 "2/4 차원" + 회색 표시
판정: KRX + 미국 최소 2차원 통과 시 75점 부여 (설계서 §10 정책)
사용자 행동: 추후 데이터 확보 시 재실행

화면 ID

SCR-08 · /robustness

우선순위

P0 · v2.6.2 핵심

통과 임계

- Tradeability ≥ 75 → LIVE
- ≥ 60 → MOCK 후보
- Plateau ≥ 70
- BBoot/MC ≤ 1.5×

연결 화면

- SCR-07 백테스트 (입력)
- SCR-11 WFA 리포트
- SCR-02 승격 처리

권한

운용자 R/W
연구자 R/W
관리자 R/W

프리셋 변경은 운용자/관리자만

SCR-09
NEW · v2.6.1

## TrendStrength Monitor

유니버스 전체의 추세 강도 분포를 5단계(S1~S5)로 가시화한다. 결측 종목 수, 주봉 확인 통과율, 업종별 쏠림을 같이 본다. 시장 전체의 추세 합의도가 무너지는 시점을 가장 먼저 감지하는 화면.

와이어프레임

maps.local / trend-strength

유니버스
전체 (350)
KOSPI200
KOSDAQ150기준일
2026-05-02시계열
최근 30영업일

S1 강세

68

19.4%

S2 약한 강세

94

26.9%

S3 혼조

112

32.0%

S4 약한 약세

58

16.6%

S5 약세

10

2.9%

결측

8

2.3%

5단계 분포 추이 (30영업일)

STACKED AREA · S1 ~ S5 + 결측

S1+S2 합계가 50% 미만으로 떨어지면 시장 전체 추세 약화 신호. 현재 46.3% → 주의 구간.

업종별 5단계 쏠림

HORIZONTAL STACKED BAR · sector × bucket

주봉 확인 매트릭스 (현재)

|  | Weekly Pass | Weekly Fail | 합계 |
| --- | --- | --- | --- |
| S1+S2 (강세) | 128 | 34 | 162 |
| S3 (혼조) | 71 | 41 | 112 |
| S4+S5 (약세) | 12 | 56 | 68 |

강세 종목 중 Weekly Fail 비율이 21% → 단기 과열 가능성. 신규 진입 시 주봉 통과 종목 우선.

결측 종목 (insufficient\_data, 8건)

| 티커 | 종목명 | 상장일 | 상장일수 | 사유 |
| --- | --- | --- | --- | --- |
| 123456 | (예시) 신규IPO-A | 2026-02-15 | 76 | 상장 100일 미만 |
| 234567 | (예시) 신규IPO-B | 2026-03-08 | 55 | 상장 100일 미만 |

컴포넌트 사양

| ID | 타입 | 데이터 소스 | 액션 |
| --- | --- | --- | --- |
| KPI-BUCKETS | KPI Card × 6 | trend\_strength\_scores (timeframe='daily') | 카드 클릭 시 종목 리스트 |
| CHART-DIST-30D | Stacked Area | 최근 30일 일별 분포 | 호버 시 일자별 비율 |
| CHART-SECTOR | Stacked Bar | sector × bucket pivot | 업종 클릭 시 SCR-04로 이동 (필터 적용) |
| TBL-WEEKLY-MX | DataTable | JOIN trend\_strength\_scores (weekly) | 셀 클릭 시 해당 조합 종목 리스트 |
| TBL-INSUFFICIENT | DataTable | trend\_state='insufficient\_data' | — |

주요 시나리오

S1 · 시장 전환 감지

1. 사용자가 매일 09:00 출근 후 SCR-09 진입 (요청 갱신은 매일 08:40)
2. S1+S2 합계가 전일 52% → 오늘 46% (-6%p)
3. 추이 차트에서 5일 연속 하락 확인
4. SCR-03 장세 페이지 이동 → MarketRegime 매트릭스 셀 변동 점검

S2 · 업종 쏠림 분석

1. 업종별 막대에서 "반도체" S1 비율 80% 발견
2. 막대 클릭 → SCR-04에서 반도체 + S1 필터 자동 적용
3. 후보 종목들의 Factor 점수 + 노출 한도 점검

EX1 · 결측 비율 5% 초과

조건: insufficient\_data 종목 비율 ≥ 5% (현재 2.3%, 임계 미만)
표시: 결측 KPI 카드 빨강 + 토스트 "유니버스 데이터 품질 점검 필요"
대응: SCR-14 (Data Quality)로 이동, 데이터 갱신 로그 확인

화면 ID

SCR-09 · /trend-strength

5단계 정의

- S1 [80,100]
- S2 [60,80)
- S3 [40,60)
- S4 [20,40)
- S5 [0,20)

갱신 주기

- 일봉 TS: 매일 08:40
- 주봉 확인: 금요일 16:20

권한

운용자 R
연구자 R
관리자 R/W

SCR-10
NEW · v2.6.1

## Research Strategies

Donchian, Bollinger, Pyramiding 같은 연구 전략을 별도로 관리한다. 실계좌 자동주문은 절대 금지되며 PromotionGate 통과 현황을 한눈에 본다. 알림 신호와 모의 성과를 함께 표시한다.

와이어프레임

maps.local / research

상태
전체 (3)
Alert Only (2)
Mock (1)실계좌 자동주문 금지
PromotionGate 평가

| 전략 | 유형 | 상태 | 신호 누적 | 모의 CAGR | 모의 MDD | 관찰 기간 | 다음 게이트 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| str\_005 | Donchian 100/50 | MOCK | 182 | +14.2% | −18.4% | 4.2개월 | Mock→Live 대기 (3개월 충족, MC 검증 중) |
| str\_006 | Bollinger Squeeze | ALERT | 68 | +9.8% | −12.2% | 3.5개월 | Alert→Mock 차단 (WFA fail) |
| str\_007 | Pyramiding (R) | RESEARCH | — | — | — | — | 코드 리뷰 단계 |

선택 전략 상세 (Donchian 100/50)

EQUITY CURVE (Mock) + Signal Markers

|  |  |  |  |
| --- | --- | --- | --- |
| 최근 진입 신호 | 005930 삼성전자 | 2026-04-28 09:00 | 100일 고가 돌파 |
| 최근 청산 신호 | 051910 LG화학 | 2026-04-15 09:00 | 50일 저가 이탈 |

PromotionGate 체크리스트

|  |  |
| --- | --- |
| cagr\_ratio | PASS 0.74 |
| mc\_within\_limit | 검증 중 |
| min\_period | PASS 4.2 / 3개월 |
| v2.6.2 추가 | 분모 0 방어 OK |

주요 시나리오

S1 · Mock → Live Small 평가

1. 일요일 02:00 자동 PromotionGate 평가 실행
2. str\_005 (Donchian) 3개월 충족 → MC MDD p95 = 26.3%, 한도 30% → 통과
3. checks 모두 PASS → SCR-02 (전략관리) 승격 후보 큐에 자동 등록
4. 사용자가 SCR-02에서 최종 승인 → str\_005.stage = 'live\_small'

EX1 · 자동주문 우회 시도 차단

조건: 사용자 또는 외부 API가 stage='alert\_only' 전략에 대해 주문 요청
처리: OrderManager가 stage 검증 → ResearchStrategyError 예외 발생
표시: SCR-05 토스트 "Research 전략은 자동주문 불가. SCR-02에서 승격 후 시도"
권한: 관리자 권한으로도 우회 불가

화면 ID

SCR-10 · /research

절대 규칙

- Research → 자동주문 X
- Alert Only → 알림만
- Mock → 모의계좌만
- Live Small 승격은 게이트만

권한

운용자 R
연구자 R/W
관리자 R/W

신규 연구 전략 등록은 연구자/관리자만

SCR-11
NEW · v2.6.1 → v2.6.2 4조건 강화

## Walk-Forward Report

5-fold WFA의 fold별 IS/OOS 성과를 모두 보여준다. v2.6.2부터 4조건 (Sharpe 평균 > 0, 변동계수 ≤ 0.5, 음수 fold ≤ 1, OOS/IS G2P ≥ 0.6)을 모두 표시하여 "안정적으로 손실 나는 전략"이 통과되지 않도록 한다.

와이어프레임

maps.local / wfa?strategy=str\_001

전략
Pullback v3실행
2026-05-01 03:42:18최종 판정
PASS · 4/4

Sharpe 평균 > 0

1.42

v2.6.2 신규 조건

변동계수

0.31

≤ 0.5 PASS

음수 fold 수

0

≤ 1 PASS

OOS/IS G2P

0.78

≥ 0.6 PASS

5-fold 결과 (3년 IS / 1년 OOS)

| Fold | IS 기간 | OOS 기간 | IS Sharpe | OOS Sharpe | IS G2P | OOS G2P | OOS/IS | 최적 파라미터 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2018~2020 | 2021 | 1.82 | 1.18 | 2.14 | 1.62 | 0.76 | fast=40, slow=120 |
| 2 | 2019~2021 | 2022 | 2.01 | 0.94 | 2.31 | 1.48 | 0.64 | fast=40, slow=110 |
| 3 | 2020~2022 | 2023 | 1.94 | 1.78 | 2.45 | 2.18 | 0.89 | fast=40, slow=120 |
| 4 | 2021~2023 | 2024 | 1.76 | 1.52 | 2.08 | 1.74 | 0.84 | fast=50, slow=120 |
| 5 | 2022~2024 | 2025 | 1.88 | 1.68 | 2.22 | 1.82 | 0.82 | fast=40, slow=120 |

5개 fold 중 4개가 fast=40, slow=120 또는 인접값에 수렴 → Plateau Score(78)와 일치하는 안정성 패턴

fold별 OOS Sharpe

BAR CHART · 5 folds · target line at 0

IS vs OOS G2P 산점도

SCATTER · y=x reference + 0.6× line

주요 시나리오

S1 · 4조건 모두 통과 → 정상 승격 진행

1. SCR-07에서 풀 스위트 완료 → SCR-11 자동 등록
2. KPI 4개 모두 녹색 → 최종 판정 PASS
3. SCR-02 (전략관리) → "WFA: PASS" 배지 표시
4. PromotionGate alert→mock 자동 통과

EX1 · "안정적으로 나쁜" 전략 차단 (v2.6.2 핵심)

조건: 모든 fold OOS Sharpe = -0.32 (균일하게 음수)
v2.6.1 판정: 변동계수 0 → "매우 안정적" PASS (오판)
v2.6.2 판정: Sharpe 평균 = -0.32 < 0 → 즉시 FAIL
표시: 첫 KPI 카드 빨강 "Sharpe 평균 ≤ 0 차단"
의미: 일관되게 손실 나는 전략은 변동성이 낮아도 통과 불가

EX2 · 특정 fold 한정 작동 차단

조건: fold 1, 2, 3, 4 음수 / fold 5만 양수 (특정 시기 한정)
판정: neg\_count = 4 > 1 → FAIL (3번째 조건 위반)
표시: "음수 fold 수" KPI 빨강
의미: 특정 시장 환경에서만 작동하는 전략 — 검증 통과 불가

화면 ID

SCR-11 · /wfa

v2.6.2 변경

- Sharpe 평균 > 0 신규
- 4조건 AND 강화
- 1조건만 깨져도 FAIL

우회 정책

사용자 메모로 통과 사유를 덮어쓸 수 없음. 운용자/관리자 권한으로도 우회 불가.

권한

운용자 R
연구자 R
관리자 R

SCR-12
NEW · v2.6.1

## Cost Sensitivity

CostModel 가정값(거래세 0.18%, 슬리피지 대형주 0.05% / 중소형 0.15%)이 ±50% 변동했을 때 Net CAGR이 어떻게 바뀌는지 본다. 실측치가 가정에서 멀어지면 SCR-13 Live Monitor와 연동되어 알림이 발생한다.

와이어프레임

maps.local / cost-sensitivity

전략
Pullback v3
ATH Outlier
Multi-AssetCostModel 버전
2026Q2 (2026-04-01 적용)
분기 가정 리뷰

현재 CostModel 가정 (KRW 기준)

|  |  |  |
| --- | --- | --- |
| 증권거래세 (매도) | 0.18% | 코스피·코스닥 |
| 증권사 수수료 (왕복) | 0.015% | — |
| 슬리피지 대형주 | 0.05% | 시총 ≥ 5천억 |
| 슬리피지 중소형주 | 0.15% | 시총 < 5천억 |
| ETF 거래세 | 0% | 면제 |

금월 실측 vs 가정

|  | 가정 | 실측 | 편차 |
| --- | --- | --- | --- |
| 대형주 슬립 | 0.05% | 0.04% | −20% |
| 중소형 슬립 | 0.15% | 0.22% | +47% |
| 총 거래비용 | 0.32% | 0.36% | +12% |

중소형 슬립 +47%는 가정 +50% 임계 근접. 추가 1주만 악화되면 SCR-13에서 자동 알림 발동.

±50% 시나리오 Net CAGR 분석

SCENARIO LINE · slip × {-50%, -25%, 0%, +25%, +50%}

| 시나리오 | Net CAGR | Net Sharpe | Tradeability | 상태 |
| --- | --- | --- | --- | --- |
| 슬립 −50% | +24.8% | 1.78 | 86 | LIVE |
| 슬립 −25% | +23.6% | 1.70 | 84 | LIVE |
| 가정 (0%) | +22.4% | 1.62 | 82 | LIVE |
| 슬립 +25% | +21.0% | 1.52 | 79 | LIVE |
| 슬립 +50% | +19.6% | 1.42 | 76 | LIVE |

±50% 시나리오 모두 LIVE 임계 75 유지 → 비용 민감도가 안정적인 전략. 견고함의 또 다른 근거.

주요 시나리오

S1 · 분기 가정 리뷰 (분기 1일 04:00)

1. 자동 스케줄러가 review\_cost\_assumptions() 실행
2. 직전 분기 평균 실측치 vs 가정 비교
3. |편차| ≥ 15% 항목이 있으면 사용자에게 가정 갱신 권고
4. 사용자가 [분기 가정 리뷰] 클릭 → 모달에서 새 가정값 입력 + audit 로그

EX1 · 슬리피지 가정 +50% 초과 발생

조건: 월 평균 실측 슬리피지 / 가정 슬리피지 > 1.5
처리: SCR-13 LiveMonitor가 트리거 → 신규 진입 일시 중단
표시: 본 화면 KPI 빨강 + Tradeability 즉시 재계산
PromotionGate: live\_small→live 단계는 즉시 fail (slippage\_actual\_within=0.5 위반)

화면 ID

SCR-12 · /cost-sensitivity

갱신 주기

- 실측 집계: 매일 16:30
- ±50% 시뮬: 월 1회
- 가정 리뷰: 분기 1일 04:00

권한

운용자 R
연구자 R
관리자 R/W

가정값 수정은 관리자만

SCR-13
NEW · v2.6.1

## Live Monitor

실거래 중 검증 지표가 깨졌을 때의 자동 대응을 관제한다. MDD가 허용 한도의 0.8배에 도달하면 신규 진입을 자동 중단하고, 한도 도달 시 비중 50% 축소 후 사용자 승인을 대기한다. 실측 슬리피지가 가정에서 ±50% 벗어나면 Tradeability 재계산.

와이어프레임

maps.local / live-monitor

자동 대응
활성대기 승인
1건최근 트리거
14:22 (슬립 경고)
이력 전체

실측 MDD

−18.2%

한도 28% × 0.8 = 22.4% 미만

대형주 슬립

0.04%

정상

중소형 슬립

0.22%

가정 +47% (임계 50%)

연속 손실

3

10 이상 시 트리거

자동 대응 횟수 (30d)

2

자동 대응 트리거 정의

| 트리거 | 임계 | 대응 | 승인 |
| --- | --- | --- | --- |
| 실측 MDD | ≥ 한도 × 0.8 | 신규 진입 일시 중단 | 자동 |
| 실측 MDD | ≥ 한도 | 비중 50% 축소 | 승인 대기 |
| 월평균 슬립 | ±50% 벗어남 | Tradeability 재계산 | 자동 |
| 연속 손실 | ≥ 10회 | 신규 진입 중단 + Robustness 재실행 큐 | 자동 |
| MarketRegime | 강세 → 약세 전환 | 전략군별 한도 재산정 | 자동 |

대기 승인 큐 (1건)

MDD 한도 도달

str\_004 (Pullback v4 β) 실측 MDD = −17.4% / 한도 18%

자동 적용된 임시 조치: 비중 50% 축소

승인 (영구 적용)
롤백

최근 30일 트리거 이력

| 시각 | 트리거 | 대상 | 대응 | 사용자 결정 |
| --- | --- | --- | --- | --- |
| 2026-05-02 14:22 | 슬립 경고 | 중소형 평균 | Tradeability 재계산 | 자동 (승인 불필요) |
| 2026-04-28 09:32 | MDD 한도 | str\_004 | 비중 50% 축소 | 승인됨 |
| 2026-04-15 16:10 | Regime | 전체 | 한도 100% → 50% | 자동 |

주요 시나리오

S1 · 자동 대응 발동 → 사용자 승인 (정상 흐름)

1. 09:32 일별 자산곡선 갱신 시 str\_004 MDD 한도 도달 감지
2. LiveBacktestMonitor가 즉시 비중 50% 축소 적용 + 알림 발송 (이메일·앱 푸시)
3. 사용자가 SCR-13 진입 → 대기 승인 큐 확인
4. [승인] 클릭 → 영구 적용 + audit 로그
5. 또는 [롤백] → 원복 + 사유 메모 필수

EX1 · 보유 청산 트리거 (사용자 승인 필수)

조건: 자동 대응이 신규 진입 중단을 넘어 보유 청산까지 가는 경우
정책: 보유 청산은 절대 자동 실행 금지. 반드시 사용자 명시적 승인 필요
표시: 큐에 큰 빨강 배너 "보유 청산 승인 대기"
승인 시간 제한: 24시간 내 미승인 시 운용자에게 추가 알림

EX2 · 자동 대응 비활성 (긴급 정지)

조건: 사용자가 SCR-05에서 [긴급 정지] 클릭
처리: 모든 자동 대응 트리거 일시 비활성. 트리거 발생해도 알림만 표시
재개: SCR-13 우측 [자동 대응 재개] 버튼 → 관리자 권한 + 사유 입력 필수
의도: 시스템 점검 중 의도치 않은 비중 축소 방지

화면 ID

SCR-13 · /live-monitor

자동 대응 범위

- 신규 진입 중단 (자동)
- 비중 축소 (자동 + 승인)
- 보유 청산 (승인 필수)
- 재계산 (자동)

우선순위

P0 · 사용자 보호

권한

운용자 R/W
연구자 R
관리자 R/W

자동 대응 비활성/재개는 관리자 권한 필수

SCR-14
NEW · v2.6.2

## Data Quality

DataQualityFilter (v2.6.2 신규)가 거부한 종목과 사유를 본다. 6개 기준(수정주가, 상장폐지, 거래정지/관리종목, 거래대금 하한, 신규상장, ETF/ETN/스팩)별 거부 통계와 거부 비율 추이를 표시한다. 입력 데이터의 신뢰도가 모든 후속 검증의 신뢰도다.

와이어프레임

maps.local / data-quality

모드
Live
Backtest기준일
2026-05-02시계열
최근 90일
유니버스 재구성

유니버스 후보

350

KOSPI200 + KOSDAQ150

통과 (kept)

312

89.1%

거부 (rejected)

38

10.9%

거부율 임계

5%

초과 시 알림

거부 사유 분포

| 사유 코드 | 의미 | 건수 | 비율 |
| --- | --- | --- | --- |
| low\_turnover | 거래대금 하한 미달 | 14 | 36.8% |
| recently\_listed | 상장 100일 미만 | 8 | 21.1% |
| trading\_halted | 거래정지 | 6 | 15.8% |
| managed\_stock | 관리종목 지정 | 4 | 10.5% |
| delisted | 상장폐지 (Live 모드) | 3 | 7.9% |
| excluded\_type | 스팩 자동 제외 | 2 | 5.3% |
| unadjusted\_price | 수정주가 미반영 | 1 | 2.6% |

거부율 추이 (90일)

LINE · daily reject ratio + 5% threshold

최근 4월 중순부터 거래대금 하한 거부 증가 — 약세장 진입과 일치. SCR-03 장세 페이지와 교차 점검 필요.

거부 종목 상세 (38건 중 상위 10)

| 티커 | 종목명 | 시장 | 거부 사유 | 거래대금 (20d) | 상장일수 | 비고 |
| --- | --- | --- | --- | --- | --- | --- |
| 123450 | (예시) 소형주-A | KOSDAQ | low\_turnover | 2.1억 | 1248 | 3억 미달 |
| 234561 | (예시) 신규IPO-A | KOSPI | recently\_listed | 12.4억 | 76 | 상장 76일 |
| 345672 | (예시) 정지-A | KOSPI | trading\_halted | — | 2103 | 회계감사 의견거절 |
| 456783 | (예시) 관리-A | KOSDAQ | managed\_stock | 5.8억 | 1842 | 4년 적자 |

DataQualityFilter 설정 (관리자 전용)

|  |  |  |
| --- | --- | --- |
| MIN\_LISTING\_DAYS | 100 | TS insufficient\_data와 일치 |
| MIN\_TURNOVER (KOSPI) | 5억 KRW | 20일 평균 |
| MIN\_TURNOVER (KOSDAQ) | 3억 KRW | 20일 평균 |
| EXCLUDED\_TYPES | ['SPAC'] | ETF/ETN은 통과 (CostModel에서 거래세 면제) |

컴포넌트 사양

| ID | 타입 | 데이터 소스 | 액션 |
| --- | --- | --- | --- |
| FILTER-MODE | Toggle | — | Live/Backtest 모드 전환 |
| KPI-QUALITY | KPI Card × 4 | universe\_quality\_log | 거부율 ≥ 5% 시 빨강 |
| TBL-REASONS | DataTable | rejection\_summary (JSON) | 행 클릭 시 해당 사유 종목 상세 |
| CHART-TREND-90D | Line + Threshold | 일별 kept/total 비율 | 5% 임계선 표시 |
| TBL-REJECTED | DataTable + Filter | 최근 거부 목록 | — |
| SETTINGS-DQ | Read-only Form | RobustnessConstants | 관리자 클릭 시 편집 모달 |

주요 시나리오

S1 · 일별 데이터 품질 점검 (16:35)

1. 사용자 SCR-01 진입 → 알림 영역에 INFO "유니버스 312/350"
2. 클릭 → SCR-14 자동 이동
3. 거부율 10.9%, 임계 5% 초과지만 사유 분포가 "low\_turnover" 위주 → 약세장 정상
4. 사유 분포 표 확인 후 종료. 추가 액션 불필요

EX1 · 거부율 급증 (15% 초과)

조건: 일일 거부율 ≥ 15% (정상 5~12% 범위)
처리: 사용자에게 푸시 알림 "데이터 품질 점검 필요"
원인 진단: (a) 시장 전체 약세 (b) DataCollector 갱신 실패 (c) 신규 폐지 다수
대응: SCR-14에서 사유 분포 확인 → 시장 전체 약세면 정상, 갱신 실패면 DataCollector 재실행

EX2 · 동일 종목 30일 연속 trading\_halted

조건: 한 종목이 30영업일 이상 거래정지 지속
처리: 자동 별도 알림 발생 (단순 거부 알림과 분리)
대응: 보유 중이면 SCR-06 (리스크) 이동 안내. 정지 해제 시까지 평가손익 동결

EX3 · 백테스트 모드 / Live 모드 차이 검증

목적: 생존자 편향 차단 확인
백테스트 모드: 폐지일 이전까지 종목을 유니버스에 포함 → delisted\_before\_ref 사유 발생 정상
Live 모드: 폐지 종목 즉시 제외 → delisted 사유 발생 정상
교차 검증: WFA fold별 ref\_date에서 delisted\_before\_ref 발생 = look-ahead 차단 확인

화면 ID

SCR-14 · /data-quality

우선순위

P0 · v2.6.2 핵심

왜 P0 인가

검증 로직이 아무리 정교해도 입력 데이터에 생존자 편향이 있으면 모든 백테스트가 거짓말을 한다. 모든 후속 검증의 신뢰도가 이 화면에서 결정된다.

갱신 주기

- Live 모드: 매일 08:35
- Backtest 모드: 백테스트 실행 시
- WFA fold별: 자동

권한

운용자 R
연구자 R
관리자 R/W

DataQualityFilter 설정 변경은 관리자만

APPENDIX A
REFERENCE

## 권한 매트릭스

3개 역할(운용자, 연구자, 관리자)이 14개 화면에서 갖는 권한을 한 표로 정리한다. R = 읽기 전용, R/W = 읽기/쓰기, X = 접근 불가, R/W★ = 일부 액션 추가 권한 필요.

| 화면 | 운용자 | 연구자 | 관리자 | 특이사항 |
| --- | --- | --- | --- | --- |
| SCR-01 대시보드 | R/W | R | R/W | — |
| SCR-02 전략 관리 | R/W★ | R | R/W★ | 승격 승인 = 운용자/관리자 |
| SCR-03 장세/팩터 | R | R | R/W | — |
| SCR-04 종목 후보 | R/W | R | R/W | 주문 큐 등록 = 운용자/관리자 |
| SCR-05 주문/체결 | R/W★ | X | R/W★ | 긴급 정지 = 운용자/관리자 |
| SCR-06 리스크 | R/W | R | R/W | 한도 변경 = 관리자 |
| SCR-07 백테스트 | R/W | R/W | R/W | — |
| SCR-08 Robustness | R/W★ | R/W | R/W★ | 가중치 프리셋 변경 = 운용자/관리자 |
| SCR-09 TrendStrength | R | R | R/W | — |
| SCR-10 Research | R | R/W | R/W | 신규 연구 전략 등록 = 연구자/관리자 |
| SCR-11 WFA | R | R | R | 판정 우회 불가 (모든 권한 동일) |
| SCR-12 Cost Sensitivity | R | R | R/W★ | 가정값 수정 = 관리자 |
| SCR-13 Live Monitor | R/W★ | R | R/W★ | 자동 대응 비활성/재개 = 관리자 |
| SCR-14 Data Quality | R | R | R/W★ | DQ 설정 변경 = 관리자 |

절대 규칙 (모든 권한 공통 적용)

- WFA 4조건 통과 판정은 어떤 권한으로도 우회할 수 없다.
- Research 전략의 자동주문은 관리자 권한으로도 실행할 수 없다.
- 보유 청산을 동반하는 자동 대응은 사용자 명시적 승인 없이 실행되지 않는다.
- Tradeability 승격 임계 (60/75)는 가중치 프리셋과 무관하게 고정이다.
- 모든 설정 변경, 승격 승인, 자동 대응 승인은 audit 로그에 사용자 ID와 함께 기록된다.

APPENDIX B
REFERENCE

## 화면 전이 다이어그램

사용자의 주요 워크플로 4가지가 화면을 어떻게 거치는지 정리한다. 모든 워크플로의 시작은 SCR-01 (대시보드)이며, 의사결정 지점은 회색 강조로 표시된다.

Workflow A · 일일 운용 (평일 16:35)

SCR-01 대시보드 (KPI 점검)
└─ 정상 → 종료
└─ 알림 발생 →
├─ SCR-13 Live Monitor (자동 대응 승인)
├─ SCR-14 Data Quality (거부율 점검)
└─ SCR-06 Risk (한도 점검)

Workflow B · 신규 진입 (평일 16:00 ~ 다음날 09:00)

SCR-01 대시보드
└─ SCR-03 장세/팩터 (매트릭스 셀 확인)
└─ SCR-09 TrendStrength (5단계 분포 확인)
└─ SCR-04 종목 후보 (Factor + TS 통과 종목)
└─ SCR-06 Risk (한도 점검)
└─ SCR-05 주문/체결 (주문 큐 등록)

Workflow C · 신규 전략 검증 (연구자, 1주~6개월)

SCR-07 백테스트 콘솔 (단일 백테스트 + 풀 스위트)
└─ SCR-08 Robustness (Tradeability 점수)
├─ SCR-11 WFA (4조건 통과 확인)
└─ SCR-12 Cost Sensitivity (±50% 시나리오)
└─ SCR-02 전략 관리 (Alert Only 등록)
└─ SCR-10 Research (모의 신호 누적, 6개월)
└─ SCR-02 (PromotionGate 통과 → Mock → Live Small → Live)

Workflow D · 위기 대응 (트리거 발생 시)

알림 푸시 (이메일/앱)
└─ SCR-13 Live Monitor (트리거 종류 확인)
├─ 신규 진입 중단 → 자동, 추가 액션 불필요
├─ 비중 50% 축소 → 사용자 승인 (24h 내)
└─ 보유 청산 검토 →
├─ SCR-06 Risk (전략군 한도 재산정)
├─ SCR-05 주문/체결 (긴급 정지 대안)
└─ 사용자 명시적 승인 후 청산

APPENDIX C
REFERENCE

## 디자인 시스템

14개 화면 전체에 일관되게 적용되는 시각 규약. 색상 토큰, 타이포그래피, 데이터 포맷, 상태 배지를 정의한다.

의미 색상 토큰

PASS / 양호

#4ade80

검증 통과, 이익

FAIL / 위험

#f87171

검증 실패, 손실

WARN / 주의

#fbbf24

임계 근접

INFO / 중립

#60a5fa

정보, 갱신

RESEARCH

#c084fc

연구 단계

ACCENT

#d4a85c

핵심 KPI, 활성

상태 배지 8종

LIVE
MOCK
ALERT
RESEARCH
PASS
FAIL
WARN
INFO

타이포그래피

| 용도 | 폰트 | 샘플 |
| --- | --- | --- |
| 화면 제목 | Fraunces 600 36px | Trend Robustness |
| 본문 | IBM Plex Sans KR 400 14px | 5-fold WFA의 fold별 IS/OOS 성과를 모두 보여준다. |
| 데이터 / KPI | JetBrains Mono 500 22px | +22.4% |
| 레이블 / 메타 | JetBrains Mono 500 10px UPPER 0.18em | SHARPE PEAK 0.5 |
| 코드 / 식별자 | JetBrains Mono 400 12px | str\_001 / fast=40 |

데이터 포맷 규칙

| 유형 | 포맷 | 예시 |
| --- | --- | --- |
| 퍼센트 | 소수 1자리 + % | +22.4%, −18.2% |
| 가격 (KRW) | 천 단위 콤마 + 통화 기호 | ₩142,308,500 |
| 점수 (0~100) | 정수 또는 소수 1자리 | 82, 73.5 |
| 비율 (0~1) | 소수 2자리 | 0.82, 1.17 |
| 일자 | YYYY-MM-DD | 2026-05-02 |
| 시각 | HH:mm:ss | 16:31:04 |
| 거래대금 | 억/조 단위 + 1자리 | 5.8억, 1.2조 |
| 티커 | 6자리 숫자 | 005930, 000660 |
| 전략 ID | str\_NNN snake\_case | str\_001, str\_pullback\_v3 |

레이아웃 그리드

- 최소 뷰포트: 1280px (데스크탑 우선)
- 사이드바: 280px 고정 / 메인: 가변
- 화면별 사이드바(우측): 320px 고정 (화면 메타·권한·연결)
- 섹션 패딩: 32px (상하) × 48px (좌우)
- 카드 그리드: auto-fit minmax(140px, 1fr)
- 1100px 미만: 우측 사이드바 본문 하단으로 이동
- 768px 미만: 좌측 네비게이션도 본문 상단으로 이동

MAPS v2.6.2 SCREEN DESIGN · 14 SCREENS + 3 APPENDICES · KEYBOARD: ← → TO NAVIGATE
