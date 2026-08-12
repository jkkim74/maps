# HANDOFF

## 8/12 개인화 1차 — 계정·권한·개인설정 (로컬 구현 완료, 미배포)

- MAPS 는 지금까지 공용 비밀번호 1개짜리 단일 사용자 시스템이었다. `app_user` 테이블과
  역할(`admin`/`user`)을 도입해 **"누가 요청했는지"를 시스템 전체가 알게** 만들었다.
  마이그레이션은 **`0024_app_user`** 다.
- **자동매매 경계는 그대로다.** 주문·무장·Kill Switch·스케줄러·승격은 전부 관리자 전용이고
  운영자 1계좌에서만 돈다. 전역 파이프라인 로직은 한 줄도 바꾸지 않았다.
  유료 사용자 계좌 연결은 `app_user.plan` 컬럼만 자리로 두고 구현하지 않았다.
- 권한 강제 지점은 `maps/api/auth.py` 의 게이트 미들웨어 **한 곳**이다. 라우터 26개에
  `Depends` 를 뿌리지 않았다. `_USER_ALLOWED` **허용 목록**에 없으면 전부 관리자 전용이라
  새 라우터가 실수로 열리지 않는다. 일반 사용자에게는 GET 만 열려 있어 `arm-plan` 같은
  상태 변경(POST)이 자동으로 막힌다.
- 역할·상태를 세션이나 모바일 토큰에 굽지 않고 **매 요청 DB에서 다시 읽는다.** 계정을
  비활성화하면 이미 로그인한 세션과 발급된 토큰이 즉시 막힌다(테스트로 확인).
- 비밀번호는 **표준 라이브러리 `hashlib.scrypt`** 로 해시한다(`maps/common/passwords.py`).
  `passlib`/`bcrypt` 의존성을 늘리지 않았고, 파라미터를 저장값에 담아 나중에 세기를 올려도
  기존 해시가 검증된다. `.env` 자격증명은 **계정이 0개일 때 관리자 1명을 시드**하는 데만
  쓰이고 그 뒤 인증 경로에는 남지 않는다(뒷문 방지).
- 개인정보 경계 2가지를 1차에 포함했다 — 없으면 첫 일반 사용자에게 바로 문제가 된다:
  - `stock_analysis_history`·`analysis_pick` 에 `owner_user_id`(nullable) 추가. 기존 행은
    `NULL` = 운영자 데이터라 **일반 사용자에게 보이지 않고** 관리자에게는 그대로 보인다.
    backfill 없음.
  - 계정별 **일일 AI 분석 한도**(기본 10회, 관리자 무제한). 한도 초과는 Bedrock 을
    호출하기 **전에** 429로 끊는다.
- 화면: `/settings`(내 설정 — 시작 화면·후보 표시 필터·알림·비밀번호 변경),
  `/admin/users`(회원 관리 — 생성·활성/비활성·역할 변경·임시 비밀번호 재발급).
  네비게이션은 역할로 걸러지지만 **그건 편의일 뿐 통제는 게이트가 한다.**
  `/` 는 일반 사용자를 개인 시작 화면으로 리다이렉트한다(대시보드는 운영자 잔고를 그린다).
- 모바일 앱은 운영자 계좌·포지션을 반환하므로 **1차에서 관리자 전용**이다. 일반 사용자가
  모바일 로그인하면 403.
- 검증: 전체 Python **861 passed**(경고 22건), `maps/tests` **81 passed**,
  신규 `tests/test_passwords.py` 9 + `tests/test_users.py` 18,
  빈 SQLite 전체 migration 0001→0024, JS 문법 검사 통과.
- **아직 커밋·배포하지 않았다.** 배포 시 `alembic upgrade head`(0024)가 필수이며, 운영
  PostgreSQL 백업을 먼저 만든다. 배포 직후 최초 기동에서 기존 `.env` 자격증명으로 관리자
  계정이 1건 시드되는지 확인할 것.

### 다음 단계 (2차 후보)

