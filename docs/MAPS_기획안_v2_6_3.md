**MAPS 기획안 v2.6.3**

MAPS — Market-Adaptive Profit Management System

**v2.6.3 — Implementation Readiness (구현 착수 준비 통합본)**

| **항목** | **내용** |
| --- | --- |
| 문서 유형 | 기획안 |
| 버전 | v2.6.3 (v2.6.2 + 구현 착수 준비 — ChatGPT 개발 검토 반영) |
| 핵심 추가 | 데이터 소스 정책, BrokerAdapter 정책, 알고리즘 계좌 규정, as-of-date 유니버스, Phase 로드맵, 파일럿 전략 기준 |
| 대상 | 국내 주식 + ETF + 글로벌 자산군 추세 지표 |
| 운영 원칙 | 검증 체계가 먼저, 주문 기능은 마지막 |

*주의: 본 문서는 투자 수익을 보장하지 않으며 실거래 전 관련 법규와 증권사 API 약관 확인이 필수다.*

# **1. v2.6.2 → v2.6.3 변경 이력**

v2.6.3은 외부 개발 검토(ChatGPT)에서 발견한 "구현 착수 전 선결 조건" 빈 칸을 채우는 준비 보강이다. 코드-사양 동기화(v2.6.2)가 끝난 시점에서 실제 개발 환경, 데이터 소스, 법적 선결 조건을 확정하지 않으면 Phase 1조차 시작할 수 없다.

| **구분** | **v2.6.2 상태** | **v2.6.3 추가** | **우선순위** |
| --- | --- | --- | --- |
| 데이터 소스 | 암묵적 가정 | 원천 정책 + KRX/증권사 API 매핑 명시 (§6) | HIGH |
| as-of-date 유니버스 | 단순 필터로 기술 | 기준일 관점 유니버스 생성기 재정의 (§7) | HIGH |
| BrokerAdapter 추상화 | OrderManager에서 직접 API 호출 가정 | 인터페이스 + 지원 증권사 목록 (§8) | HIGH |
| 알고리즘 계좌 규정 | 언급 없음 | 실거래 전 법적 선결 조건 명시 (§9) | HIGH |
| Phase 로드맵 | 구현 우선순위 목록만 존재 | Phase 0~5 마일스톤 + 진입 기준 (§10) | MEDIUM |
| 파일럿 전략 기준 | 전략 ID만 정의 | 구현 수준 기준 (진입/청산/파라미터) 최소 요건 (§11) | MEDIUM |
| Mock OrderManager | 언급 없음 | 검증 항목 + 실계좌 전환 조건 (§12) | MEDIUM |
| Kill Switch 정책 | SCR-13에만 언급 | RiskManager 기획 관점 명문화 (§13) | MEDIUM |

# **2. v2.6.3 목적**

v2.6.1~v2.6.2는 "검증 체계가 약속한 수준을 실제로 보장"하는 작업이었다. v2.6.3은 그 체계를 실제로 만들기 위해 "첫 줄의 코드를 쓰기 전에 확정해야 할 것들"을 기획안 수준에서 결정한다.

구현의 철학은 ChatGPT 검토 결론과 문서의 운영 원칙이 일치한다: 좋아 보이는 전략을 빨리 돌리는 것이 아니라, 나쁜 전략이 실계좌로 못 올라오게 막는 것. 구현 순서도 이 철학 그대로 간다.

# **3. MAPS v2.6.3 핵심 컨셉**

**MAPS v2.6.3 =**

- 기존 v2.6.2 전체 (검증 체계 + 데이터 품질 + 코드-사양 동기화)
- + 데이터 소스 원천 정책 (KRX + 증권사 API 조합)
- + as-of-date 유니버스 생성기 개념 재정의
- + BrokerAdapter 인터페이스 추상화 정책
- + 알고리즘 계좌 규정 확인 선결 조건
- + Phase 0~5 구현 마일스톤
- + 파일럿 전략 최소 구현 기준
- + Mock OrderManager 검증 항목
- + Kill Switch 정책

