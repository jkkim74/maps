# 모의계좌 비용 정산 구현 인수인계 — 2026-10-12

## 재개 세션 현재 상태 — 2026-10-12

아래 `최초 중간 저장 기록`보다 이 절이 우선한다. 체크포인트 `b9d99c0` 이후
잔여 회귀 테스트와 실제 브라우저 확인, 모듈 문서 보완, 최종 자체 검토를 완료했다.
최종 전체 검증은 **2009 passed, 2596 warnings (614.48초)**다.
독립 최종 리뷰와 실제 KIS 비용 자료 검증은 아래 운영 전 TODO로 남긴다.

- 현재 worktree: `D:/workspace/maps/maps/.worktrees/paper-cost-settlement`
- 브랜치: `codex/paper-cost-settlement`, 원격 체크포인트 위의 미커밋 변경
- Python: `D:/workspace/maps/maps/.venv/Scripts/python.exe`
- 원본 `master`의 ETF/백테스트 변경과 사용자 파일은 보존했다. fetch는 완료했지만
  원본 pull은 `maps/strategy/live_rules.py`의 로컬 변경과 겹쳐 적용하지 못했다.
- 운영 DB·설정·주문·전략 상태는 변경하지 않았다. 병합·배포·활성화하지 않았다.

### 재현 후 수정한 결함

1. 교체 계좌 명세서가 주문 비용 배분 한도를 초기화하던 문제:
   문서 ID 대신 해당 관측 구간에 완전히 포함된 주문들의 최신 비용을 합산한다.
   기존 200 + 신규 200 > 계좌 실제 비용 360은 거절하고, 신규 160은 허용한다.
2. 과거 수량 차이 보정 때문에 자동 정산이 거절되어도 receipt가 남던 문제:
   receipt·보정 변경·성공 이벤트를 savepoint로 묶어 모두 취소한다.
   보정 변경 직후 강제 예외를 주입하는 테스트도 추가했다.
3. 후지모토 원장에서 비용 감소 정정을 거절하면 HTTP 500이 되던 문제:
   HTTP 409로 반환하고 계좌 근거·receipt·주문 비용을 함께 rollback한다.
4. API 금액에 저장소 `Numeric(24,4)` 한도를 적용해 초과 입력은 422로 거절한다.
5. 기존 후지모토 만료 테스트는 실행일이 고정 만료일을 지난 뒤 실패했다.
   테스트 생성 시각을 만료일 이전으로 고정했으며 운영 코드는 변경하지 않았다.

`tests/test_paper_cost_reconciliation.py`에서 실제 reconcile 경로의 체결 감소,
수량 불일치, unknown/open 주문, 미확정 체결금액, 과거 수량 보정 차단을 확인한다.
관찰→자동 전환과 새 DB 세션에서도 중복 차감하지 않는지 검증한다.
기존 정산 테스트의 주문 전용 receipt/current coverage 차단 계약도 유지한다.

### 검증과 재실행

- 최종 전체 `tests maps/tests`: **2009 passed, 2596 warnings (614.48초)**, 종료 코드 0.
  최종 제품 코드/테스트 편집을 마친 뒤 실행했다. 경고는 NumPy/pandas, Starlette,
  Alembic 등의 사용 중단 경고와 기존 테스트의 SQLAlchemy/리스크 경고를 포함한다.
- 정산·후지모토·KIS·마이그레이션·UI·문서 집중 실행: **252 passed, 19 warnings (327.37초)**.
  이 실행 후 추가된 마지막 원자성 회귀는 아래 22개 실행 및 최종 전체 실행에 포함한다.
- 자동 정산·누적 한도·API 원자성 및 기존 risk API: **22 passed**, 기존 Starlette 경고 1개.
- 변경된 전체 Python 파일 compileall, `node --check static/js/paper_costs.js`,
  `git diff --check` 통과.
- 최종 결과 문서 갱신 후 `test_docs_index.py`: **24 passed (0.35초)**.
- [브라우저 캡처와 확인 범위](../evidence/paper-cost-20261012/README.md):
  실제 템플릿/JS를 합성 계좌 자료로 렌더링했다. 계좌 정산과 과거 주문 보완 화면을
  확인했으며 임시 서버와 탭은 종료했다. 실제 계좌 비용 수집을 검증한 것은 아니다.
- 체크포인트 전체 실행은 테스트 실패 1개를 재현했지만 마지막 pytest 정리 과정에서
  기존 Windows `pytest-current` 접근 권한 오류가 났다. 최종 검증에는 전용 basetemp를 사용한다.
- 이번 재개 세션은 단일 에이전트의 최종 자체 검토다. 이전 독립 리뷰의 쟁점에
  회귀 테스트를 보강했지만 새 독립 리뷰 종료 확인을 받았다고 주장하지 않는다.