1. 개인 워치리스트(관심종목)를 `analysis_pick` 과 분리해 신규 테이블로
2. 개인 알림 실제 발송 분리 — 지금은 설정 저장까지만 하고 전송은 여전히 전역 대상이다
3. 자가 가입 신청 폼(`status=pending` 컬럼은 이미 있다) + 승인 화면
4. 유료 단계로 갈 때: `user_broker_account`(암호화 키 보관), 사용자별 스케줄러·Kill Switch·
   노출 한도, KIS 호출 한도 공유, **투자일임업 검토**

## 8/12 문서 지도 정비 — index.md · 패키지 CLAUDE.md · HANDOFF 아카이브

- 코드 탐색 진입점으로 루트 **`index.md`** 를 만들었다. "무엇을 찾나 → 어디로" 라우팅 표,
  패키지 지도, 코드 밖 디렉터리, 상황별 문서, 자주 밟는 지뢰 5절 구성이다. 읽기 순서는
  `index.md` → 해당 패키지 `CLAUDE.md` → 소스이며 이 규칙을 루트 `CLAUDE.md` 상단에 넣었다.
- 패키지 `CLAUDE.md` 15개를 실제 파일 목록에 맞춰 갱신하고, 문서가 없던
  `maps/ai`, `maps/stock_analysis` 와 코드 밖 `tests/`, `scripts/`, `alembic/`, `apps/mobile/`
  에 새로 썼다. 갱신 전 드리프트는 미문서화 모듈 40개(strategy 14 · api 8 · ops 7 · market 4 ·
  backtest 3 · data 3 · common 1)와 유령 항목 6개였다.
- 문서가 다시 낡는 것은 `tests/test_docs_index.py` 로 막는다. 각 패키지 문서의
  `## Directory structure` 트리가 실제 `.py` 목록과 **집합으로 정확히 일치**해야 하고,
  루트 `index.md` 가 모든 패키지·부속 문서를 링크해야 한다. **22 passed**.
- `HANDOFF.md` 는 76,986 bytes / 30개 절이라 한 번 읽는 데 토큰 상한에 걸렸다. 최근 4개 절과
  Goal·주의·Next Steps·브랜치 상태만 남기고 **22개 절을 `docs/handoff_archive/2026-08.md`
  로 그대로 옮겼다**(요약·삭제 없음, 절 30개 보존을 스크립트로 대조 확인). 25KB + 52KB 로 분리.
- 값을 문서에 복사하지 않는 원칙을 지켰다 — 손절률·호가표·MDD·가중치는 함수·상수를 가리키기만
  한다. 다만 루트 `CLAUDE.md` 에서 실제로 빠져 있던
  `contrarian_quality_accumulation_v1` → `contrarian_quality`(MDD 25%) 행은 채웠다.

## 8/12 시장·후보 점수 100% 실측 게이트 구현·운영 배포 완료

- 문제 원인은 시장 종합점수의 미연결 유동성·투자심리를 중립값처럼 다루고 후보 전략 점수도
  일부 입력을 추정값으로 채워, 표시 점수와 주문 가능 점수를 구분할 준비도 메타데이터가 없던
  것이었다. 이제 측정값만 재정규화해 표시하되 `coverage/status/ready`, 측정·결측 항목과 출처를
  시장 로그·후보 스냅샷·API·일일 다이제스트에 함께 저장한다.
- 모든 자동 신규 BUY(후보 주문, 단일/분할 전략매매)와 전략 승격은 시장·후보 점수가 정확한
  기준일에 100% 실측돼야 통과한다. SELL·손절·익절·기존 포지션 청산에는 이 게이트를 적용하지
  않는다. 불완전 점수는 보이지만 주문에는 쓰이지 않는다.
- pykrx 외국인·기관·개인 순매수, KRX OHLCV 시장 내부지표, Naver 공식 뉴스 검색, Bedrock 구조화
  심리점수를 연결했다. 눌림목·돌파·멀티에셋 후보는 실제 OHLCV·수급·펀더멘털 컴포넌트를 쓰며,
  없는 값은 중립 50으로 만들지 않는다. migration은 `0023_score_readiness_feeds`다.
- 커밋은 `c892cf8`(기능)과 `db2ad4b`(독립 실행 가능한 수급 backfill)이며 master push 완료.
  전체 Python **810 passed**(경고 22건), 신규 집중 테스트 5 passed, 빈 SQLite 전체 migration
  0001→0023과 운영 PostgreSQL 0022→0023을 확인했다.