**핵심 문장: v2.6.3은 "검증 중심 자동매매 플랫폼"으로서 MAPS가 실제 코드가 되기 위한 첫 번째 물음들에 답하는 문서다.**

# **4. v2.6.2 체계 전체 계승**

v2.6.3은 v2.6.2의 모든 정책을 계승한다. 아래는 변경 없이 유지되는 핵심 항목이다.

| **항목** | **v2.6.2 기준** | **v2.6.3 상태** |
| --- | --- | --- |
| WFA 4조건 AND | Sharpe>0, CV<=0.5, 음수fold<=1, OOS/IS G2P>=0.6 | 유지 |
| PromotionGate 단계 | Research→Alert→Mock→Live Small→Live | 유지 |
| 전략군별 허용 MDD | Pullback 18%, ATH 35%, Portfolio 28% | 유지 |
| Tradeability 프리셋 | Conservative/Balanced/Growth | 유지 |
| DataQualityFilter 6개 기준 | 수정주가, 폐지, 정지, 거래대금, 신규상장, ETF/스팩 | 보강 (as-of-date 재정의) |
| 부등호 표기 규약 | 산문 유니코드, 코드·표 ASCII | 유지 |
| DB 호환 정책 | SQLite 기본, MAPS\_DB\_URL로 PostgreSQL 전환 | 유지 |

# **5. v2.6.3 적용 원칙**

| **원칙** | **내용** |
| --- | --- |
| 선결 조건 우선 | §6~§9(데이터·브로커·규정)는 코드 착수 전 반드시 확정 |
| 주문 기능 마지막 | BrokerAdapter 실구현은 Phase 4. Phase 1~3는 실주문 없이 진행 |
| audit 로그 첫날부터 | promotion\_history, universe\_quality\_log, 주문 로그는 Day 1부터 스키마 존재 |
| 파일럿 전략 1개 집중 | 최초 구현은 전략 1개(pullback\_v3)로 전체 파이프라인 검증 후 확장 |
| Mock 충분히 | 실계좌 전환은 Mock에서 최소 3개월 검증 후 PromotionGate 통과 시만 |

# **6. 데이터 소스 원천 정책 (신규)**

## **6.1 필요 데이터 유형과 원천**

DataQualityFilter와 TrendStrengthEngine이 is\_adjusted, is\_halted\_at(), avg\_turnover\_20d() 같은 메서드를 호출하려면 이 데이터가 먼저 확보되어야 한다. 어디서 어떻게 가져오는지가 Phase 1의 첫 번째 장벽이다.

| **데이터 유형** | **권장 원천** | **특이사항** | **우선순위** |
| --- | --- | --- | --- |
| 일별 OHLCV (수정주가) | KRX Open API 또는 증권사 HTS/API | 수정주가는 원시 종가와 별도 수집 필요 | P0 |
| 종목 메타데이터 (상장/폐지일, 시장구분) | KRX Open API 참조정보 | 인증키 신청 + 활용 신청 필요 | P0 |
| 거래정지 이력 | KRX Open API 또는 증권사 API | 정지 기간 시작/종료일 모두 필요 | P0 |
| 관리종목 지정 이력 | KRX Open API | 지정일·해제일 포함 | P1 |
| 거래대금 (20일 평균) | OHLCV에서 직접 계산 | 별도 수집 불필요 | P0 |
| ETF/ETN/SPAC 구분 | KRX 참조정보 security\_type 필드 | ETF 거래세 면제 CostModel 연동 | P1 |
| 실시간 체결 (Live) | 증권사 WebSocket API | KIS 또는 키움 선택 필요 | P4 |

## **6.2 KRX Open API 활용 정책**

