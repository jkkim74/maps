# MAPS — Market-Adaptive Profit Management System

## 프로젝트 개요
검증 중심 자동매매 플랫폼. "좋아 보이는 전략을 빨리 돌리는 것"이 아니라
"나쁜 전략이 실계좌로 못 올라오게 막는 것"이 핵심 철학.

## 기술 스택
- Python 3.11+, FastAPI, SQLAlchemy 2.x, Alembic
- APScheduler (배치), pytest (테스트)
- DB: SQLite 기본, MAPS_DB_URL 환경변수로 PostgreSQL 전환 가능

## 폴더 구조
maps/
  common/          # constants.py, db.py, exceptions.py
  data_quality/    # universe_filter.py (as-of-date 생성기)
  data/            # collector.py, krx_adapter.py
  strategy/        # base.py, pullback_v3.py
  backtest/        # engine.py, cost_model.py
  validation/      # plateau.py, walk_forward.py, monte_carlo.py
  promotion/       # gate.py
  execution/       # broker_adapter.py, mock_broker.py, order_manager.py
  risk/            # manager.py (Kill Switch 포함)
  dashboard/       # strategy_compare.py
  tests/

## 핵심 설계 원칙 (반드시 준수)
1. DataQualityFilter는 as-of-date 생성기. generate(ref_date) 형태.
   ref_date 이후 정보(폐지, 정지)는 절대 참조하지 않는다.
2. WalkForwardAnalyzer 통과 조건 3개는 AND 조건:
   - sharpe_mean > 0 (필수, 없으면 안정적 손실 전략 통과)
   - 음수 fold <= 1개
   - OOS/IS G2P >= 0.6
   (std/|mean| <= 0.5 조건은 제거됨 — 변동성 자체를 통과 기준으로 삼으면
   고수익·고변동 전략이 불합리하게 탈락하고, MDD/MC 가 실질 위험을 이미 통제)
3. PromotionGate는 KeyError로 죽으면 안 됨. 모르는 전략 ID나
   없는 메트릭은 "fail with reason"으로 처리.
4. BrokerAdapter는 추상 인터페이스. MockBroker만 Phase 4까지 사용.
   실증권사 어댑터는 Phase 5에서만 연결.
5. Kill Switch는 신규 진입 차단만 자동. 보유 청산은 사용자 승인 필수.
6. audit 로그 (promotion_history, universe_quality_log, order_log,
   kill_switch_log)는 Day 1부터 스키마 존재.

## 허용 MDD (전략군별)
pullback_short:   mc_p95_limit = 18%
ath_outlier:      mc_p95_limit = 35%
multi_asset:      mc_p95_limit = 22%
donchian_research:mc_p95_limit = 30%
portfolio_total:  mc_p95_limit = 28%

## Tradeability 가중치 프리셋
balanced (기본): robustness=0.30, risk=0.30, recovery=0.20, return=0.20
승격 임계: mock_candidate=60, live_candidate=75 (가중치와 무관, 고정)

## 코딩 규칙
- 모든 코드는 type hint 필수
- 함수/클래스마다 docstring 작성
- 예외는 common/exceptions.py의 커스텀 예외 사용
- 테스트 파일은 tests/ 하위, pytest 사용
- 환경변수는 .env 파일, python-dotenv로 로드

## 참고 문서
- 기획안 v2.6.3: 정책·Phase 로드맵·파일럿 전략 기준
- 설계서 v2.6.3: 클래스 사양·코드 예시·DB 스키마
- 화면설계서 v2.6.2: 14개 화면 와이어프레임·컴포넌트 정의