- 운영 배포 전 custom-format 백업
  `/opt/maps/backups/pre_score_readiness_20260812_140608.dump`(323,961,144 bytes, mode 600)을
  생성했다. 앱 계정 권한이 없는 기존 임시 테이블 `order_log_backup_20260724`만 백업 대상에서
  제외했다. 배포 중 첫 안전중단 뒤 `create_all`이 미리 만든 신규 피드 테이블 두 개는 모두 0행임을
  확인하고 제거한 뒤 Alembic으로 재생성했다. 운영은 HEAD `db2ad4b`, Alembic `0023 (head)`,
  systemd `maps=active`, `/health` ok, 배포 후 ERROR/Traceback 0건이다.
- 최근 수급 backfill은 8/3~8/11 7영업일 **18,461건** 저장했다. 8/12는 장 마감 전이라 수급이
  비어 정상적으로 실패 기록만 남겼다.

### 후속 커밋 `0e3a83e`·`3fcfae6` — Naver API Hub 이전과 뉴스 건수 정합 (배포 완료)

- Naver 뉴스 검색이 구 `openapi.naver.com` 으로는 동작하지 않아 **API Hub**
  (`naverapihub.apigw.ntruss.com`, `X-NCP-APIGW-API-KEY-ID`/`X-NCP-APIGW-API-KEY` 헤더)로
  옮겼다(`0e3a83e`). 설정 변수명은 `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET` 그대로지만
  값은 **NCP API Hub 키**다.
- Bedrock 이 돌려준 긍정·중립·부정 건수 합이 기사 수와 어긋나면 비율을 유지한 채 재정규화하고,
  불가능하면 실패로 기록한다(`3fcfae6`).
- 8/12 15:24 KST 실측: 운영 HEAD `3fcfae6`, alembic `0023 (head)`, `maps=active`,
  `market_news_sentiment` 8/12 = `success`(score 67.0, positive, 기사 100건,
  `us.anthropic.claude-sonnet-4-6`), broker_sync `sync_errors=0`, 최근 2시간 ERROR 0건.
- **아직 게이트는 닫혀 있다.** `market_regime_log` 최신 행(ref_date 8/12, 08:55 KST 생성)은
  `score_coverage_ratio=0.0`, `score_status=unavailable`, `score_ready=false` 이고
  `investor_flow_snapshot` 은 8/11(2,630건)까지다 — 8/12 수급은 장 마감 후에 들어온다.
- 다음 확인은 8/12 16:00 analyze·16:40 수집 이후에 시장·후보 coverage 1.0 과
  `score_ready=true` 를 보는 것이다. 이 확인 전 눌림목 전략을 mock 후보로 올리거나
  신규 주문 게이트를 끄면 안 된다.

## 8/12 종목분석 이력·현재가 갱신·전략매매 UX 구현 완료

- 작업 브랜치 `feat/stock-analysis-history`에서 설계·계획의 7개 작업을 구현하고 원격 push,
  `master` fast-forward 병합, 운영 배포까지 완료했다. 기능 배포 커밋은 `1b2903a`다.
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
- 09:48 KST 운영 반영 완료: 배포 전 PostgreSQL custom-format 전체 백업
  `/opt/maps/backups/maps-pre-stock-analysis-history-20260812-094730.dump`
  (323,947,622 bytes, mode 600)을 만들고 `pg_restore -l` 검증을 통과했다. 운영 alembic은
  **`0022_stock_analysis_history (head)`**, systemd `maps=active`, 내·외부 `/health` 200,
  서버 tracked worktree clean이다. 신규 테이블 접근과 초기 이력 0건도 확인했다.
- 실제 KIS/Bedrock 전체 분석 호출은 불필요한 운영 데이터·AI 비용을 만들지 않도록 배포 과정에서
  실행하지 않았다. 다음 사용자의 정상 종목분석 1회 후 이력 1건 저장, 현재가 갱신, 재분석 2행
  누적을 화면에서 확인하면 된다.

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

> 이 문서에는 최근 작업만 남긴다. 그 이전 기록은 삭제하지 않고
> [docs/handoff_archive/2026-08.md](docs/handoff_archive/2026-08.md) 로 옮겼다.