KRX Open API (openapi.krx.co.kr)는 종가, 종목 이벤트, 참조정보 등을 제공한다. 인증키 신청과 활용 신청 절차가 별도로 있으며, 실시간 데이터는 제공하지 않으므로 종가 기준 일별 수집에 활용한다.

- 인증키 신청 → 활용 신청 → API 테스트 → 일별 수집 자동화 순서로 진행
- 제공 항목: 일별 OHLCV, 종목 메타, 지수 구성종목, 거래정지 공시
- 미제공 또는 불완전 항목(수정주가, 관리종목 상세 이력)은 증권사 API 또는 유료 데이터 보완
- Phase 1에서 KRX API로 커버 가능한 항목 목록과 불가 항목을 명시적으로 확인 후 대안 확정

## **6.3 데이터 소스 비용 정책**

| **소스** | **비용 수준** | **커버리지** | **결정 기준** |
| --- | --- | --- | --- |
| KRX Open API | 무료 (인증 필요) | 일별 OHLCV, 종목 메타 | Phase 1 기본 원천 |
| 증권사 API (KIS/키움) | 계좌 개설 후 무료 | 실시간, 호가, 체결, 계좌 | Phase 4 실거래 필수 |
| 유료 데이터 (TS2000 등) | 월정액 유료 | 수정주가, 재무, 이벤트 완전본 | KRX API 불완전 시 검토 |
| 크롤링 | 법적 위험 있음 | 다양 | 원칙적으로 사용 금지 |

# **7. as-of-date 유니버스 생성기 재정의 (신규)**

v2.6.2의 DataQualityFilter는 "특정 조건을 가진 종목을 걸러내는 필터"로 기술되었다. 그러나 백테스트 정확성을 위해서는 단순 필터가 아니라 "특정 기준일(ref\_date)에 실제로 매수 가능했던 종목 집합을 재현하는 생성기"여야 한다.

예시: 2020-03-15 기준 유니버스는 그 날짜에 상장되어 있고, 거래정지가 아니고, 관리종목이 아니며, 거래대금 요건을 충족하는 종목의 집합이다. 2023-03-15 기준 유니버스는 그보다 훨씬 다른 집합이다.

| **구분** | **단순 필터 (v2.6.2)** | **as-of-date 생성기 (v2.6.3)** |
| --- | --- | --- |
| 호출 방식 | apply(universe) 일회성 | generate(ref\_date) 기준일마다 재호출 |
| 상장폐지 처리 | delisted 플래그 조건 분기 | delisting\_date > ref\_date 이면 포함, 이하면 제외 |
| 거래정지 처리 | is\_halted\_at(ref\_date) 호출 | 정지 기간 이력 테이블에서 ref\_date 포함 여부 조회 |
| WFA 연동 | fold별 재호출 명시 | 각 fold IS/OOS 시작일을 ref\_date로 넘겨 독립 유니버스 생성 |
| Look-ahead 방지 | 부분적 | ref\_date 이후 정보(폐지, 정지) 차단을 생성기가 책임 |

운영 정책: WFA 5개 fold의 각 IS 시작일과 OOS 시작일마다 generate(ref\_date)를 호출하여 해당 시점의 정확한 유니버스를 만든다. 동일 fold 안에서 IS와 OOS 유니버스가 달라도 된다 — 폐지 종목은 IS 구간 내 폐지일에서 자동 청산되기 때문이다.

# **8. BrokerAdapter 추상화 정책 (신규)**

v2.6.2의 OrderManager는 특정 증권사 API를 직접 호출하는 것처럼 암묵적으로 가정하고 있다. MAPS가 특정 증권사에 종속되지 않으려면 BrokerAdapter 인터페이스를 먼저 정의하고, 구체적인 증권사 구현체는 이 인터페이스를 구현하는 형태로 설계해야 한다.

## **8.1 BrokerAdapter 인터페이스 정의 (기획 수준)**