```powershell
Set-Location D:/workspace/maps/maps/.worktrees/paper-cost-settlement
& D:/workspace/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py tests maps/tests -q --tb=short --basetemp=$env:TEMP/maps-paper-cost-full-final-20261012 --junitxml=$env:TEMP/maps-paper-cost-full-final.xml
node --check static/js/paper_costs.js
git diff --check
```

격리 runner는 dotenv를 배제하고 mock 설정, 임시 DB/lock, 외부 네트워크 차단을 사용한다.
로그와 JUnit 결과는 시스템 TEMP의 `maps-paper-cost-full-final.log/.xml`에 있다.
운영 전 TODO는 실제 KIS paper 비용 필드 및 **18:00 KST 이후 2회 안정 관측** 가정의
검증, 독립 최종 리뷰다. 기본 observe를 유지하며 운영 적용 시 migration 0042가 필요하다.
내역이 쌓인 비용 감사 테이블을 downgrade하거나 삭제하지 않는다.

## 최초 중간 저장 기록 — 아래 내용은 재개 전 이력

### 당시 상태

사용자가 구현 도중 **모든 브랜치 push 및 다른 세션을 위한 인수인계**를 요청했다.
이 브랜치는 구현 중간 저장본이다. **전체 검증 완료·병합·배포 승인을 뜻하지 않는다.**
운영 DB 변경, 운영 설정 변경, 주문 제출, 전략 활성화는 하지 않았다.

- 저장소: `https://github.com/jkkim74/maps.git`
- 작업 브랜치: `codex/paper-cost-settlement`
- 시작 커밋: `0f02fa8ff09de6e1761447a5e5ac92139e8ef39f`
- 구현 worktree: `C:/Users/jack/.codex/worktrees/84af/maps`
- 원본 worktree: `D:/workspace/maps/maps/maps` (`master`)
- 실행 계획: [2026-10-12-paper-cost-settlement.md](../superpowers/plans/2026-10-12-paper-cost-settlement.md)
- 원본의 미추적 `docs/diary/20260914.txt`, `20260916.txt`는 사용자 파일로 보존하며 커밋하지 않는다.
- 테스트의 계좌 번호·거래·잔고·비용은 가상 데이터다. 실제 운영 계좌 데이터나 인증정보를 문서에 추가하지 않는다.

## 다음 세션 시작

같은 컴퓨터에서는 이미 존재하는 구현 worktree에서 이어서 작업한다.

```powershell
Set-Location C:/Users/jack/.codex/worktrees/84af/maps
git status --short
git log -3 --oneline
git fetch origin
```

다른 컴퓨터에서는 저장소를 받은 뒤 `git switch --track origin/codex/paper-cost-settlement`로 시작한다.
이미 브랜치가 존재하면 `git switch codex/paper-cost-settlement`를 사용한다.
`index.md` → 관련 패키지 `CLAUDE.md` → 소스를 읽는 저장소 규칙과 `.agent/PLANS.md`를 따른다.
이 브랜치를 master에 병합하거나 운영 마이그레이션을 실행하는 작업은 이번 인수인계에 포함하지 않는다.

## 문제와 승인된 방향

기존 계좌 대조는 수수료 차이가 생기면 신규 진입을 차단하지만, 미분류 보정 하나가 있으면
후속 비용이 더 발생해도 과거 보정 금액만 화면에 남는다. KIS 모의 계좌의 비용 이력이
지원되지 않는다는 표시는 후지모토 모의 체험 준비 판정에서도 차단 사유가 된다.

승인된 설계는 **실제 비용 자료에 의한 자동 정산 + 증권사 명세서에 의한 수동 예외 처리**다.
현금 잔차나 수수료율로 실제 비용을 추정하면 안 된다. 계좌합계 비용으로 주문별 수수료·세금을
임의 배분하면 안 된다. 수량 불일치, 미확인 주문, 자료 누락 등 기존 안전장치는 유지한다.

## 구현한 파일과 동작

