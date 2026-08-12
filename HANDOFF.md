# HANDOFF

## 8/12 종목분석 이력·현재가 갱신·전략매매 UX 구현 완료

- 작업 브랜치 `feat/stock-analysis-history`에서 설계·계획의 7개 작업을 구현했다. 로컬 브랜치는
  원격 `origin/feat/stock-analysis-history`보다 7커밋 앞서 있으며 **push·master 병합·운영 배포는
  하지 않았다.** 운영 서버는 여전히 아래 8/11 절의 `3e7a153` / migration `0021` 상태다.
- 신규 `StockAnalysisHistory`와 migration **`0022_stock_analysis_history`**를 추가했다. 같은 종목을
  반복 분석해도 매번 새 행을 추가하며 `snapshot`, AI 원고, 구조화 `trade_plan`, 분석 당시 가격은
  생성 후 바꾸지 않는다. 현재가 갱신은 `latest_*`와 `price_refreshed_at`만 변경한다.
- SSE와 단일 응답 분석은 완료 시 이력을 정확히 한 번 저장한다. SSE 저장만 실패하면 분석 결과는
  계속 표시하고 `history_error`를 전달한다. 목록은 최신순 경량 응답, 상세는 저장 원본 전체를
  반환한다. 현재가는 브로커를 우선하고 실패 시 최신 OHLCV로 폴백하며 두 소스 모두 없으면 기존값을
  보존한 채 503을 반환한다.
- 독립 종목분석 화면에 이력 목록·상세·재분석을 연결했다. 상세는 저장된 분석과 매매가격을 먼저
  복원한 뒤 현재가·등락·계획 가격 거리를 비동기 갱신한다. 분석 시각은 DB의 UTC naive 값을 API에서
  명시적 UTC로 보정해 브라우저 KST 표시가 9시간 어긋나지 않게 했다.
- 전략매매 입력을 공통 행(시장/총액/목표/손절)과 진입가 행(1/2/3차)으로 분리했다. 분석 종목의
  KOSPI/KOSDAQ 시장도 팝업에 복원한다. `/trade-limits`가 예산 없이 현재 계좌의 안전 최대금액과
  최소 주문가능 금액을 계산하며, 매매 방식·가격 선택 후 총액과 preview를 자동 채운다. 입력이
  바뀌면 이전 preview와 무장 상태를 즉시 무효화하고 최종 `/arm-plan`은 최신 계좌·게이트·중복·한도를
  다시 검증한다.
- 구현 커밋: `1f3656d`(schema), `b27aeb2`(history API), `5c8dff8`(analysis persistence),
  `143168c`(history UI), `e3e78b3`(budget-free limits), `50cde5e`(auto budget/layout),
  `be946f8`(UTC/market review fix).
- 검증:
  - 집중 스위트 **81 passed**(경고 12건)
  - 전체 Python **803 passed**(경고 22건)
  - `node --check static/js/stock-analysis.js`, `git diff --check` 통과
  - 인메모리 SQLite·Mock 브로커 앱 스모크: `/stock-analysis` 200, 동일 ticker 이력 2건 최신순,
    저장 상세·trade plan 복원, 현재가 72,000원/기준종가 70,000원 갱신, `/trade-limits` 무쓰기 확인
  - jsdom 상호작용 스모크: 저장 상세 복원 → 3분할 선택 → 안전금액 10,000,000원 자동입력 →
    preview 자동 호출 → 최종 무장 버튼 활성화 확인
- 배포 시 PostgreSQL 전체 백업 후 `alembic upgrade head`로 `0022_stock_analysis_history`를 적용하고,
  운영 KIS/Bedrock 환경에서 분석 1회 저장·현재가 오버레이·재분석 2행 누적을 별도 확인해야 한다.

## 8/11 종목분석 이력·현재가 갱신·전략매매 UX — 설계·계획 완료

- 작업 브랜치: `feat/stock-analysis-history`, 격리 worktree:
  `.worktrees/stock-analysis-history`. 구현 코드는 아직 시작하지 않았고 원격 push·운영 배포도
  하지 않았다. 운영 서버는 바로 아래 절의 `3e7a153` 상태와 동일하다.
- 사용자 문제 확인:
  - 종목분석 결과는 현재 SSE 응답으로 화면에만 표시되어 다른 메뉴를 다녀오면 사라진다.
  - 같은 종목을 재분석해도 독립 이력이 쌓이는 저장소가 없다.
  - 3분할 선택 시 숨김 입력칸이 하나의 4열 자동 그리드에 나타나면서 줄 배치가 밀린다.
  - 총 매수금액을 먼저 추측해서 입력한 뒤 안전한도를 계산하는 흐름은 순서가 반대다.
- 승인된 데이터 경계:
  - 전체 분석마다 별도 `StockAnalysisHistory` 행을 추가하고 같은 ticker도 중복 보관한다.
  - 분석 당시 snapshot, AI 원고, 구조화 trade plan은 생성 후 바꾸지 않는다.
  - 상세를 열 때 현재가·기준 종가·등락·매매계획 가격까지의 거리와 갱신 시각만 다시 조회한다.
  - 가격 조회 실패 시 저장된 상세는 계속 보여주고 마지막 확인값임을 표시한다.
  - 재분석만 새 행을 만들며 가격 갱신은 기존 행의 `latest_*` 열만 변경한다.
  - 상세의 `매매 설정`은 저장된 구조화 매수가·목표가·손절가를 그대로 복원한다.
  - `AnalysisPick`은 최종 승인된 전략매매 실행 상태로 계속 분리한다.
- 승인된 화면 흐름:
  - 종목분석 검색창 아래에 최신순 이력 목록을 표시하고, 행의 `상세`를 누르면 저장된 분석을
    즉시 복원한 뒤 현재가 오버레이를 비동기로 갱신한다.
  - `재분석`은 기존 전체 SSE 분석을 실행해 새 이력 행을 추가한다.
  - 전략매매 입력은 공통 행(시장/총액/목표/손절)과 진입가 행(1/2/3차)을 분리한다.
  - 매매 방식 선택 직후 서버가 안전 최대금액을 먼저 계산해 총 매수금액에 자동 입력하고,
    사용자는 더 낮은 금액으로 변경할 수 있다. preview와 최종 arm은 계속 서버에서 재검증한다.
- 설계 문서: `docs/superpowers/specs/2026-08-11-stock-analysis-history-design.md`
  (`941b23c`). 구현 계획:
  `docs/superpowers/plans/2026-08-11-stock-analysis-history.md` (`0a4babf`).
- 구현 계획은 7개 TDD 작업이다: ① 모델·`0022_stock_analysis_history` migration,
  ② 저장/목록/상세/가격 API, ③ SSE 1회 저장, ④ 이력 화면 복원, ⑤ budget 없는 안전한도 API,
  ⑥ 안전금액 자동입력·3분할 정렬, ⑦ 전체 검증·HANDOFF.
- 격리 worktree의 구현 전 기준 검증: 전체 Python **790 passed**(경고 13건).
- 다음 시작점: 구현 방식(작업별 subagent 또는 현재 세션 inline)을 정한 뒤 계획 Task 1의
  실패 테스트부터 시작한다. 마이그레이션이 포함되므로 실제 배포 시 운영 DB 백업과
  `alembic upgrade head`가 필수다.

### 다음 세션 재개 지침

1. 아래 worktree로 이동한다.

   ```powershell
   Set-Location D:\workspace2\maps\maps\maps\.worktrees\stock-analysis-history
   git branch --show-current   # feat/stock-analysis-history
   git status -sb              # HANDOFF 커밋 후 clean 예상
   ```

2. 설계 문서와 구현 계획을 순서대로 읽는다. 설계 탐색과 대안 선택은 이미 사용자 승인을
   받았으므로 다시 시작하지 않는다.

   ```text
   docs/superpowers/specs/2026-08-11-stock-analysis-history-design.md
   docs/superpowers/plans/2026-08-11-stock-analysis-history.md
   ```

3. 별도 subagent 요청이 없으면 현재 세션 inline 방식으로 계획 Task 1부터 실행한다. 첫 작업은
   `tests/test_stock_analysis_history_model.py`와 `tests/test_migrations.py`에 실패 테스트를 쓴 뒤
   실제로 RED를 확인하는 것이다. production model이나 migration을 먼저 작성하지 않는다.
4. 이 worktree의 ignored `maps.db`는 기준 테스트용 로컬 DB다. 운영 DB가 아니며 커밋하지 않는다.
5. 메인 작업공간 `D:\workspace2\maps\maps\maps`에는 사용자의 기존 변경이 남아 있다:
   `docs/blog_series_backtest/11_눌림목_전략_부검기.txt` 삭제와 `docs/diary/` 미추적 파일.
   이번 기능에서 수정·삭제·stage하지 않는다.
6. 현재 원격/운영 `master`는 `3e7a153`, 서비스 active, alembic
   `0021_analysis_pick_split_plan (head)`, 내·외부 health 200이다. 이 기능 브랜치의 설계·계획·
   HANDOFF는 아직 원격과 운영에 없다.
7. 구현 완료 전에는 기존 분석 이력 데이터가 없다는 사실을 전제로 한다. migration에 임의
   backfill이나 `AnalysisPick` 복사를 추가하지 않는다.

## 8/11 종목분석 가격 → 전략매매 팝업 인계 수정

- 작업 브랜치 `fix/stock-analysis-trade-plan-ui`를 `master`에 fast-forward 병합하고 원격 push와
  운영 배포까지 완료했다. 기능 배포 커밋은 `3725bee`다.
- 팝업이 투명해 보인 원인은 정의되지 않은 CSS 변수 `--bg`였다. 종목분석 모달과 전략매매
  팝업 배경을 실제 전역 변수 `--bg-base`로 바꿔 불투명하게 표시한다.
- 기존 UI는 분석 원고를 만든 뒤 `매매 설정`을 누를 때 `/stock-analysis/trade-plan`을 다시
  호출해, 원고에 표시된 가격과 팝업 가격이 서로 달라질 수 있었다. 이제 분석 한 번당 구조화된
  `trade_plan` 하나만 만들고, 같은 객체를 AI 원고와 SSE 완료 응답, 분석 결과 가격 요약,
  전략매매 팝업 입력에 함께 사용한다.
- 분석 결과 카드에 `분석 의견`, `1·2·3차 매수가`, `목표가`, `손절가`를 명시적으로 표시한다.
  팝업은 이 값을 그대로 채우며 추가 AI 호출을 하지 않는다. 사용자가 가격을 수정하면 저장
  출처는 자동으로 `manual`로 바뀐다.
- Bedrock 구조화 출력은 AWS가 거부하던 `prefixItems`를 호환 가능한 `items`로 정규화했다.
  BUY뿐 아니라 WATCH/SELL도 분석 원고에서 가격을 임의 생성하지 않도록 검증된 가격 묶음을
  제공한다. 구조화 출력 실패 시에는 가격을 만들지 않고 수동 입력으로 닫힌 방식으로 전환한다.