| **메서드** | **입력** | **반환** | **설명** |
| --- | --- | --- | --- |
| place\_order(order) | Order 객체 | OrderResult | 시장가/지정가 주문 제출 |
| cancel\_order(order\_id) | 주문 ID | bool | 미체결 주문 취소 |
| get\_position(ticker) | 티커 | Position | 현재 보유 수량/평균단가 |
| get\_account\_balance() | — | AccountBalance | 총 자산, 예수금, 보유 목록 |
| subscribe\_realtime(ticker, callback) | 티커, 콜백 | — | 실시간 체결가 구독 (WebSocket) |
| is\_market\_open() | — | bool | 장 운영 시간 여부 |

## **8.2 지원 증권사 후보**

| **증권사** | **API 유형** | **특이사항** | **Phase** |
| --- | --- | --- | --- |
| 한국투자증권 (KIS) | REST + WebSocket | 운영/개발 환경 분리, 개발 서버 테스트 권장 | Phase 4 후보 |
| 키움증권 | REST + OpenAPI (Win) | 알고리즘 계좌 등록 안내 존재, 약관 확인 필수 | Phase 4 후보 |
| Mock Broker | 내부 구현체 | 실주문 없이 OrderManager 검증용 | Phase 2~3 전용 |

결정 정책: Phase 4 진입 전에 증권사 1개를 확정한다. 그 전까지는 MockBroker만 사용한다. 증권사 전환 시에도 OrderManager 코드를 수정하지 않고 어댑터만 교체할 수 있어야 한다.

# **9. 알고리즘 계좌 규정 선결 조건 (신규)**

키움 OpenAPI 안내에는 관련 규정에 따라 알고리즘 계좌로 거래소에 등록될 수 있다는 내용이 있다. MAPS가 자동으로 주문을 생성하고 제출하면 알고리즘 매매 시스템에 해당할 수 있으며, 이는 기술 완성도와 무관하게 실거래 전에 확인해야 하는 법적 선결 조건이다.

| **항목** | **확인 사항** | **시점** |
| --- | --- | --- |
| 알고리즘 매매 정의 | 금융위/거래소 기준 알고리즘 매매 해당 여부 확인 | Phase 4 착수 전 |
| 증권사 약관 | 선택 증권사의 자동매매 관련 약관 및 API 이용 제한 확인 | Phase 4 착수 전 |
| 계좌 등록 | 알고리즘 계좌 등록 요건 충족 여부 (증권사별 상이) | Phase 4 착수 전 |
| 주문 속도 제한 | 초당 주문 건수 제한, 일일 주문 한도 확인 | Phase 4 착수 전 |
| 감사 기록 보존 | 주문 이력 보존 의무 기간 확인 | Day 1부터 구현 |
| API 이용 신청 | KIS/키움 개발자 포털 API 이용 신청 절차 완료 | Phase 3 말 |

원칙: 위 항목 중 하나라도 미확인 상태에서 실계좌 주문을 실행하지 않는다. MockBroker 단계에서 기술 검증을 완료한 후 규정 확인이 완료된 시점에 Phase 5로 진입한다.

# **10. 구현 Phase 로드맵 (v2.6.3 기준)**

v2.6.1에서 "구현 우선순위" 목록만 있었던 것을 Phase 단위 마일스톤으로 재정의한다. 각 Phase에는 완료 기준(Definition of Done)이 있어야 다음 Phase로 진입한다.