| 영역 | 파일 | 현재 구현 |
|---|---|---|
| 비용 수집 | `maps/execution/broker_adapter.py`, `kis_adapter.py` | `PositionSnapshot.cost_observation` 선택 필드. 기존 잔고 응답에서 `thdt_tlex_amt`, `bfdy_tlex_amt` 수집. 추가 HTTP 없음. 누락·음수·비유한 값은 미확인. 전일 값은 진단용 |
| 설정 | `maps/common/settings.py` | `MAPS_PAPER_COST_RECONCILIATION_MODE=observe\|auto`, 기본 `observe`, KIS paper만 적용 |
| 저장 | `maps/common/models.py`, `alembic/versions/0042_paper_cost_settlement.py` | 불변 `PaperCostEvidence`, 멱등 `PaperCostSettlement`. 기존 데이터 소급 변경 없음. 이력이 있는 테이블 downgrade 거절 |
| 계좌 정산 | `maps/execution/paper_costs.py`, `reconciliation.py` | 최신 잔차/수량/기확정 보정/정산 내역 계산. 실제 비용과 안정된 관측 2회에 의한 자동 차감. 명세서 미리보기·원자적 확정, 의미 기반 fingerprint 및 idempotency key |
| 기존 분류 | `maps/execution/safety_admin.py` | KIS paper의 신규 fee 분류는 명세서 정산 경로 요구. 과거 confirmed fee는 유지 |
| API | `maps/api/risk.py`, `schemas.py` | GET safety에 `paper_costs`, admin POST `/execution-safety/settlements/preview`, `/commit`. 현재 설정 계좌만 접근, extra 필드/비정상 금액/근거 누락 거절 |
| 후지모토 | `maps/fujimoto/service.py` | `settle_costs(commit=False)`로 계좌 정산과 같은 트랜잭션. 실제 대조 완료일 때만 알려진 unsupported 기능 표시를 진단 정보로 취급 |
| UI | `templates/risk.html`, `static/js/paper_costs.js`, `templates/fujimoto.html` | 현재 잔차와 과거 미분류 기록 분리, 명세서 URL/SHA-256/총액/사유 입력, preview→commit, 과거 주문만 보완하는 모드, 후지모토에서 정산 화면 링크 |

계좌 정산 commit은 주문을 보내거나 전략을 켜지 않는다. 결과는 `awaiting_reconciliation`이며
다음 계좌 동기화에서 안전 상태를 다시 판정한다. 계좌와 후지모토 원장은 실패 시 함께 rollback한다.

계좌 명세서 총액은 화면의 **기준 관측부터 최신 관측까지**를 대상으로 한다.
기확정 fee 및 기존 정산 receipt ID를 명시적으로 매핑하여 미반영 금액만 차감한다.
일자만 보고 당일 전체 비용을 그대로 입력하면 기준 관측 전에 반영된 비용까지 포함할 수 있으므로
실제 시간 구간을 검토해야 한다. URL과 해시는 운영자가 검토한 문서의 출처 표기이며, 서버가 문서를
다운로드하거나 진위를 자동 검증하지는 않는다.

과거 주문 보완은 `account_evidence_id`로 이미 정산된 계좌 근거와 당시 관측을 참조한다.
계좌 현금을 추가로 차감하지 않는다. 주문 전체 비용 합계가 계좌 근거 총액을 넘으면 거절한다.
정산 구간 시작 전에 일부 체결된 주문은 누적 비용을 안전하게 배분할 수 없어 재구성 검토가 필요하다.

## 검증과 리뷰 상태

최종 커밋 전 아래 focused 명령을 다시 실행하여 **138 passed, 18 warnings (77.33초)**를 확인했다.
이 결과는 마지막 UI 변경과 가상 계좌 테스트 데이터 변경을 포함한다.
이후 UI/저장 테스트의 예제 값 정리 및 문서 트리 보완 후 재실행한
`test_paper_cost_ui.py test_paper_cost_persistence.py test_docs_index.py`는 **32 passed (1.05초)**다.
변경 Python 파일 `py_compile`, `node --check`, `git diff --check`도 오류 없이 종료했다.
그 이전의 확인된 결과는 다음과 같다. 서로 겹치는 테스트이므로 숫자를 더하지 않는다.

- 어댑터·저장·마이그레이션 focused: 78 passed, 기존 Alembic 경고 18개.
- 정산 핵심: 28 passed. 반복 동기화, 관찰/자동 모드, 명세서 중복, 변경된 잔고,
  시각만 바뀐 관측, 과거 주문 보완, 후지모토 정산 후 강제 실패 rollback, 날짜 경계 포함.
- API + 기존 risk API: 30 passed.
- UI JavaScript 실행 및 기존 후지모토 UI: 12 passed.
- 후지모토 capability 판정: 3 passed, 37 deselected.
- `git diff --check` 및 JavaScript 구문 검사는 통과했다.

**전체 `tests maps/tests` 실행은 완료하지 못했다.** 코드 수정과 겹쳐 실행되었고 진행 출력에
실패 표시가 있었으며, 사용자 인수인계 요청에 따라 중단했다. 실패 요약을 얻지 못했으므로
모두 해결되었다고 가정하면 안 된다. `.pytest_cache`는 병행 focused 실행 결과와 섞일 수 있다.
전체 실행의 실패를 재현하여 원인과 결과를 별도로 기록하는 것이 다음 세션의 우선 과제다.

독립 리뷰에서 발견하여 수정한 사항:

1. 기준일 변경 후 과거 주문의 비용을 보완할 수 없던 문제 → 원래 계좌 근거와 당시 관측 사용.
2. 체결 수량 감소를 거절하기 전에 자동 receipt가 저장될 수 있던 문제 → 선행 검사 추가.
3. 과거 주문 전용 receipt가 현재 계좌 비용의 완전성을 증명하던 문제 → 계좌 정산 receipt만 인정.
4. 관찰 모드에서 모순된 비용 관측을 정상 기준으로 흡수하던 문제 → 비용 미확인 시 기준 진행 차단.

리뷰 1·2 수정 확인은 받았으나 마지막 3·4 수정에 대한 독립 재검토 종료 확인은 아직 없다.

## 남은 작업 — 권장 순서

1. 아래 전체 테스트를 **편집 중이 아닌 최종 트리**에서 다시 실행하고 실패를 조사한다.
   수정된 테스트/모듈에 대한 focused 결과와 전체 결과를 구분해서 기록한다.
2. 선행 체결 수량 감소 검사에 대해 **실제 reconcile 경로에서 receipt/보정이 전혀 저장되지 않는**
   회귀 테스트를 추가한다. 현재 직접 테스트는 다른 안전 경로에 집중되어 있다.
3. 계좌 근거 공유 주문들의 누적 배분 한도, 주문 전용 receipt의 현재 coverage 불가,
   관찰→자동 전환, 재시작 후 멱등성, 알 수 없는 주문/수량 차이를 최종 검토한다.
4. `index.md`, `maps/common/CLAUDE.md`, `maps/execution/CLAUDE.md`, `maps/api/CLAUDE.md`,
   `maps/fujimoto/CLAUDE.md`, `tests/CLAUDE.md`에 동작·운영 절차를 반영한다.
   현재 ExecPlan, 인수인계, index 링크, execution 파일 트리만 갱신했다.
   `.env.example`은 이 checkout에 없어 생성하지 않았다.
5. 개선된 정산 UI의 실제 브라우저 렌더링을 다시 확인하고 스크린샷을 남긴다.
   이전 로컬 가상 화면에서는 DOM·표시를 확인했으며, 밀집된 입력 배치를 개선하는 CSS를 이후 추가했다.
   개선 후에는 Node 회귀 검사만 완료했고 스크린샷 재확인은 남아 있다.
6. 독립 최종 리뷰와 diff/비밀정보 검사를 마치고 ExecPlan 완료 여부를 갱신한다.

```powershell
Set-Location C:/Users/jack/.codex/worktrees/84af/maps
& D:/workspace/maps/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py tests/test_paper_costs.py tests/test_paper_cost_api.py tests/test_paper_cost_persistence.py tests/test_paper_cost_ui.py tests/test_fujimoto_ui.py tests/test_kis_adapter.py tests/test_migrations.py -q --tb=short
& D:/workspace/maps/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py tests maps/tests -q --tb=short
node --check static/js/paper_costs.js
git diff --check
```

격리 runner는 `.env`를 읽지 않고 외부 네트워크를 차단하며 임시 SQLite/계좌 lock을 사용한다.
Python 3.11+ 및 기존 가상환경을 사용한다. 운영 DB에 테스트나 마이그레이션을 실행하지 않는다.

## 운영 적용 전 확인할 제한

- 실제 KIS paper 응답의 당일 비용 필드가 유효하게 채워지는지는 아직 측정하지 않았다.
  공식 KIS 예제에 필드 정의가 있다는 것과 실제 계좌 응답에서 사용 가능한 것은 다르다.
- 날짜를 넘는 자동 정산은 현재 **18:00 KST 이후 안정된 2회 관측을 마감 비용으로 간주**한다.
  broker의 최종 확정 flag가 없으므로 이 가정과 늦은 정정 동작을 실제 자료로 검증해야 한다.
  중간 거래일 자료가 빠지거나 비용이 감소하면 자동으로 채우지 않는다.
- 기본 observe에서도 미확인 비용은 차단을 유지한다. auto는 증거 요건을 완화하는 스위치가 아니다.
- 애플리케이션 적용에는 additive migration 0042가 필요하지만 이번 작업에서 운영 적용하지 않았다.
- observe로 되돌리면 새 자동 차감만 중단한다. 이미 저장된 근거·정산 내역은 삭제하지 않는다.
- 후지모토 활성화에는 비용 외에도 선별·시세·전용 예산·소유자·명시적 동의 조건이 계속 필요하다.

## 임시 도구와 정리

로컬 가상 UI용 `C:/Users/jack/AppData/Local/Temp/maps-paper-cost-ui.py`는 저장소 밖에 있다.
운영 DB나 broker를 사용하지 않는 고정 응답 서버였고, 인수인계 시 서버를 중지했다.
이전 세션의 에이전트·프로세스 ID에 의존하지 말고 저장된 코드와 문서를 기준으로 재개한다.
