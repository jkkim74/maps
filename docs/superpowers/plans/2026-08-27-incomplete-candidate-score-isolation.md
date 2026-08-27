# Incomplete Candidate Score Isolation Implementation Plan

> **For agentic workers:** Execute each task with TDD and verify before committing.

**Goal:** 완성되지 않은 후보 점수를 매매 후보 순위와 AI 예산에서 격리하면서 감사 정보는
별도 목록에 보존한다.

**Architecture:** 기존 `score_ready`와 `score_coverage_ratio`를 공통 판정으로 묶어 후보 저장,
AI 대상, API, 다이제스트가 같은 규칙을 사용한다. 기존 응답 필드는 유지하고 완성·미완성 목록과
집계 필드만 추가한다.

**Tech Stack:** Python 3.12, SQLAlchemy, FastAPI/Pydantic, Jinja/vanilla JavaScript, pytest.

## Global Constraints

- DB 마이그레이션, 백필, 새 설정이나 외부 의존성을 추가하지 않는다.
- 자동 BUY/SELL 안전 게이트와 주문 미리보기 감사 행의 동작을 바꾸지 않는다.
- 미완성 부분 산출값을 추천점수나 비교 가능한 순위로 표현하지 않는다.
- 현재 작업공간의 기존 삭제·미추적 파일을 수정하거나 커밋하지 않는다.

### Task 1: 공통 판정·저장·AI

**Files:** `maps/ops/candidate_selection.py`, `maps/ops/scheduler.py`,
`maps/ai/scoring_service.py`와 대응 테스트.

- [ ] 완성도 경계와 SQL 표현식 테스트를 먼저 실패시킨다.
- [ ] `candidate_score_complete()`와 `candidate_score_complete_expression()`을 최소 구현한다.
- [ ] 저장 상한에서 완성 비신호 행이 부분점수 고득점 행보다 우선하고 미완성 신호 행은
  보존되는 테스트를 실패시킨 뒤 정렬을 수정한다.
- [ ] 미완성 후보가 AI 호출·예약을 만들지 않는 테스트를 실패시킨 뒤 대상 조건을 수정한다.
- [ ] 관련 테스트를 통과시키고 커밋한다.

### Task 2: 후보 API와 다이제스트

**Files:** `maps/api/schemas.py`, `maps/api/candidates.py`, `maps/ops/daily_digest.py`와 대응 테스트.

- [ ] 후보 API가 완성·미완성 목록과 필터 전 집계를 분리하는 실패 테스트를 작성한다.
- [ ] `CandidatesResponse`에 `ready_count`, `incomplete_count`,
  `incomplete_candidates`를 추가하고 라우터를 구현한다.
- [ ] 다이제스트가 완성 후보를 우선하고 같은 티커를 미완성 목록에 중복하지 않는 실패 테스트를
  작성한다.
- [ ] `DailyDigest`에 `candidate_ready_total`, `candidate_incomplete_total`,
  `incomplete_candidates`를 추가하고 빌더를 구현한다.
- [ ] 관련 테스트를 통과시키고 커밋한다.

### Task 3: 화면·매매일지·문서

**Files:** `templates/candidates.html`, `static/js/app.js`, `.claude/commands/blog.md`,
`maps/ops/CLAUDE.md`와 대응 테스트.

- [ ] 화면이 두 목록과 데이터 품질 정보를 구분하는 실패 테스트를 작성한다.
- [ ] 완성 후보 표와 미완성 감사 표를 렌더링하고 contrarian 전략 선택 항목을 추가한다.
- [ ] 매매일지 계약 테스트를 먼저 실패시킨 뒤 미완성 후보 서술 규칙을 추가한다.
- [ ] 패키지 문서를 실제 동작과 맞추고 관련 테스트를 통과시킨 뒤 커밋한다.

### Task 4: 전체 검증과 인계

- [ ] 집중 테스트, `pytest tests -q`, `pytest maps/tests -q`, compileall, Alembic head,
  `git diff --check`를 실행한다.
- [ ] 계획 요구사항과 diff를 자체 리뷰하고 발견된 결함은 새 실패 테스트부터 수정한다.
- [ ] `HANDOFF.md`에 구현·검증 결과와 미배포 상태를 기록하고 최종 커밋한다.