| **Phase** | **목표** | **주요 산출물** | **완료 기준** |
| --- | --- | --- | --- |
| Phase 0 프로젝트 골격 | 패키지 구조, DB, 스케줄러 설정 | common/constants.py, common/db.py, Alembic baseline, 테스트 프레임워크 | 모든 import 성공, Alembic upgrade head 성공 |
| Phase 1 데이터 계층 | 가격/종목 메타/거래정지 수집 + as-of-date 유니버스 | DataCollector, security\_metadata 테이블, DataQualityFilter(생성기 버전), universe\_quality\_log | generate(ref\_date) 호출 시 look-ahead 없이 종목 목록 반환 + 테스트 통과 |
| Phase 2 백테스트/비용 | 단일 전략 1개 백테스트 가능 | CostModel, BacktestEngine (pullback\_v3 한 개), 손익곡선 출력 | CostModel 단위 테스트 전부 통과, 10년 백테스트 30분 이내 |
| Phase 3 검증 엔진 | WFA, Plateau, MC, PromotionGate | WalkForwardAnalyzer(4조건), ParameterPlateauTester, MonteCarloSequenceTester, PromotionGate | test\_wfa\_negative\_mean\_blocked 포함 회귀 테스트 전부 통과 |
| Phase 4 Mock 거래 | 주문 생성/취소/체결/복구 검증 | MockBroker, OrderManager, RiskManager(Kill Switch 포함), 최소 API | 중복주문 방지, 장 종료 강제 정리, API 장애 복구 시나리오 통과 |
| Phase 5 Live Small | 실계좌 최소 금액 Live Small 전략 1개 | 실브로커 어댑터, LiveBacktestMonitor, 최소 대시보드 | 알고리즘 계좌 규정 확인 완료 + PromotionGate Mock→Live Small 통과 후 진입 |

# **11. 파일럿 전략 구현 최소 기준 (신규)**

문서에서 pullback\_v3, ath\_outlier, multi\_asset 같은 전략 ID는 정의되어 있지만 구체적인 진입/청산 규칙이 구현 수준으로 기술되어 있지 않다. Phase 2에서 BacktestEngine을 만들려면 전략 1개가 먼저 코드 수준으로 정의되어야 한다. v2.6.3은 최초 파일럿 전략(pullback\_v3)의 최소 기준을 아래와 같이 정한다.

| **항목** | **정의** | **비고** |
| --- | --- | --- |
| 전략 이름 | pullback\_v3 (단기 평균회귀 눌림목) |  |
| 유니버스 | KOSPI200 + KOSDAQ150 (DataQualityFilter 통과 종목) |  |
| 진입 조건 | ① MA5 > MA20 (단기 추세 확인) ② RSI(2) < 10 (눌림목 확인) ③ 당일 저가가 전일 저가 미만 (추가 눌림) | 3조건 AND |
| 청산 조건 | ① 보유 후 1일 초과 + 종가가 MA5 이상 시 청산 ② 진입가 대비 -5% 손절 | ExitEngine 연동 |
| 포지션 사이징 | 계좌 위험 0.5% / (진입가 - 스탑가) 단일 종목 노출 10% 상한 | PositionSizingEngine |
| 파라미터 범위 | RSI 임계: 5/10/15, MA 기간: 20/30/40 | Plateau 그리드 |
| 검증 순서 | 단일 백테스트 → Plateau → WFA → MC → PromotionGate | 전체 파이프라인 검증용 |

파일럿 전략을 먼저 전체 파이프라인으로 통과시킨 후, 두 번째 전략(ath\_outlier)을 추가한다. 전략을 늘리기 전에 파이프라인 자체가 안정적임을 확인하는 것이 우선이다.

# **12. Mock OrderManager 검증 항목 (신규)**

실계좌 주문 전에 MockBroker 환경에서 다음 시나리오가 모두 통과해야 한다. 이 단계를 건너뛰고 실계좌로 가면 전략 버그보다 운영 버그가 더 위험하다.

