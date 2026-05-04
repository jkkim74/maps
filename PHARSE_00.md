# Phase 0: 프로젝트 골격 구축

CLAUDE.md를 읽었다면 아래 작업을 순서대로 진행해줘.

## 작업 목표
MAPS 프로젝트의 기반 구조를 만든다.
이후 모든 모듈이 의존하는 상수, DB 연결, 예외, 마이그레이션을 먼저 완성한다.

## Step 1: 폴더 구조 생성
maps/ 하위 모든 패키지 폴더를 만들고 __init__.py 추가.
(CLAUDE.md의 폴더 구조 그대로)

## Step 2: common/constants.py 작성
아래 항목 모두 포함:
- ALLOWED_MDD 딕셔너리 (전략군별 expected, mc_p95_limit)
- TREND_STRENGTH_BUCKETS 리스트 (S1~S5, [lo, hi) 반개구간)
- MIN_VALID_MAS = 5
- PLATEAU_GRADES 딕셔너리
- STRATEGY_GROUP_MAP (전략 ID -> 전략군)
- PROMOTION_GATES (단계별 통과 조건, mc_within_limit 포함)
- WEIGHT_PRESETS (conservative/balanced/growth)
- TRADEABILITY_THRESHOLDS (mock_candidate=60, live_candidate=75)

## Step 3: common/exceptions.py 작성
MAPSError 기반 8개 커스텀 예외 클래스.

## Step 4: common/db.py 작성
SQLAlchemy 2.x engine, Session 팩토리.
MAPS_DB_URL 환경변수 없으면 sqlite:///./maps.db 기본값.

## Step 5: Alembic 초기화 + baseline 마이그레이션
v2.6.2 설계서 §16 + v2.6.3 설계서 §10의 모든 테이블 포함.
(security_metadata, universe_quality_log, parameter_plateau_results,
walk_forward_results, monte_carlo_sequence_results, promotion_history,
tradeability_weight_log, order_log, kill_switch_log, collection_log,
strategy_param_log, cost_model_assumptions)

## Step 6: pytest 세팅
tests/conftest.py 작성 (인메모리 SQLite 픽스처).
test_constants.py — ALLOWED_MDD, PROMOTION_GATES 구조 검증.

## 완료 기준
- pytest 실행 시 에러 없이 통과
- alembic upgrade head 성공
- from maps.common.constants import ALLOWED_MDD 정상 작동