- 주요 커밋: 설계 `3c1ebd0`, 계획 `6bd6723`, 구조화 계획 수정 `4291143`, SSE 단일 원본
  `6f0f2c5`, UI·팝업 수정 `c1a52cd`.
- 검증: 집중 테스트 **72 passed**(경고 1건), 전체 Python **790 passed**(경고 13건),
  `node --check static/js/stock-analysis.js` 및 `git diff --check` 통과.
- 17:05 KST 운영 반영 확인: systemd `maps=active`, alembic
  **`0021_analysis_pick_split_plan (head)`**, 내·외부 `/health` 200. 운영 파일에서 SSE 가격
  전달 코드와 `--bg-base` 불투명 배경도 직접 확인했다. 재시작 직후 최초 health는 애플리케이션
  기동 전 호출되어 일시적으로 실패했으나, 시작 로그와 포트 바인딩을 확인한 뒤 정상화됐다.

> 갱신일: 2026-08-11 KST · 작성자: 세션 에이전트 (**현재 PC, 키 `D:\ssh_maps\`**)
> 운영 서버 기능 커밋 = **`3725bee`**, alembic **`0021_analysis_pick_split_plan`**(head).
> 종목분석→전략매매 구현과 8/11 가격 인계·팝업 수정은 운영 배포 완료 상태다.
> 기능 커밋은 `8214235`~`b00c029`, 신규 migration head는 **`0021_analysis_pick_split_plan`**이다.
> 후보 퍼널 Phase 2 AI Scoring 구현·운영 배포와 Ops Config 모드 제어 완료. KIS 주문번호
> 재사용 충돌의 DB 복구·영구 수정·배포까지 완료했고 전체 테스트 **716 passed**.
> `maps=active`, 내·외부 `/health` 200, 운영 tracked worktree/index clean. 현재 AI 모드는
> **`rerank`**. 인바디(`041830`) 35주 진입 행과 자동 손절 55,100원 감시는 복구됐다.
> 별도 브랜치 `feat/ui-design-ppt`에는 종목 분석→전략 설정→워치리스트 자동매수 흐름의
> API·스케줄러·웹·모바일 구현과 독립형 HTML 프로토타입, 18장 PPT 화면설계서가 있다.
> 8/3 기록은 "8/3 작업 ①~⑤" 절, 8/2 는 "이전 날(8/2)" 절 — 결론은 여전히 유효하다.

## Goal — 이 작업이 향하는 곳

후보 생성이 **전략 신호를 보지 않는** 구조를 고쳐, "후보"를 유동성·추세 상위 종목이 아니라
**"이 전략이 오늘 사겠다고 말한 종목"** 으로 되돌리는 것. 그 위에 비용이 제한되고 출처가
투명한 AI 스코어링을 붙인다. Phase 1·2 구현과 배포는 완료했다. 병행 목표는 승격 게이트가
**실제 성과로** 갈리게 만드는 것 —
Sharpe 왜곡 수정(8/2)과 자동 강등(8/2)이 8/3에 처음 실측됐다.

## 8/11 SCR-20 매매 기록 초보자용 원고

- SCR-20에서 복사하는 일일 매매 기록 원고 자체를 초보자도 읽기 쉽게 개편했다. 앞부분은
  `오늘의 매매 한눈에`부터 `내일 예정된 행동`까지 5개 쉬운 섹션, 뒷부분은 원시 값과
  식별자를 보존하는 `상세 기록`과 기존 `투자 유의사항`으로 구성한다.
- 핵심 전문용어는 처음 나올 때 `쉬운 설명(원래 용어)` 형식으로 쓴다. 예를 들어
  `상승과 하락 신호가 섞인 시장(MIXED)`으로 표시하고, 이후에는 쉬운 표현만 사용할 수 있다.
- `DailyDigest`·DB·API·SCR-20 화면·cron 일정은 변경하지 않았다. 다이제스트 JSON만 사실
  출처로 쓰고, 기존 숫자 대조와 네이버 평문 검사를 그대로 유지한다.
- 기존 `check_naver_format.py`에 warning-only `readability` 범주를 추가했다. 쉬운 본문에서
  설명 없이 먼저 등장한 핵심 용어를 찾되, 원시 값을 보존하는 `6. 상세 기록`은 검사하지 않는다.
- 독립 리뷰에서 누락된 한국시장 약세 보정, 업종·후보 감사 필드, AI 실패 시 규칙 기반
  데이터 경계를 보완했다. `price_source=rule` 값은 AI 결론으로 표현하지 않는다.
- 검증: 블로그·다이제스트 집중 테스트 **52 passed**(경고 1건), 전체 Python
  **786 passed**(경고 13건), 기존 전략 가이드 CLI 포맷 검사 통과.
- 설계 `0847300`, 구현 계획 `d5eee47`, 원고 계약 `6c657a4`, 가독성 검사 `c8e91f3`.
  운영 서버와 원격 브랜치에는 아직 배포·push하지 않았다.

## 8/10~8/11 화면설계·로컬 구현 — 종목 분석에서 전략매매·자동매수까지

- 기능 브랜치: `feat/ui-design-ppt` (worktree `.worktrees/ui-design-ppt`). 화면설계를 기준으로
  API·DB·스케줄러·웹·모바일을 구현했으며 운영 서버에는 배포하지 않았다.
- HTML: `docs/ui-design/maps-analysis-trade-prototype.html`. 분석 결과에서 사용자가
  `매매 설정`을 누른 뒤 단일매매 또는 3분할매매를 선택하고, 분석 가격·목표·손절값이
  자동 입력되는 흐름을 제공한다. 3분할 기본 비중은 **30/30/40**이다.
- 사용자 작업은 파란색 `U`, 시스템 자동처리는 초록색 `S`로 화면과 설명 패널에 연결했다.
  정상 흐름 외 AI 실패/수동입력, 주문가능 현금 부족, 중복, 자동매매 게이트 OFF,
  부분체결 시나리오를 포함한다.
- 구조화 AI 계획은 가격 순서와 경계를 검증하고, 실패하거나 BUY가 아니면 주문값을 비운
  `MANUAL_REQUIRED`로 fail-closed 처리한다. 수동값에도 동일한 서버 검증을 적용한다.
- 안전 최대금액은 브로커 주문가능 현금, 단일 종목 노출, 포트폴리오 잔여 용량, 손절 위험
  한도의 최솟값이다. 미리보기 이후 최종 무장 API가 최신 잔고·게이트·중복을 다시 확인하고
  계획 저장과 `ARMED` 전환을 한 트랜잭션으로 처리한다.
- migration `0021_analysis_pick_split_plan`과 회차 모델을 추가했다. 스케줄러는 한 주기에
  적격 회차 하나만 주문하고, 부분체결 누적을 멱등 처리하며 종료된 주문의 미체결 잔량만
  재주문한다. 현금 부족·만료·사용자 중지는 신규매수만 막고 보유분 목표·손절은 계속 감시한다.
- 웹은 분석 결과의 `매매 설정`, 단일/3분할 설정, 안전 미리보기·최종 무장, 워치 상세와
  남은 매수 중단을 연결했다. 모바일은 워치 진행률·회차 상세·남은 매수 중단을 제공하며
  계획 생성은 웹에서만 한다.
- 구현 안전 계약:
  - 구조화 AI 값 검증 실패 시 자동 주문값을 비우고 수동입력으로 전환
  - 최종 무장은 서버 게이트를 다시 조회해 재검증
  - 한 주기에 분할 주문은 최대 한 회차
  - 부분체결 주문 종료 후 미체결 잔량만 재주문
  - 누적 체결량과 현재 주문 체결 커서는 브로커의 일시적인 하향 보고에도 감소하지 않음
  - 추가매수 중지는 보유분 목표·손절 청산 감시를 중지하지 않음
  - 청산 주문 제출 후 보유수량 0을 확인할 때만 CLOSED
  - 만료 계획의 미체결 매수 주문은 취소 후 최종 체결을 동기화
  - 주문 감사 로그에서 미연결 회차 주문을 복구해 중복 제출 방지
  - 활성 종목 중복은 DB 고유 인덱스로 차단
- PPT: `docs/ui-design/MAPS_종목분석_전략매매_화면설계서.pptx` — HTML 실제 화면 캡처
  16개를 포함한 **18장 16:9** 화면설계서다. 생성기는
  `scripts/build_stock_analysis_ui_ppt.py`, 계약 테스트는
  `tests/test_ui_design_deliverables.py`다.
- 최종 검증: 서버 집중 테스트 **140 passed**, 전체 Python **775 passed**(경고 13건),
  모바일 전체 **33 passed**, 모바일 production build와 웹 JS 문법 검사 통과. 산출물 테스트
  **8 passed**, 화면 캡처 16개 1120×740, PPT 18장·내장 이미지 16개 동기화를 확인했다.
- 인증 OFF·Mock 브로커·스케줄러 OFF와 새 임시 SQLite DB로 로컬 앱을 기동해 `/health`,
  `/api/v1/analysis-picks`, `/stock-analysis`, `/analysis-picks` 응답과 1440×1000 데스크톱 렌더링을
  확인했다. KIS·Bedrock 호출은 하지 않았다.
- 전체 회귀 중 신규 migration 테스트가 Alembic `fileConfig`로 기존 앱 로거를 비활성화하는
  순서 의존성을 발견했다. `disable_existing_loggers=False`와 상태 보존 회귀 테스트를 추가해
  migration→위험관리 로그 조합 및 전체 회귀를 다시 통과했다.
- 독립 코드 리뷰에서 청산 주문 미확정 `CLOSED`, 만료 주문 미취소, 주문 제출 직후 크래시의
  미연결 주문, 취소 직전 체결 누락, 공백 종목코드·동시 무장 중복을 발견했다. 청산 주문과
  브로커 보유를 사이클마다 재조정하고, 감사 로그 기반 회차 복구, 취소 후 최종 fill/position
  재조회, 6자리 종목코드 정규화, 활성 종목 partial unique index로 보완했다. 후속 리뷰에서
  체결 완료 청산 주문의 attachment 유지와 누적 체결량·현재 주문 커서의 단조 증가까지 검증했다.
  최종 독립 재검토 결과 Critical/Important 이슈는 0건이다.

### 화면설계 작업 재개 기록

- 2026-08-10에는 HTML·캡처 16개·PPT 설계에서 중단했고, 2026-08-11 해당 설계를 검토한 뒤
  로컬 기능 구현을 재개했다.
- 독립 코드 리뷰에서 발견한 안전한도 우회, 단일→분할 상태 유실, 수동입력값 유실,
  회차 수량·부분체결 상태 불일치, 문서 의존성 미선언을 모두 반영했다. 후속 재검토에서 발견된
  최종 확인 화면의 현금 부족·게이트 OFF·중복 우회도 `tradeBlocks()` 재검증과 버튼 비활성화로 막았다.
- `requirements.txt`에 `pillow`, `python-pptx`를 선언했다. 재생성은
  `python scripts/build_stock_analysis_ui_ppt.py`, 집중 검증은
  `python -m pytest tests/test_ui_design_deliverables.py -q`로 수행한다.
- 원격 브랜치 `origin/feat/ui-design-ppt`, 초안 PR은
  `https://github.com/jkkim74/maps/pull/2`에 있다.
- 운영 DB와 운영 서버에는 이번 기능 변경을 적용하지 않았다. 배포 시 migration
  `0021_analysis_pick_split_plan` 적용과 KIS/Bedrock 자격증명 환경의 별도 스모크 검증이 필요하다.
- 이 worktree의 ignored `maps.db`는 Alembic 이력이 없는 구 스키마라 신규 `analysis_pick` 컬럼이
  없다. 로컬 실행은 새 DB를 사용하거나 데이터를 백업한 뒤 정식 migration 경로를 마련해야 한다.
  기존 파일은 사용자 데이터 보존을 위해 이번 작업에서 변경하지 않았다.

## 8/11 긴급 복구·영구 수정·운영 배포 완료 — KIS ODNO 재사용 충돌

- 08:55 직전 서비스를 정지하고 7분 자동 재시작 timer를 설치해 **8/11 주문 사이클 1회를
  안전하게 건너뛰었다.** 서비스는 08:56 자동 재시작됐고 broker sync는 계속 정상 동작한다.
- PostgreSQL 전체 custom-format 백업을 관리자 권한으로 생성했다:
  `/opt/maps/backups/maps-pre-order-identity-20260811-085104.dump`
  (323,668,622 bytes, mode `600`). 앱 역할로 한 첫 백업은 구 테이블 권한 때문에 실패했고
  복구 트랜잭션은 시작되지 않았다. 예외 출력에 연결 문자열이 포함돼 `maps_app` 비밀번호를
  새 난수값으로 즉시 교체하고 `.env`를 mode `600`으로 원자 갱신한 뒤 새 연결을 확인했다.
- 단일 트랜잭션으로 `order_log.id=53`의 8/6 `051160` 행을
  `expired/fill_qty=0/fill_price=NULL`로 원복하고, `id=59`에 인바디 진입 행을 만들었다:
  `kis:d59a650c:20260810:0000000755`, `ath_breakout_v1`, BUY 35주,
  주문가 71,600원, 체결가 69,200원, ATR14 `5638.281459`, `filled`.
- 영구 수정은 KIS 감사 ID를 `kis:<계좌 SHA-256 지문 8자>:<KST YYYYMMDD>:<ODNO>`로 저장한다.
  계좌번호 원문은 저장하지 않고 `12345678`과 `12345678-01`을 같은 계좌로 정규화한다.
  sync는 내부 ID와 ticker/side/broker가 일치할 때만 갱신하고, 구 raw ID는 같은 KST 주문일의
  KIS 행에 한해 호환 조회한다. 취소 API 경계에서는 raw ODNO를 복원한다. `ORD_TMD`가 없어도
  UTC 호스트 시간이 아니라 KST 날짜를 사용한다.
- 설계·계획: `docs/superpowers/specs/2026-08-11-kis-order-identity-design.md`,
  `docs/superpowers/plans/2026-08-11-kis-order-identity.md`.
  구현: `144e993`, `ae8028f`, `d92234d`, 리뷰 보완 `b065c54`.
- TDD RED→GREEN 후 집중 테스트 **52 passed**, 병합된 `master` 전체 테스트 **716 passed**.
  독립 코드 리뷰의 Critical/Important 0건, 최종 `Ready to merge: Yes`.
- 09:14 운영 배포 완료: 기능 HEAD `b065c54`, alembic `0020_ai_scoring (head)`, 서비스 active,
  내·외부 health 200. 09:16:24 첫 장중 broker sync는 `market_open=true`,
  `exit_monitor_active=true`, `sync_errors=0`, `skipped_sell_orders=0`.
  KIS 실제 보유는 인바디 35주 @69,200원, DB 진입 행과 일치하며 정본 함수의 손절가는
  **55,100원**이다(확인 시 현재가 68,500원으로 손절 미도달).

## 8/10 운영 점검 — rerank 전환·매수 2건·인바디 자동 손절 누락

- 사용자가 `/ops-config`에서 AI Scoring 모드를 `rerank`로 변경했고, 운영 설정값도
  **`rerank`**로 확인했다. 기존 후보는 자동 재평가되지 않는다. 8/10 08:55 주문은 8/7 생성
  스냅샷(`ai_mode=off`, `score_source=RULE`)을 사용했으므로 AI 재정렬 결과가 아니다.
- 08:55 주문 사이클은 `ath_breakout_v1` 매수 2건을 제출하고 92건을 스킵했으며 매도는 없었다.
  KIS 모의계좌에서 두 건 모두 전량 체결됐다.

| 종목 | KIS 주문번호 | 체결 | %/ATR 손절가 | Rule 매수 사유 |
|---|---|---:|---:|---|
| 인바디 `041830` | `0000000755` | 35주 @ 69,200원 | 55,100원 | 신고가 돌파 신호, 추세강도 100·신고가 95, 점수 59.12 |
| BGF리테일 `282330` | `0000000751` | 25주 @ 150,700원 | 130,700원 | 신고가 돌파 신호, 추세강도 100·신고가 95, 점수 59.14 |

- **당시 미해결 운영 위험(8/11 해결):** KIS가 8/6 `051160` 주문에 썼던 `0000000755`를 8/10 인바디 주문에
  재사용했다. `order_log.order_id`는 전 기간 unique라 인바디 행 INSERT가 `IntegrityError`로
  스킵됐고, 다음 broker sync는 `order_id`만으로 과거 `051160` 행을 찾아 인바디의
  `status=filled`, `fill_qty=35`, `fill_price=69200`을 덮어썼다. 종목·전략·주문수량·ATR은
  과거 값이 남은 혼합 행이다.
- 당시 KIS 실제 보유에는 인바디 35주가 있었지만 DB에는 `041830` 진입 행이 없었다. 청산 감시는
  보유 ticker와 DB BUY 행을 연결하지 못해 매분 `skipped_sell_orders=1`이었고, **55,100원 자동 손절은
  적용되지 않았다.** `maps_plan_based_exits_enabled=false`; 위 손절가는 체결가와 주문 시점
  ATR(14)=5638.281459를 사용한 실제 `%/ATR` 규칙 산출값이다.
- 원인은 AI Scoring과 무관하다. 영구 수정은 raw KIS `ODNO` 단독이 아니라
  `broker+account+KST 주문일+ODNO`를 주문 식별자로 쓰고, sync에서 날짜·ticker·side까지 검증해
  불일치 행 갱신을 거부하는 것이다. **8/11 위 절의 방식으로 수정·DB 복구·배포를 완료했다.**

## 8/7 밤 후보 퍼널 Phase 2 AI Scoring 구현·배포 완료

- 기능 커밋 `ad3c79b`에서 `off|rerank|replace`, 일일 전역 5회 한도, 당일 캐시,
  Rule fallback/source 표시, Sonnet 4.6 구조화 출력, 후보 API·UI provenance, 평가 CLI와
  마이그레이션 `0020_ai_scoring`을 구현했다.
- 첫 운영 호출은 Pydantic JSON Schema의 `minimum`, `maximum`, `minLength`, `maxLength`,
  `maxItems`가 Bedrock 지원 범위를 벗어나 즉시 provider 오류가 났다(기록 토큰 0).
  `39fefb3`에서 도메인 검증은 유지하고 Bedrock 전송 스키마에서만 미지원 제약을 제거했다.
- 수정 후 실제 Sonnet 4.6 호출 성공: 기준일 `2026-08-07`, ticker `002020`, AI 점수 `20.0`,
  schema success, input 1,240 / output 97 tokens, latency 19.23초. 이는 평가 CLI가 기존
  `entry_signal=true`, `weekly_pass=true`, `final_score` 상위 후보 1개를 사후 평가한 것으로,
  AI 점수로 종목을 먼저 추출한 실행은 아니다. 결과는 `/tmp/ai-scoring-evaluation-39fefb3.json`.
- 운영 모드 변경 기능 누락을 `54c29e9`에서 보완했다. `/ops-config` 화면에서 `off`, `rerank`,
  `replace`를 선택할 수 있고, 저장 즉시 단일 worker 스케줄러 설정 객체에 반영되며 `.env`에
  원자적으로 저장돼 재시작 후에도 유지된다. `replace`는 확인창을 거친다.
- 운영 API로 동일 모드 저장(`off → off`)을 실측해 HTTP 200, `.env` 권한 `600`, health 정상까지
  확인했다. **당시 모드는 `off`**였다. 다음 후보 생성부터 AI를 반영하려면 사용자가 명시적으로
  `rerank`를 선택한 뒤 candidate generation을 실행해야 한다. 기존 후보는 자동 재평가되지 않는다.
- 배포 커밋: `ad3c79b feat: add bounded AI candidate scoring`,
  `39fefb3 fix: sanitize Bedrock scoring schema`, `54c29e9 feat: add AI scoring mode control`.
- 검증: AI 집중 테스트 20 passed, 최종 전체 테스트 **707 passed**, JS 문법 검사 통과,
  운영 PostgreSQL alembic `0020_ai_scoring (head)`, 서비스 active, 내·외부 health 200.

## 8/7 저녁 운영 긴급 조치 완료 — 보안·breadth·주문 정합

- 운영 `.env`를 `600`, `logs/`를 `700`, 로그·KIS 토큰 캐시를 `600`으로 제한했다.
  systemd drop-in `UMask=0077`과 analyze/blog cron의 `umask 077`도 적용해 새 파일과
  로그 회전 뒤에도 권한이 넓어지지 않는다.
- PostgreSQL 전용 역할 `maps_app`의 비밀번호를 새 난수값으로 교체했다. 다른 서비스가 이 역할을
  공유하지 않는 것을 먼저 확인했고, 교체 후 새 연결·서비스 재기동·broker sync를 검증했다.
  비밀번호 값은 어떤 문서나 출력에도 남기지 않았다.
- 구 자격증명이 남아 있던 로그·Claude JSONL **8개 파일, 26개 항목**을 마스킹했다.
  systemd journal에는 노출이 없었고, 정리 후 현재 자격증명 잔존 0건·대상 JSONL 파싱 오류 0건이다.
- Claude stream-json은 `scripts/redact_stream_secrets.py`를 `tee` 앞에 두어 raw·진행 로그에
  알려진 비밀값이 들어가기 전에 제거한다. 실제 운영 `.env` 기반 필터 점검도 통과했다.
- 운영 서버의 미커밋 `maps/market/breadth.py` 성능 패치를 회귀 테스트와 함께 정식화했다
  (`88450ce`). 전체 이력 윈도우 정렬 대신 `ma_window*3+10` 캘린더일 구간만 읽는다.
  서버 tracked worktree는 clean이며 임시 패치 백업은 정식 커밋 확인 후 제거했다.
- 보안·성능 커밋: `b60cf59`(UTC 테스트 시드 안정화), `02f6941`(로그 보호),
  `88450ce`(breadth 조회 제한). 로컬 전체 테스트 **671 passed**.
- 8/6 주문 `051160` 427주·`148780` 689주는 모두 `expired`, `fill_qty=0`이고
  8/7 00:00 broker sync에서 `expired_orders=2`로 정리됐다. 당시 EOD 취소 잡은 KIS 모의서버
  `40580000 장종료`로 실패했으나 보유 0·현금 1억원과 일치했다. 이 오류 처리 개선은 별도 후속이다.
- 8/7 16:00 analyze는 run id=31 `completed`, strong, 후보 1·픽 0으로 정상 종료했다.
  재기동 후 현재 KIS 미체결 0, 보유 `051900` 12주·`073240` 270주, broker sync 오류 0이다.

## 8/7 AI Scoring 설계·구현 계획 확정 — 구현 기준 기록

아래 문서는 Phase 2 구현의 기준이 된 최종 설계와 계획이다. 구현·배포 결과는 바로 위 절을 따른다.

- 최종 설계: `docs/superpowers/specs/2026-08-07-ai-scoring-design.md`
- 실행 계획: `docs/superpowers/plans/2026-08-07-ai-scoring.md` (Task 1~10)
- 문서 커밋: `4bc76d0 docs: design cost-aware AI scoring`,
  `9bf9441 docs: plan AI scoring implementation`
- 기존 `docs/plans/candidate-funnel-ai-scoring.md`는 Phase 1 및 Phase 2 초기안 기록이다.
  **AI Scoring 구현 기준은 위 최종 설계·실행 계획**으로 한다.

### 확정 동작

- `ai_scoring_mode=off|rerank|replace`, 기본값 `off`. 명시적 환경설정 없이 기존 동작을 바꾸지 않는다.
- `rerank`: 후보 자격과 최소 점수는 계속 `rule_score`로 판정하고, 순위만
  `rule_score * (1 - ai_weight) + ai_score * ai_weight`로 조정한다. 기본 `ai_weight=0.20`.
- `replace`: 시장·신호·유동성·제외·신선도·주문 안전 게이트는 유지하되, AI 평가 대상으로
  전역 최대 5개의 고유 ticker를 먼저 추리고 AI 점수가 추천 점수를 대체한다. 한도 밖 종목은
  관찰 기록만 남기고 최종 추천·주문 대상에서는 제외한다.
- 일일 AI 호출 한도는 **전체 전략 합산 5개 고유 ticker**가 기본이며 환경설정으로 변경 가능하다.
  여러 전략에 같은 ticker가 있으면 AI 호출은 1회만 하고 결과를 공유한다.
- AI 실패·잘못된 응답은 `rule_score`로 대체하고 `score_source=RULE_FALLBACK`을 표시한다.
  한도 초과는 `score_source=RULE`, `ai_status=SKIPPED_LIMIT`; 자격증명 미설정은 호출과 예산을
  사용하지 않고 `score_source=RULE`, `ai_status=SKIPPED_UNCONFIGURED`로 남긴다.
- AI 점수는 추세 25, 모멘텀 20, 거래량 15, 위험 15, 진입 타이밍 15, 전략 적합도 10점으로
  산출한다. 모델은 항목별 점수를 반환하고 서버가 범위를 검증한 뒤 합산한다.
- 당일 캐시는 `ref_date+ticker+input_hash+model_id+prompt_version` 기준이다.
- 원시 30봉 OHLCV 대신 파생 지표만 전송하고 AI가 진입가·손절가·목표가를 만들지 않는다.
  prompt caching, batch inference, 자동 재시도는 사용하지 않는다.
- AWS Bedrock Runtime의 구조화 출력을 사용한다. 기본 모델은 **Claude Sonnet 4.6**이고
  Claude Haiku 4.5는 환경설정으로 선택 가능하다. Sonnet 4.6은 adaptive thinking과
  `output_config.effort="low"`를 사용하며 `thinking=disabled`나 sampling 파라미터를 보내지 않는다.
- 배포 순서는 `off` → `rerank` → `replace`이며 모드 전환은 항상 명시적 환경설정으로 한다.

### 구현 결과

계획 Task 1~10은 완료됐다. 현재 alembic head는 `0020_ai_scoring`이며, 다음 작업은 재구현이
아니라 운영 모드를 명시적으로 `rerank`로 전환해 실제 candidate generation을 관찰하는 것이다.

## 8/7 오전 운영 점검 — 8/6 analyze stage 주입 실효·실패 원인·재실행 확인

### 8/6 16:00 정규 cron — stage JSON 주입 성공, selector 전 단계에서 `exit=143`

- `16:00:05` exporter가 DB 기반 stage JSON을 생성했고 `claude -p` 프로세스 인자에도
  `source=promotion_history.latest_passed` JSON 전체가 정상 주입됐다.
- 다만 시장국면 단계의 breadth 조회가 장시간 걸려 진단하던 중 에이전트가 부모
  `timeout` 프로세스까지 종료했다. `16:13:45`, `claude exit=143`으로 실패했으며
  **2700초 타임아웃은 아니다**.
- selector 단계에 도달하지 못했으므로 이 회차만으로는 selector 입력 실효를 판정할 수 없다.
- DB `analysis_run` id=29: `status=failed`, `picks_count=0`,
  `error_message='claude exit=143'`.

### 8/6 17:57 수동 재실행 — JSON 실제 전달·재선정 제거 실효 확인

- market-regime 결과와 운영 DB stage JSON이 `strategy-selector` 프롬프트에 함께 전달됐다.
  적격 전략은 `ath_breakout_v1`, `donchian_v2` 두 개, 나머지 네 전략은 `research`로 정확했다.
- `strategy-selector` 호출은 **정확히 1회**. raw 이벤트의 Read 도구에는 `HANDOFF.md`가 없고,
  단계 확인 불가·추측·재선정도 없었다.
- selector 결과도 두 적격 전략만 선정(`ath_breakout_v1` 24.74%, `donchian_v2` 25.26%,
  현금 50%)해 exporter JSON과 일치했다.
- 전체 파이프라인은 `18:41:03` 성공. Claude 소요 **2,580.769초(약 43분 1초)**로
  2,700초 제한까지 여유가 약 **119초뿐**이었다. 타임아웃은 피했지만 여유가 작다.
- DB `analysis_run` id=30: `status=completed`, `regime=strong`, `candidates_count=1`,
  `picks_count=0`, 오류 없음.

### 🔴 새 운영 위험 — 서버 dirty 코드와 로그 내 DB 자격증명

- 재실행 에이전트가 운영 서버의 추적 파일 `maps/market/breadth.py`를 직접 수정했다.
  `recent_dataframes()`에 `start=ref_date-(ma_window*3+10일)`을 넣어 586만 행 전체 정렬을
  피하는 7줄 패치다. 이 패치로 재실행은 진행됐지만 **미커밋·미배포 상태**이며,
  서버 작업 트리는 dirty다. 무심코 `git pull`하지 말고 패치를 먼저 보존·테스트·정식 커밋할 것.
- 17:57 진행 로그와 raw JSONL에 DB 자격증명이 Bash 명령 문자열로 평문 기록됐다.
  두 파일 권한도 **664**다. 실제 값은 HANDOFF에 기록하지 않는다.
  **DB 자격증명 교체 + 노출 로그 접근 제한/정리 + analyze 로그 비밀 마스킹**이 필요하다.
- 서버에는 과거 analyze가 만든 untracked scripts/reports 등이 다수 있다. 이번 핵심 tracked
  변경은 위 `breadth.py` 1개다.

### 8/7 16:00 관찰 대기

- 8/7 09:42 기준 서버 HEAD `2dd36af`, `maps=active`, analyze lock `idle`, cron은
  `0 16 * * 1-5`로 정상이다.
- 회사 PC에 15:58~16:55 읽기 전용 감시를 걸었다. lock, 오늘 로그, stage JSON 주입,
  selector 진입·호출 횟수, 완료/실패/타임아웃을 확인한다.
  로컬 기록: `%TEMP%\maps_analyze_monitor_20260807.log`.
- **16:00~16:45 배포·재기동 금지.** 관찰 중 운영 서버를 변경하지 않는다.

## 8/6 세션 — 정상 주문 첫 검증 · KIS 체결조회 복구 · 승격 단계 입력 개선

### 08:55 주문 사이클 — 신호 게이트·강등·신 계좌 정상 동작 확인

장세 `strong`, 신 계좌 `50200591-01`에서 주문 사이클이 22.8초 만에 성공했다.

- submitted 2 / skipped 74, 매도 0. `40910000`·Kill Switch 재발 없음.
- 제출: `ath_breakout_v1` 051160 427주 @10,100원, 148780 689주 @3,925원.
- 스킵 사유: 진입 신호 비활성 5, 장세 진입 한도 2건 도달 69, 기타 0.
- 후보 생성 시 저장 신호와 주문 시점 400봉 재계산 신호 **76건 전부 일치(불일치 0)**.
- 승격 단계 필터는 의도대로 `ath_breakout_v1`, `donchian_v2` 두 전략만 통과.
- 002810은 브로커 보유 0, 8/6 오류 없음. 구 계좌의 7/29 매수 226주 감사 행만 남아 있다.

### 🔴 추가 발견·즉시 복구 — KIS 일별주문체결조회 TR ID 오류

주문 2건은 KIS에 실제 접수됐지만 기존 `get_daily_order_results()`/`get_open_orders()`가 0건을
반환했다. 원인은 구 TR ID(`VTTC8001R`/`TTTC8001R`). KIS 현재 ID인
`VTTC0081R`/`TTTC0081R`로 수정(`f941597`)하고 10:44 배포했다.

- 운영 직접 조회: 주문 2건 모두 `pending`, fill 0으로 확인.
- 11:29 재확인: KIS·DB 모두 잔량 전량(051160 427주, 148780 689주), 보유 0,
  현금·총자산 1억원으로 일치. broker_sync도 `open_orders=2`, `items=0`, `holdings=0`.
- 다음 broker_sync: `open_orders=2`, `sync_errors=0` — 동기화 경로 복구 실효 확인.
- 전체 테스트 651 passed(배포 당시). 마이그레이션·requirements 변경 없음.
- **장 종료 후 과제**: 두 주문의 체결·부분체결·만료와 DB `status/fill_qty` 정합 확인(진행 중).

### strategy-selector 승격 단계 입력 개선 (배포 완료)

`promotion_history`의 전략별 최신 `passed=True` 행을 JSON으로 내는
`scripts/export_strategy_stages.py` + `maps/promotion/stage_snapshot.py`를 추가했다.
cron이 Claude 실행 전에 JSON을 생성해 `/analyze` 프롬프트에 직접 주입하고,
strategy-selector는 `HANDOFF.md`·메모리에서 현재 단계를 추측하지 못하도록 명시했다.
대상 단계는 `mock_candidate`, `live_candidate`, `live`; 자동 강등의
`passed=True → research`도 즉시 반영된다. 쉘 문법 검사 + 전체 656 passed.
12:11 배포 후 exporter 실측 대상은 `ath_breakout_v1`, `donchian_v2`로 주문 자격과 일치했다.

### 구 계좌 이력 분리 (배포·운영 적용 완료)

원 체결을 삭제하거나 가짜 매도를 만들지 않는다. `MAPS_ACCOUNT_HISTORY_START_DATE`를 도입해
이전 감사 행은 보존하면서 현재 계좌의 거래 리뷰·대시보드 수익률·MDD·슬리피지·
`mock_months`에서 제외한다. 운영 `.env`에 **`2026-08-05`**로 적용했다.
부수로 UTC 주문시각을 그대로 `.date()` 해 `mock_months`가 하루 길어지던 문제도 KST 변환으로 수정.
배포 후 거래 리뷰에서 002810 제외, initial/current assets 1억원을 직접 확인했다.

### 자동 강등 정책 결정 — 현행 유지

정책은 **점수 <50 연속 10회면 mock→research, research에서 ≥60이면 재승격**으로 유지한다.
10점 히스테리시스와 약 2주 관측창이 과도한 왕복을 막는다. 운영 이력 재확인 결과 4전략 모두
실제 10회 연속 미달을 충족했고, 강등 후에도 3전략은 50 미만, ath_breakout_v2는 약 53으로
재승격 60 미만이다. 따라서 기존 4건 강등도 되돌리지 않는다.

### pullback_v3.3 청산 구조 재설계 — 구현·배포 완료, 강세 구간 게이트 탈락

기존 `pullback_v3`는 그대로 보존하고 병렬 연구 후보 **`pullback_v3_3`**를 추가했다(`afcbf09`).
진입과 고정/ATR 손절은 V3.2와 동일하고, 청산만 **2R 목표 + 1.5R 활성/0.5R 간격
트레일링 + MA_long 추세 이탈**로 바꿨다. 단일 백테스트와 포트폴리오 재생이 같은 상태형
청산 함수를 사용하며, 거래 결과에 초기 위험·R 배수·보유일·청산 사유를 기록한다.

- 수동 백테스트와 수동 WFA 레지스트리에는 등록했다.
- 운영 scheduler의 `_RUNNABLE_STRATEGIES`에는 **등록하지 않았다**. 후보 생성·검증·주문에 영향 없음.
- 같은 봉에서 손절과 익절/트레일링이 충돌하면 보수적으로 손절 우선, 트레일링은 직전 봉까지의
  고점만 사용한다. 기존 전략은 레거시 청산 경로를 그대로 탄다.
- 연구 도구 `scripts/evaluate_pullback_v3_3.py`는 6개 청산 조합과 세 강세 구간을 감사 로그에
  저장하고, 세 구간을 모두 통과한 조합에만 전체 기간 검증을 허용한다.

8/6 운영 KOSPI 데이터 평가 결과, **6개 조합 모두 탈락**했다. 각 조합의 Sharpe:

| 조합 (목표/활성/간격 R) | 2017 | 2020 | 2023 | 세 구간 통과 |
|---|---:|---:|---:|---|
| 1.5 / 1.0 / 0.5 | -0.104 | 0.425 | -0.374 | 아니오 |
| 2.0 / 1.0 / 0.5 | -0.071 | 0.437 | -0.425 | 아니오 |
| 2.0 / 1.5 / 0.5 | -0.094 | 0.472 | -0.505 | 아니오 |
| 2.0 / 1.5 / 0.75 | -0.114 | 0.413 | -0.512 | 아니오 |
| 2.5 / 1.5 / 0.5 | -0.086 | 0.430 | -0.487 | 아니오 |
| 2.5 / 1.5 / 0.75 | -0.127 | 0.440 | -0.494 | 아니오 |

기준 Sharpe는 2017 **0.245**, 2020 **0.388**, 2023 **-0.005**. 모든 조합이 2020만
통과하고 2017·2023에서 기준 미달했다. 거래 수·손익비·MDD 조건은 대체로 충족했지만 Sharpe
재현성이 없으므로 **선정 조합 없음, 전체 기간/WFA/Plateau/MC 미실행, research 유지**가 결론이다.
운영 재기동 뒤 `broker_sync`는 `open_orders=2`, `sync_errors=0`; 기존 051160/148780 주문도
영향 없이 유지됐다.

**배포 확인:** 기능 커밋 `afcbf09`, 최초 HANDOFF 반영 `3655d86`. 운영 서버에서 두 커밋을
순서대로 fast-forward했고, 기능 배포 때만 서비스를 재기동했다. 이후 HANDOFF 문서 동기화는
재기동 없이 반영했다. 최종 확인 시 서비스 `active`, `/health` 200, 추적 파일 변경 없음.

## 8/5 세션 — KIS 모의계좌 만료 사고 · 복구 · Kill Switch 해제 버튼

### 🔴 사고 — 장세 게이트가 처음 열린 날, KIS 모의계좌가 만료돼 있었다

8/5 08:55 주문 사이클에서 장세 차단이 처음 풀려 실제 주문이 나갔는데, **전 주문이
KIS `40910000 "모의투자 주문이 불가한 계좌입니다"` 로 실패** (모의계좌 유효기간 만료).

- `ath_breakout_v1` 매수 5연속 실패 → **Kill Switch 자동 발동** (연속 실패 감지는 설계대로 동작)
- `multi_asset_trend_v1` 002810 청산 매도도 같은 오류로 실패
- 최종 submitted 0 / skipped 72 (매수 71, 매도 1)

### 복구 (완료)

| 단계 | 내용 |
|---|---|
| 계좌 재생성 | 구 `50185813-01` → 신 **`50200591-01`** (사용자, KIS 홈페이지) |
| 키 재발급 | 새 appkey/appsecret → 서버 `.env` `KIS_APP_KEY`/`KIS_APP_SECRET`/`KIS_ACCOUNT_NO` 교체 후 재기동 |
| 실효 확인 | 09:48 broker_sync 성공 — cash 1억, 보유 0, sync_errors 0 |
| Kill Switch 해제 | `kill_switch_log` 에 deactivate 이벤트 직접 삽입 (당시 UI 버튼 부재 → 아래 신규 기능) |

> ⚠️ **계좌번호만 바꾸면 `OPSQ2000 INVALID_CHECK_ACNO` 가 난다.** appkey 가 계좌에
> 묶여 있어 **키도 같이** 재발급해야 한다. 같은 40910000 이 재발하면 만료를 먼저 의심.

**후과 — 성과 이력 단절:**
- 구 계좌의 002810 포지션(~1,020만)은 청산 없이 브로커 쪽에서 소멸. DB 전략 트레이드에
  열린 기록이 남아 있을 수 있다 — 다음 사이클 로그에서 관찰 (8/5 오전 broker_sync 는 조용함).
- **모의 성과 이력이 8/5 에서 끊긴다.** 6/1~7/30(-14.7%)과 새 계좌(1억 리셋)를 잇지 말 것.
  `mock_months` 재산정에도 영향 (이월 15번과 연결).

### 8/4 실측 확인 (8/3 Next Steps 1·2번 처리)

- **주문 사이클(1번) → 이월**: 8/4 도 weak — 64건 전량 `preferred_regime_mismatch:weak`.
  주문 가능 전략 2개(ath_breakout_v1, donchian_v2)는 강등 반영대로. 신호 집합 일치 검증은
  8/6 이후 정상 주문일로 이월.
- **G2P 수정 실효(2번) → ✅ 종결**: 8/4 17:10 검증에서 `Infinity`/`NaN` **0건**. 무거래 사유가
  예상 3전략에 부착 — contrarian **5/5**, multi_asset **3/5**, ath_v2 **2/5** (예상 3/5,
  fold 1개가 이번엔 거래 — 무해). pullback_v2 유일 WFA 통과 유지, **passed=0/failed=8 불변**
  (통과 수 변화 없음 = 기대 충족). 부수: ath_breakout_v1 점수 78.7 → **65.4** — 이제
  mock_months 외에 점수로도 차단된다.

### 신규 기능 — Kill Switch 해제 버튼 (`debe036`, 배포 완료)

해제 수단이 API 직접 호출뿐이었다 → 리스크 화면(SCR-06)에 발동 중 목록 + 해제 버튼.
`RiskResponse.active_kills` 신설(`api/risk.py`, `api/schemas.py`), 기존
`POST /api/v1/live-monitor/{id}/release` 를 화면에 연결(`static/js/app.js`,
`templates/risk.html`). 발동이 없으면 아무것도 안 보인다. `created_at` 은 UTC ISO 로
내려 브라우저에서 KST 변환 (9시간 함정 회피). 테스트 1건 추가, 전체 647 passed.
마이그레이션·requirements 변경 없음.

## 8/3 커밋·배포

| 해시 | 내용 |
|---|---|
| `d913cf5` | feat: KIS 연속조회 발동·보유 종목 수를 로그에 남긴다 (관측성 보강) |
| `0a186c0` | feat: 후보 퍼널을 신호 기반으로 재설계 (Phase 1) — `0018` |
| `bd222d6` | Merge feat/candidate-funnel-signal-gate |
| `e4aaa1b` | docs: 8/3 세션 핸드오프 |
| `3728958` | fix: G2P가 무거래를 inf로 내 승격 게이트를 거짓 통과시키던 것 차단 (+ analyze 타임아웃 2700) |
| `e32fa04` | Merge fix/g2p-no-trade-guard |

배포 3회: 14:05 KST(`d913cf5`), 16:25 KST(`bd222d6` + `alembic upgrade head` → 0018),
17:48 KST(`e32fa04`, 마이그레이션 없음). 전부 `active`, 기동 후 오류 없음.
requirements 변경 없음.

> ⚠️ **배포 시 `alembic upgrade head` 를 반드시 넣을 것.** CLAUDE.md 의 원라이너 배포
> 명령에는 마이그레이션 단계가 **빠져 있다**. 0018 은 수동으로 넣어 적용했다.
> (CLAUDE.md `!deploy` 안전 수칙에 이 경고와 아래 배포 금지 시간대를 8/3에 추가했다.)

## 8/3 작업 ① — broker_sync 잔고 정합 확인 (이월 7번) → 종결

DB·로그만으로 확인(외부 KIS 호출 없음). 결론: **무해하나 미검증**.
모의계좌 보유가 6/15~8/3 내내 **0~3종목**이라 연속조회(tr_cont)가 발동한 적이 없다.
재시도 소진 WARNING 배포 전/후 모두 0건, `sync_errors` 전 사이클 0, 하트비트 정상
(거래일 1,435~1,439회/일).

> ⚠️ **EGW00201은 로그로 셀 수 없다.** 재시도 단계가 `logger.debug`
> (`kis_adapter.py:662`)라 INFO 레벨 journalctl 에 안 남는다. 관측 가능한 신호는 전 재시도
> 소진 시의 WARNING 뿐이다. 핸드오프에 적혀 있던 "EGW00201 로그 증가 확인"은 실행 불가능한
> 지표였다.

**관측성 보강(배포 완료)** — 20종목을 넘는 순간 자동으로 드러난다:
`_fetch_paged` 가 2페이지 이상일 때만 INFO(`KIS 연속조회 N페이지 병합`), broker_sync
`collection_log` note 에 `holdings=<n>`(조회 미지원은 `n/a`). 실효 확인 완료 —
재기동 후 첫 사이클에 `holdings=2` 기록됨.

## 8/3 작업 ② — 후보 퍼널 재설계 Phase 1 (이월 10번) → 배포·실측 완료

계획서: `docs/plans/candidate-funnel-ai-scoring.md` (**개정본으로 갱신됨**).
TDD로 진행(테스트 6건 먼저 실패시킨 뒤 구현), `tests/test_candidate_funnel.py`.

| 변경 | 내용 |
|---|---|
| 신호 계산 일원화 | `_signal_from_frame` 이 정본, `_latest_strategy_signal` 은 DB 래퍼. 봉 수는 `_SIGNAL_LOOKBACK_BARS=400` 공유 |
| 종목 컨텍스트 1회 | `TickerContext` + `_build_ticker_contexts` — 전략 루프 **밖**에서 호출 |
| 저장 정책 | 신호 종목 전수 ∪ 나머지 상위 N (`maps_candidate_snapshot_top_n`, 기본 50) |
| 스키마 | `candidate_snapshot.entry_signal`(nullable) + `0018` |
| 0건 원인 구분 | 전략별 로그 + 잡 details `universe_size`/`signal_count`/`dropped_count` |

**8/3 16:50 실측 (성공):**

| 지표 | 7/31 | 8/3 |
|---|---|---|
| 저장 행수 | 10,288 | **664** (−93.5%) |
| 진입 신호 | — | **264건** |
| 소요 시간 | 17분 23초 | **4분 13초** (−76%) |

불변식 `saved 664 + dropped 8,864 = universe 1,191 × 전략 8` 성립.
전략별로 `stored = signals + min(50, 나머지)` 정확히 일치.
`_save_candidate_snapshot` 반환값이 **유니버스 크기 → 저장된 행 수**로 바뀌었다.

> 📌 **미검증으로 남은 것**: 8/3 은 `weak` 장세라 8전략 전부 진입 차단(`strategies_updated`
> 빈 배열)이었다. **신호 게이트와 08:55 주문 사이클의 집합 일치**는 국면이 strong/mixed 로
> 바뀌어 실제 주문이 나가는 날까지 확인할 수 없다.

**계획 대비 이탈 1건**: 계획은 "AI 대상 pre-scoring 루프 **삭제**"였으나 컨텍스트를 쓰도록
**변경만** 했다. 비싼 부분(OHLCV 재조회)은 사라졌지만 루프는 남는다 — 삭제하면 AI 대상
선정이 통째로 없어지는데 그 재설계가 Phase 2 범위이기 때문.

## 8/3 작업 ③ — 17:10 검증 잡 관전 (이월 15·20번) → 새 Sharpe·자동 강등 첫 실측

### 새 Sharpe 공식: 실효 확인

| 전략 | 7/31(구) | 8/3(신) | 음수폴드 | G2P | WFA |
|---|---|---|---|---|---|
| **pullback_v2** | −7.045 | **+0.518** | 1 | 1.086 | ✅ **PASS** |
| donchian_v1 | −2.925 | +0.175 | 2 | 1.167 | fail |
| contrarian_quality | 0.000 | 0.000 | 0 | `Infinity` | fail |
| multi_asset_trend_v1 | −5.822 | −0.015 | 1 | `Infinity` | fail |
| ath_breakout_v1 | −3.467 | −0.054 | 2 | `Infinity` | fail |
| pullback_v3 | −5.535 | −0.112 | 4 | 0.882 | fail |
| donchian_v2 | −8.262 | −0.394 | 4 | 1.179 | fail |
| ath_breakout_v2 | −7.572 | −0.412 | 1 | `NaN` | fail |

−2.9~−8.3의 비현실적 음수가 전부 0 근처로 정상화됐다. **pullback_v2 가 WFA 3조건을 처음
통과** — 8/2 실험에서 "구조적 결함 확정" 판정을 받은 pullback_v3 의 전신이라는 점이 얄궂다.

### 자동 강등 첫 가동 — 4전략 강등

`ath_breakout_v2`(48.6) · `donchian_v1`(48.1) · `multi_asset_trend_v1`(48.4) ·
`pullback_v3`(34.9) → **mock_candidate → research** (점수 <50 연속 10회).
핸드오프는 pullback_v3 하나를 예상했으나 4개가 걸렸다.

> 🔴 **운영 영향 — 판단 필요**: `mock_candidate` 이상이 **6개 → 2개**
> (`ath_breakout_v1`, `donchian_v2`)로 줄었다. `_order_candidates` 가 승격 단계로
> 필터하므로 **주문 가능 전략이 1/3로 축소**됐다. 의도된 동작이지만 규모가 예상보다 크다.

### 승격은 여전히 passed=0, failed=8 — 단 이유가 바뀌었다

- **pullback_v2**: WFA 통과했으나 tradeability **43.5 < 60** 에서 차단
- **ath_breakout_v1**: 점수 **78.7**(live_candidate 임계 75 초과)인데 `mock_months=0.0` 으로 차단

## 8/3 작업 ④ — G2P `Infinity`/`NaN` (이월 결함표 🔴) → 수정·배포 완료

### 8/3 낮 보고 정정

낮에 "`NaN` 은 어떤 비교에도 False → G2P 조건이 **영구 실패**"라고 보고했는데 **반대였다.**
`_evaluate` 는 `if mean_g2p < WF_OOS_IS_G2P_MIN: 실패사유 추가` 구조라 `NaN < 0.6` 도
`inf < 0.6` 도 False → 사유가 붙지 않고 **조용히 통과**한다. 제한이 아니라 **과잉 허용**이었다.

### 근본 원인 — "무손실"이 아니라 "무거래"

`gains`/`losses` 는 일별 수익률에서 나온다. 거래가 0건이면 둘 다 0 → `losses > 0` 이
False → `inf`. **"손실이 없다"와 "거래를 안 했다"가 같은 값**이 됐다.

8/3 fold 15건 실측에서 `oos_g2p = Infinity` 는 **예외 없이 `oos_sharpe = 0.000`**
(그 구간 무거래)과 동반했다. 관측된 13건이 전부 무거래다.

| 전략 | 실태 |
|---|---|
| contrarian_quality | **5/5 폴드 OOS 무거래**인데 G2P 조건 통과 중이었다 |
| multi_asset_trend_v1 | 3/5 무거래 + 음수폴드 1(통과) + Sharpe −0.015 → **Sharpe 가 0만 넘으면 WFA 전체 통과**할 상태였다 |
| ath_breakout_v2 | fold 1 은 IS·OOS 둘 다 무거래 → `inf/inf` = `NaN` |

부수: `_selection_score` 가 비유한 G2P 를 캡으로 바꿔 **파라미터 선택이 "거래 안 하는
조합"에 G2P 만점**을 주고 있었다. 소스 수정으로 함께 해소.

### 변경

| 파일 | 내용 |
|---|---|
| `common/constants.py` | `GAIN_TO_PAIN_CAP=3.0`, `WF_NO_TRADE_FOLD_MAX=1` 신설 |
| `backtest/engine.py` | `_gain_to_pain()` — 손실 있음→비율, 무손실→cap, **무거래→0.0**. `inf` 를 절대 반환하지 않는다 |
| `validation/walk_forward.py` | `g2p_ratio` 비유한 시 0.0 / `FoldResult.oos_trades` + `no_trade_folds` / `_evaluate` 에 비유한 실패 처리 + 무거래 전용 사유 |

> 📌 `oos_trades` 기본값은 **`None`(미지)** 이다. `0` 으로 뒀더니 값을 안 채운 경로가 전부
> "무거래"로 오인돼 기존 테스트가 깨졌다 — **미지와 실측 0을 반드시 구분**할 것.

WFA 통과 조건이 **3개 → 4개**가 됐다. `validation/CLAUDE.md` 와 모듈 docstring 갱신함.
테스트 6건 추가(`test_backtest.py` 2, `test_walk_forward.py` 4). **DB 컬럼 추가 없음.**

**예상 영향**: 8/3 데이터 기준 **판정이 뒤집히는 전략은 없다.** 잠재 거짓통과만 제거된다.

## 8/3 작업 ⑤ — analyze 배치는 "안 돈 게" 아니라 **타임아웃으로 죽었다**

`/etc/cron.d/maps-analyze` 정상, **16:00:01 에 실행됨**. `analysis_run` id=25:
`status=failed`, `picks_count=0`, `error_message=timeout(1800s)`. 16:30:03 강제 종료
— **$8.25 쓰고 0건**.

**원인 1 — 여유가 이미 없었다**: 7/27 17분 → 7/30 28분 → 7/31 29분 → 8/3 **30분 초과**.
→ 조치: `ANALYZE_TIMEOUT` **1800 → 2700**(배포 완료, 서버 반영 확인).

**원인 2 🔴 — strategy-selector 가 승격 단계를 문서에서 추측한다 (미해결)**:
`/opt/maps/.claude/agents/strategy-selector.md` 의
`tools: Read, Task*, WebFetch, WebSearch` — **`Bash` 가 없다.** psql 을 못 써
`promotion_history` 를 볼 수 없으니 사람용 문서인 `HANDOFF.md` 를 데이터 소스로 삼는다.
8/3 에는 그 추측이 틀려 "stage 확인 불가 → 전량 배제"를 냈고, 오케스트레이터가 정정 후
**재선정을 다시 돌려**(서브에이전트 1건 9.6분) 예산을 까먹었다. **타임아웃 상향은 증상
완화일 뿐이다** → Next Steps 참고.

**원인 3 — 내 배포가 실행 중에 겹쳤다 (과실)**: `HANDOFF.md` mtime = **16:25:20**.
analyze 가 16:00~16:30 에 도는 중 `git pull` 이 **에이전트가 읽는 작업 트리를 바꾸고**
`systemctl restart` 까지 했다. → 조치: CLAUDE.md `!deploy` 에 **16:00~16:45 배포 금지**
와 `flock -n /tmp/maps_analyze.lock` 확인 절차 명시(17:48 배포 시 실제로 확인 후 진행).

## 이전 날(8/2) — 전략 실험 시리즈 (운영 콘솔 API 실행, backtest_run_log 에 전부 저장)

새 백테스트 기능(기간·유니버스·판정)으로 pullback_v3 부진의 뿌리를 추적한 결론:

1. **pullback_v3 — 구조적 결함 확정, 게이트 차단이 정당했음.**
   전 기간/3개 강세 구간 × all/KOSPI/KOSDAQ × 진입 파라미터 코너 4개 = 11회 실행 전부에서
   승률 58~75% & **손익비 <1.25** (대부분 <1). 문제는 기간·유니버스·진입이 아니라
   **청산 구조**(MA5 상향 크로스 즉시 익절 — 하드코딩, 파라미터로 못 넓힘).
   rsi<8 개선 효과는 2020 V반등장 한정(2017·2023 재현 실패 — 과적합).
   유일한 PASS: 2020-04~2021-06 + KOSPI 한정(샤프 0.39 턱걸이).
   → 청산 재설계(v3.3, 이익목표/트레일링 파라미터화)는 **보류** — 우선순위 낮음.
2. **donchian_v2 — 실증 승자.** 같은 3개 강세 구간에서 유일하게 유의미한 수익
   (손익비 2.2~6.1, 승률 39~58%). **전 기간(2016~2026) 포트폴리오 리플레이:
   CAGR +8.0%/yr, MDD 19.3%, 샤프 0.67, 449거래, PASS** — 단 연도별로 5/11개 해가
   마이너스(2022 −12.6%), 수익은 2017/2020/2025/2026에 집중. 생존자 편향(현재 ADV 상위
   30 풀) 감안해 할인해 볼 것.
3. **ath_breakout_v1 — KOSPI 대형주에서는 판단 불가** (ATH 갱신 희소 → 거래 8~10건, 표본
   미달). 자연 서식지는 신규상장·중소형 — `recent_ipo` 유니버스로 별도 검증 가치 있음.
4. 참고 관찰: 샤프 단일 잣대는 추세추종의 울퉁불퉁한 수익 곡선에 구조적으로 불리
   (donchian_v2 2023: CAGR +2.3%인데 샤프 0.01). 게이트 설계 재론 시 참고.

**후속 조사·조치 (저녁):**
- 롯데렌탈(089860) pullback_v3 매수 조사 → **버그 아님**: 7/30 08:55 주문 사이클의
  mock(모의) 주문. pullback_v3는 5/23 점수 71.75로 mock_candidate 승격돼 있었음
  (이후 점수 붕괴 28~48에도 강등 메커니즘 부재) → **자동 강등 기능 구현** (`3aaf4fa`).
  다음 검증 잡부터 최근 10회 연속 점수 <50이면 mock→research 강등 + Slack WARN.
  단 새 Sharpe 공식으로 점수가 오르면 연속성이 끊겨 강등 안 될 수 있음 (정당한 재평가).
- 블로그 원고 신규: `docs/blog_series_backtest/11_눌림목_전략_부검기.txt` —
  오늘 실험 시리즈를 리서치 포스팅 스타일로 정리 (붙여넣기 검사 통과, 발행 대기)

## 운영 환경

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`, `https://maps.magable.kr`.
운영 DB PostgreSQL. **SSH 키는 PC마다 다름**: 회사 PC `D:\ssh_maps\`, 집 PC `D:\maps\`.
서버 HEAD는 **`54c29e9`** (이후 HANDOFF 문서 커밋), alembic
**`0020_ai_scoring`**(head). 기능 배포 기준 테스트 **707 passed**.
운영 tracked worktree와 index는 clean이며 서비스 `active`, 내·외부 health 200이다.
서버 `.env`의 계좌 이력 시작일은 `2026-08-05`; 배포 후 health 200과 broker_sync 정상 확인.
KIS 모의계좌는 **`50200591-01`** (8/5 재생성 — 구 50185813-01 만료, 위 "8/5 세션" 절).

> 🔴 **16:00~16:45 KST 는 배포 금지 시간대다** — `/etc/cron.d/maps-analyze` 가 매 거래일
> 16:00 에 `/analyze` 를 45분 상한으로 돌린다. 배포 전 확인:
> `flock -n /tmp/maps_analyze.lock true && echo 배포가능 || echo 대기`

## 8/2 낮 점검 세션 기록 (읽기 전용 — 발견 당시 기준)

백테스트 점검 — 계획서 `C:\Users\jack\.claude\plans\maps-expressive-wreath.md`, 보고서는 채팅으로 전달.

**확인된 정상 동작:**
- 콘솔 E2E (로컬 8001 스모크): `GET /api/v1/backtest` → `POST /run`(pullback_v3, donchian_v1) → `recent_runs` 반영까지 정상. 로컬 alembic이 깨져 있어도 기동 시 `create_all()`이 `backtest_run_log`를 만들어 동작.
- `8db50a9`(numpy 강제변환) 운영 실효 확인: journalctl에 8/2 00:27 `schema "np"` 500이 마지막, 00:30 배포 후 콘솔 실행 3건 연속 200 → `backtest_run_log` 3행 정상 기록.
- 검증 잡: 7/29·30·31 매일 17:12 KST success, 8전략 × WFA/Plateau/MC 각 8건, skipped 없음.
- 운영 데이터: `historical_ohlcv` 2016-01-04~2026-07-31, 585만 행, **2016봉 이상 1,841티커** — WFA 데이터 충분.

**신규 발견 결함 (심각도순, 미수정 — 보고만):**

| 심각도 | 내용 |
|---|---|
| 🔴 | **Sharpe 왜곡** → 이월 20번 |
| 🔴 | **로컬 `maps.db` alembic 스탬프 깨짐** — `alembic_version`이 체인에 없는 `0013_market_regime_korea_weak_guard`. `alembic upgrade head` 불가. `alembic stamp`로 교정 필요 (운영은 정상) |
| 🟡 | `engine.run()` 전 호출 경로가 `market_cap=0.0` → 항상 소형주 슬리피지 0.15% (대형주 비용 과대) |
| 🟡 | `ohlcv_repo.to_dataframe` `index.name` 미설정 → 콘솔 경로 `TradeRecord.ticker=="unknown"` |
| 🟡 | `api/wfa.py:142` `tickers[0]` — 스케줄러 `_pick_wfa_ticker` 개선 미반영 |
| 🟡 | `scheduler._wfa_required_bars()` 2016 하드코딩 — analyzer 기본값과 어긋날 수 있음 |
| 🟡 | `portfolio_replay.py:134` entry-only 신호 시 `sig["exit_signal"]` KeyError 가능; `exit_reason` 항상 `""` |
| ✅ | ~~`gain_to_pain=inf`~~ → **8/3 저녁 수정·배포 완료** (`3728958`). 원인은 "무손실"이 아니라 **"무거래"** 였다. 상세는 "8/3 작업 ④" 절. 낮에 "NaN 은 영구 실패"라고 적었던 것은 **오보** — 실제로는 `inf` 도 `NaN` 도 조건을 **조용히 통과**시켰다 |
| 🔵 | UI 진행률 5단계 장식(실제 1단계만), `progress_pct` 항상 100, "WFA 기준" 문구 낡음, `started_at` UTC naive(화면 9시간 어긋남) |
| 🔵 | `cost_sensitivity.py` `net_cagr=None` 고정 · `engine.run()` `universe` 인자 미사용 · `backtest/CLAUDE.md` 낡음(모듈 3개 누락·상수명 오기) · `/api/v1/wfa`·`robustness` 라우터 테스트 부재 |

## 브랜치·작업공간 상태

- 화면설계 작업은 `feat/ui-design-ppt`에만 있으며 원본 작업공간의 사용자 `HANDOFF.md` 변경을
  보존해 이 문서에 합쳤다. 원본 작업공간의 다른 파일은 수정하지 않았다.
- HANDOFF 갱신 전 로컬 `master`, `origin/master`, 운영 서버 기능 HEAD는 `b065c54`로 일치한다.
  현재 브랜치에는 화면설계 산출물과 `master` 병합 변경이 있다.
- 운영 서버 tracked worktree/index는 clean이다. 과거 analyze 산출 untracked
  파일은 운영에 다수 존재하므로 임의 삭제·커밋하지 말 것.

**부수 확인 (8/3)**: `order_log` `0000031820` = buy `expired` qty 1253 / fill_qty 21 —
이월 8번(부분체결이 만료 처리된다)의 **실물 사례**. 별건으로 남긴다.

## 주의 (이월, 계속 유효)

- **alembic revision ID는 32자 이내** (varchar(32) — 8/1 배포 사고 1).
- **DB에 넣는 수치가 pandas/numpy를 거쳤으면 `float()`/`int()` 강제** — SQLite는 np.float64를
  받아줘서 로컬 테스트로 못 잡는다 (8/1 배포 사고 2, 8/2 운영 로그로 수정 실효 확인됨).
- 배포 실패가 alembic 단계면 서비스는 구 코드로 살아 있다 — 마이그레이션만 고쳐 재배포.
- PowerShell here-string 커밋은 한 호출에 하나만. 닫는 `'@` 뒤에 아무것도 잇지 말 것.
- `date.today()`+UTC 저장 컬럼 함정, `order_log` 컬럼명, `journalctl | grep -v broker_sync`,
  analyze 픽과 스케줄러 주문은 다른 파이프라인, `git add -u` 전에 `git status`.
- 로컬 `.env`는 `MAPS_AUTH_ENABLED=true` — 로컬 API 스모크도 로그인 세션 필요.

## Next Steps

- ✅ **인바디 자동 손절·감사 이력 복구 및 영구 수정 완료(8/11).** 전체 DB 백업, `051160` 원복,
  `041830` BUY 35주 @69,200원/ATR 5638.281459 진입 행 생성, KIS 복합 감사 ID와 sync 불일치
  방어를 TDD로 구현·배포했다. 장중 broker sync `sync_errors=0`, `skipped_sell_orders=0`,
  손절가 55,100원 확인. 상세는 위 "8/11 긴급 복구" 절.

1. ✅ **8/6 주문 2건 장 종료 정합 확인 완료.** 051160/148780 모두 미체결·만료,
   DB `fill_qty=0`, 8/7 00:00 broker sync `expired_orders=2`, 보유 0·현금 1억원으로 일치했다.
   단, EOD 취소 잡의 KIS `40580000 장종료` 오류 처리는 별도 후속으로 남긴다.
2. ~~🔴 8/4 17:10 검증 잡 — G2P 수정 실효 확인~~ → **✅ 8/5 확인·종결** (위 "8/5 세션" 절).
   Infinity/NaN 0건, 무거래 사유 정상 부착, 통과 수 불변.
3. ✅ **analyze stage 입력·운영 후속 완료.** 8/7 정규 cron run id=31이 18분 내 정상 완료됐다.
   서버 `breadth.py` 패치 정식화, DB 자격증명 교체, 로그 권한·비밀 마스킹까지 완료했다.
4. ✅ **자동 강등 4건 정책 판단** — 현행 연속 10회 <50 강등 / ≥60 재승격 유지,
   기존 4건 강등도 유지. 운영 점수 이력으로 재확인 완료.
5. ✅ **구 계좌 성과 경계 운영 적용.** 서버 `.env`에
   `MAPS_ACCOUNT_HISTORY_START_DATE=2026-08-05` 적용 완료. 거래 리뷰에서 002810 제외와
   초기·현재 자산 1억원을 확인했다. 원 주문 감사 행은 보존.
6. ✅ **후보 퍼널 Phase 2 AI 스코어링 구현·배포 완료.** `ad3c79b`, `39fefb3`, `54c29e9`.
   기본 5개 고유 ticker, `off|rerank|replace`, Rule fallback/source, Sonnet 4.6 구조화 출력,
   일일 예산·캐시·평가 CLI·Ops Config 모드 저장까지 반영했다. 8/10 운영 모드를 `rerank`로 전환했다.
   **다음 명시적 운영 단계:** candidate generation 실행 →
   AI provenance·호출 예산·순위 변화 확인. 실제 Bedrock 평가 1회는 성공했지만 AI 기반 후보
   선정 실행은 아직 하지 않았다.
7. 블로그 21편 발행 — 원고 `docs/blog_series_backtest/`, 붙여넣기 검사 통과 상태.
   (신규 기능·Sharpe 수정으로 일부 원고 내용이 낡았을 수 있음 — 발행 전 콘솔 관련
   편의 스크린샷·문구 확인)
8. `google-services.json` 커밋 여부 사용자 결정.
9. 2015년 OHLCV 백필 (운영 절차) — 연 단위 청크로
   `POST /api/v1/scheduler/backfill/ohlcv?start=2015-01-01&end=2015-12-31` → 2016-01-03까지.
   실패는 `job_run_log`에 남음. (운영 data_start 실측 2016-01-04)
10. ath_breakout_v1 × `recent_ipo` 유니버스 검증 (IPO 전략 글 가설 — 콘솔에서 바로 가능)
11. ✅ **pullback_v3 청산 재설계 (v3.3)** — 병렬 연구 후보 구현·배포·운영 데이터 평가 완료.
    2R 목표 + 1.5R/0.5R 트레일링 + MA_long 이탈 구조와 6개 조합을 검증했으나 모두
    2020 구간만 통과하고 2017·2023 Sharpe 기준에서 탈락했다. 전체 기간/WFA는 게이트에 따라
    실행하지 않았고, scheduler에서 격리한 research 상태를 유지한다. 위 8/6 절 참고.

### 이월 (번호 유지 · 22번 8/3 신규 · 7·10·20·21번 종결)

5. 워치리스트·보유 화면 브라우저 CSS 확인 (네비 1줄, 카드 2열)
6. 픽 만료 가드 로그 — ARMED 픽 0건이라 아직 안 뜸
7. ~~KIS 잔고·주문 페이지네이션~~ → `86d5b7e` 해소. **8/3 운영 확인 완료 — 단 "미검증"**:
   모의계좌 보유가 6/15~8/3 내내 **0~3종목**이라 페이지네이션이 발동한 적이 없다
   (`portfolio_snapshot.holdings` 이력). 수정은 무해하나 실효는 입증 불가.
   재시도 소진 WARNING은 배포 전(7/31~8/2) 0건 / 후(8/2~8/3) 0건, `sync_errors` 전 사이클 0,
   broker_sync 하트비트 거래일 1,435~1,439회/일 정상. **초당 한도(EGW00201) 압박 징후 없음.**
   → 20종목을 넘는 순간 자동으로 드러나도록 **관측성 보강**함(아래 절). 재조사 불필요.
8. 🟡 부분체결이 만료 처리된다 (`expire_pending_orders`)
9. 🟡 매매일지 페어링이 티커 단위 (`trade_review.py:119`)
10. ~~🟡 후보 퍼널 재설계~~ → **Phase 1 배포·실측 완료 (8/3)**. 위 "8/3 작업 ②" 절 참고.
    저장소 정본 `docs/plans/candidate-funnel-ai-scoring.md` 를 개정본으로 갱신해 뒀다
    (원안 정정 4건 포함). **Phase 2(AI)만 남았다** → Next Steps 6번.
11. 🟡 후보 생성 누락일 2건 (7/01, 7/17) 잡 실패 로그 확인
12. 🟡 분석 워치리스트 누적 2건뿐 — 게이트 전량 탈락 중
13. `analysis_pick` id=1 CLOSED인데 exit_reason 빈 것
14. 📌 모의계좌 6/01 9,977만 → 7/30 8,513만 (-14.7%), 버그 수정으로 앞으로는 기록됨.
    **8/5 갱신**: 계좌 재생성으로 잔고 1억 리셋 — 이 이력과 새 계좌를 잇지 말 것
15. **8/3 실측으로 갱신**: 새 Sharpe 적용 후에도 승격은 여전히 passed=0/failed=8 이지만
    **이유가 바뀌었다.** `ath_breakout_v1` 은 점수 **78.7**(live_candidate 임계 75 초과)인데
    `mock_months=0.0` 으로 차단, `pullback_v2` 는 WFA 유일 통과인데 점수 43.5 < 60 으로 차단.
    즉 병목이 "점수가 낮아서"에서 **"mock 기간/점수 중 하나가 어긋나서"** 로 이동했다.
    ~2026-09-01 `mock_months ≥ 3` 충족 예정이던 계산은 강등 4건으로 무효 — 재산정 필요.
16. 업종 필터 활성화 (`earnings_revision` 0.25 자리표시자)
17. 애드센스 — `maps.magable.kr` 등록 (사용자 계정 작업)
18. 블로그 기획서 수정본 — 원고(시리즈 11편 + 백테스트 10편)를 정본으로 삼는 편이 빠름
19. 이월: KIS 90020000 장외 경고, `/opt/stock_report` 버전관리, 네트워크 테스트 mock화,
    서명 릴리스 APK, `order_log_backup_20260724` DROP 가능
20. ~~🔴 Sharpe 산출 구조 왜곡~~ → **수정 완료 (8/2 오후 작업 세션, 미커밋)**.
    방식: rf를 **일별 투자 비중(노출)만큼만 차감** — `engine._compute_metrics`와
    `portfolio_replay._metrics` 동일 규칙. 완전 투자 시 종전 공식과 동치, 노출 규모에
    수학적으로 불변. 같은 조건 실측: 콘솔 pullback_v2 3개월 샤프 **−12.52 → +0.650**.
    회귀 테스트 5건(`test_backtest.py`·`test_portfolio_replay.py`).
    🟡 4번(ticker="unknown")도 동반 수정(`to_dataframe`이 `index.name=ticker` 설정).
    🟡 3번(market_cap=0 슬리피지)은 **미처리** — DB에 시총 데이터가 없어 원천 확보가 선행.
    **잔여 후속 → 8/3 해소**: 8/3 17:10 검증 잡에서 새 공식 첫 산출. 8전략 전부
    −2.9~−8.3 → 0 근처로 정상화, `pullback_v2` WFA 첫 통과. **이 항목 종결** (상세는
    "8/3 작업 ③" 절). 다만 G2P `Infinity`/`NaN` 이 새 병목으로 드러났다 → 결함표 참고.
21. ~~🔴 로컬 `maps.db` alembic 스탬프 깨짐~~ → **해소**. 현재 운영 head는
    `0020_ai_scoring`; 오염 없는 임시 로컬 DB에도 전체 migration chain 적용을 검증했다.
22. ~~🟡 **자동 강등 규모** (8/3 신규)~~ — 첫 가동에 4전략이 `mock_candidate → research`.
    주문 가능 전략 6개 → 2개. **8/6 운영 이력 재검토 후 현행 정책 유지로 결정·종결**:
    연속 10회 <50 강등, research에서 ≥60 재승격(Next Steps 4번 및 8/6 절 참고).

## 핵심 파일 맵 — 8/6 로컬 변경분

- `maps/promotion/stage_snapshot.py` — 최신 통과 승격 이력으로 전략별 현재 단계 JSON 생성.
- `scripts/export_strategy_stages.py` — analyze cron용 단계 컨텍스트 exporter.
- `.claude/commands/analyze.md`, `.claude/agents/strategy-selector.md`,
  `scripts/run_analyze_cron.sh` — DB 기반 단계 JSON 주입과 추측 금지 규약.
- `maps/common/account_history.py`, `maps/common/settings.py` — 신 계좌 이력 기준일과 KST/UTC 경계.
- `maps/api/trade_review.py`, `maps/api/dashboard.py`, `maps/api/live_monitor.py`,
  `maps/ops/scheduler.py` — 구 계좌 감사 행을 보존한 채 현재 계좌 성과 지표에서 제외.
- `tests/test_promotion_stage_snapshot.py`, `tests/test_account_history.py`,
  `tests/test_trade_review.py` — 승격 단계·강등·계좌 경계 회귀 테스트.

## 핵심 파일 맵 — 8/3 변경분

- `maps/ops/scheduler.py` — `TickerContext`(dataclass), `_build_ticker_contexts()`,
  `_signal_from_frame()`(신호 정본), `_latest_strategy_signal()`(DB 래퍼),
  `_SIGNAL_LOOKBACK_BARS=400`, `_save_candidate_snapshot(contexts=, stats=)`.
- `maps/execution/kis_adapter.py` — `_fetch_paged()` 2페이지 이상 INFO 로그.
- `maps/common/models.py` — `CandidateSnapshot.entry_signal`.
- `maps/common/settings.py` — `maps_candidate_snapshot_top_n`(기본 50).
- `tests/test_candidate_funnel.py` — 신규 6건 (신호 경로 일치, N회 로드, 게이트, 카운터).
- `alembic/versions/0018_candidate_entry_signal.py`.
- `maps/backtest/engine.py` — `_gain_to_pain()` (무거래=0.0 / 무손실=cap, **inf 반환 없음**).
- `maps/validation/walk_forward.py` — `FoldResult.oos_trades`(기본 `None`=미지),
  `WalkForwardResult.no_trade_folds`, `g2p_ratio` 유한 보장, `_evaluate` 조건 4개.
- `maps/common/constants.py` — `GAIN_TO_PAIN_CAP`, `WF_NO_TRADE_FOLD_MAX`.
- `scripts/run_analyze_cron.sh` — `ANALYZE_TIMEOUT` 2700.
- `CLAUDE.md` — `!deploy` 안전 수칙에 alembic 경고 + 16:00~16:45 배포 금지.

## 핵심 파일 맵 (8/2 점검에서 본 곳)

- `maps/backtest/engine.py` — `BacktestEngine.run`, `_compute_metrics`(Sharpe·rf 3%), 사이징 상수.
- `maps/api/backtest.py` — 콘솔 API (`GET /api/v1/backtest`, `POST /run`), `RUNNABLE_STRATEGIES` 7종,
  `float()`/`int()` 강제변환(8db50a9), `BacktestRunLog` INSERT.
- `maps/ops/scheduler.py` — `run_validation` → `_generate_validation_metrics` → WFA/Plateau/MC 저장.
- `maps/validation/walk_forward.py` — 2016봉 요구, 통과 3조건 (sharpe_mean>0 AND 음수폴드≤1 AND G2P≥0.6).
- 응답 스키마 필드는 `recent_runs` (`api/schemas.py:305`) — `runs` 아님.
- **운영 접속**(집 PC): `ssh -i D:\maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`.