| **시나리오** | **검증 내용** | **통과 기준** |
| --- | --- | --- |
| 정상 주문 생성 | 시장가/지정가 주문 생성 후 체결 | OrderResult.status = FILLED |
| 주문 취소 | 미체결 주문 취소 후 포지션 없음 | Position.qty = 0 |
| 부분 체결 | 수량 일부만 체결 처리 | Position.qty = 부분 수량 |
| 미체결 재시도 | 시간 초과 시 재주문 로직 | 최대 3회 재시도 후 포기 |
| 중복 주문 방지 | 동일 종목 동일 방향 동시 주문 차단 | DuplicateOrderError 발생 |
| 장 종료 강제 정리 | 15:30 이후 당일 미체결 전량 취소 | OrderManager.eod\_cleanup() 테스트 |
| API 장애 복구 | 네트워크 오류 시 지수 백오프 재시도 | 최대 3회 후 알림 발송 |
| Kill Switch | 긴급 정지 시 신규 주문 전면 차단 | place\_order()가 KillSwitchError 발생 |

# **13. Kill Switch (긴급 정지) 정책 (신규)**

자동매매 시스템에서 Kill Switch는 선택이 아니라 필수다. SCR-05/SCR-13에서 사용자가 수동으로 발동하는 것 외에, RiskManager가 조건 충족 시 자동으로도 발동해야 한다.

| **트리거** | **조건** | **자동 여부** | **조치** |
| --- | --- | --- | --- |
| 일 손실 한도 초과 | 당일 실현+미실현 손실 >= 계좌 1.5% | 자동 | 신규 진입 전면 차단 + 알림 |
| 연속 주문 실패 | 주문 실패 5회 이상 연속 | 자동 | 신규 주문 차단 + 알림 |
| API 응답 지연 | 브로커 API 응답 >= 10초 | 자동 | 신규 주문 차단 |
| 가격 결측 | 종가 수신 실패 | 자동 | 해당 종목 신규 진입 차단 |
| 포지션 불일치 | OrderManager vs 브로커 포지션 불일치 | 자동 | 전체 신규 주문 차단 + 사용자 승인 필요 |
| 사용자 수동 | SCR-05 [긴급 정지] 클릭 | 수동 | 전체 신규 주문 차단 |
| 재개 조건 | 위 조건 해소 + 사용자 [재개 승인] | 수동 | 관리자 권한 필수 |

원칙: Kill Switch는 "신규 진입 차단"까지 자동 적용. 보유 포지션 강제 청산은 반드시 사용자 명시적 승인 후에만 실행한다.

# **14. 적용 체크리스트 (v2.6.3)**

| **단계** | **작업** | **선결 조건** | **완료 기준** |
| --- | --- | --- | --- |
| PRE-0 | KRX Open API 인증키 신청 + 활용 신청 | — | API 테스트 호출 성공 |
| PRE-1 | as-of-date 유니버스 생성기 설계 확정 | §7 정책 승인 | generate(ref\_date) 스펙 문서화 |
| PRE-2 | 증권사 1개 선택 + API 신청 | §8 정책 승인 | 개발 서버 테스트 환경 확보 |
| PRE-3 | 알고리즘 계좌 규정 확인 | §9 체크리스트 | 법적 자문 또는 증권사 공식 확인 |
| PRE-4 | 파일럿 전략 pullback\_v3 상세 정의 | §11 기준 | 팀 리뷰 통과 |
| P0 | 프로젝트 골격 구축 | PRE-4 | Phase 0 완료 기준 충족 |
| P1 | 데이터 계층 + as-of-date 유니버스 | PRE-0 | Phase 1 완료 기준 충족 |
| P2 | 백테스트 + CostModel | P1 | Phase 2 완료 기준 충족 |
| P3 | 검증 엔진 | P2 | Phase 3 완료 기준 충족 |
| P4 | Mock 거래 + 전체 시나리오 | P3, PRE-2 | Phase 4 완료 기준 충족 |
| P5 | Live Small 진입 | P4, PRE-3 | 알고리즘 규정 확인 + PromotionGate 통과 |

# **15. 변경 영향도 요약**

| **v2.6.3 항목** | **v2.6.2 대비 변화** | **기존 데이터 호환** |
| --- | --- | --- |
| 데이터 소스 정책 | 정책 확정 (코드 없음) | 완전 호환 |
| as-of-date 유니버스 | DataQualityFilter 내부 설계 변경 | 재구현 필요 (기존 스키마 호환) |
| BrokerAdapter 인터페이스 | 신규 추상 계층 (코드 추가) | 기존 코드 영향 없음 |
| 알고리즘 계좌 규정 | 정책 확정 (코드 없음) | 완전 호환 |
| Phase 로드맵 | 계획 문서 (코드 없음) | 완전 호환 |
| 파일럿 전략 기준 | 전략 사양 확정 (코드 추가) | 신규 |
| Mock OrderManager | 신규 모듈 | 신규 |
| Kill Switch | RiskManager 확장 | 기존 LiveBacktestMonitor 트리거와 병합 |

# **16. 결론**

v2.6.3은 MAPS가 문서에서 코드로 넘어가는 첫 번째 관문이다. v2.6.1~v2.6.2가 "검증 체계가 약속한 수준을 실제로 보장"했다면, v2.6.3은 "그 체계를 실제 환경에서 만들 수 있도록 선결 조건을 확정"한다.

가장 먼저 막히는 지점은 데이터다. KRX API가 수정주가와 관리종목 상세 이력을 완전히 커버하는지 Phase 1 첫 주에 확인하고, 불완전하면 보완 소스를 즉시 확정해야 한다. 그 다음은 as-of-date 유니버스다. 이게 틀리면 모든 백테스트가 자동으로 look-ahead 편향을 가진다.

주문 기능(BrokerAdapter 실구현)은 Phase 4의 일이다. 그 전까지는 MockBroker만으로 전체 파이프라인을 검증한다. 이 순서를 지키는 것이 MAPS의 철학 — 나쁜 전략이 실계좌로 못 올라오게 막는 것 — 을 구현에서도 실천하는 방법이다.

# **부록 A. 용어집 (v2.6.1~v2.6.3 누적)**

| **용어** | **정의** |
| --- | --- |
| R (Risk Unit) | 초기 진입 시 스탑까지의 거리. 1R = (진입가 - 스탑가) |
| MDD | Maximum Drawdown. 자산곡선 최고점 대비 최대 하락 폭 |
| Ulcer Index | MDD의 빈도와 깊이를 동시에 반영하는 위험 지표 |
| Recovery Factor | 총수익 / MDD. 회복력 지표. 일반적으로 >= 3 양호 |
| Gain-to-Pain Ratio | 월별 수익 합 / 월별 손실 합 절댓값 |
| Plateau Score | 최적 파라미터 이웃 중 성과 75% 이상 + MDD 125% 이내 점 비율 × 100 |
| Tradeability Score | Robustness/Risk/Recovery/Return 서브스코어 가중합 (0~100) |
| Walk-Forward Analysis | IS-OOS를 슬라이드하며 파라미터 안정성을 검증하는 방식 |
| Block Bootstrap | 거래 순서 재배열 시 연속 거래의 군집성을 보존하는 셔플 방식 |
| DataQualityFilter | 데이터 수집 직후의 단일 게이트. 6개 기준으로 유니버스 정제 |
| as-of-date 유니버스 (v2.6.3) | 특정 기준일에 실제로 매수 가능했던 종목 집합을 재현하는 생성기 |
| BrokerAdapter (v2.6.3) | 특정 증권사 API를 교환 가능하게 하는 추상 인터페이스 |
| Kill Switch (v2.6.3) | 조건 충족 시 신규 주문을 전면 차단하는 긴급 정지 메커니즘 |
| 생존자 편향 | 폐지 종목을 유니버스에서 제외해 결과가 자동으로 좋아지는 현상 |
| Look-ahead Bias | 특정 시점에서 알 수 없었던 미래 정보가 신호 산출에 새는 현상 |

**MAPS v2.6.3 | Implementation Readiness (구현 착수 준비 통합본)**
