# HANDOFF

## 8/27 미완성 후보 점수의 순위 격리 — ✅ 구현·검증 완료, ⏸️ 미배포

8/25 절이 다음 작업으로 예약해 둔 **미완성 후보 점수 격리**를 브랜치
`feat/incomplete-candidate-score-isolation` 에 TDD로 구현했다. 설계·계획 정본은 아래 두 문서다.

- `docs/superpowers/specs/2026-08-27-incomplete-candidate-score-isolation-design.md`
- `docs/superpowers/plans/2026-08-27-incomplete-candidate-score-isolation.md`

문제는 8/24에 실제로 드러났다. 5개 평가항목 중 `valuation_margin_score` 하나만 채워진
후보들이 `final_score=100.0` 으로 상위 목록을 차지했다. `score_ready=false` 라 자동주문은
이미 차단됐지만 **화면과 매매일지에서는 완성된 100점 후보처럼 보였다.**

완성 판정을 `ops/candidate_selection` 한곳으로 모으고 저장·AI·API·다이제스트·화면·매매일지가
모두 그 하나를 쓰게 했다.

- `candidate_score_complete(row)` / `candidate_score_complete_expression()` —
  `score_ready` AND 커버리지 ≥ 1.0. 파이썬 판정과 SQL 식이 같은 규칙이다.
- 스냅샷 저장 상한은 비신호 행을 **완성 우선 → 점수 내림차순 → ticker** 로 정렬한다.
  `entry_signal` 행은 미완성이라도 감사용으로 보존한다.
- AI 스코어링 대상에서 미완성 행을 제외했다 — 완성될 수 없는 행에 예산을 쓰지 않는다.
- 후보 API 는 `candidates`(완성)와 `incomplete_candidates`(미완성)를 분리하고
  `ready_count` / `incomplete_count` 를 필터 전 기준으로 함께 준다. 개인 `candidate_min_score`
  필터는 완성 목록에만 건다 — 미완성 점수는 비교 대상이 아니다.
- 다이제스트에 `candidate_ready_total` / `candidate_incomplete_total` /
  `incomplete_candidates` 를 추가했다. 완성 행이 하나라도 있는 ticker 는 완성 목록에만 실어
  같은 종목이 두 목록에 겹치지 않는다.
- 화면은 `완성된 매매 후보` 표와 `데이터 미완성 후보` 감사 표를 나누고, 미완성 행에
  커버리지·누락 평가항목·`순위 비교 금지` 배지를 붙인다. KPI 도 완성/미완성으로 쪼갰다.
  전략 선택에 빠져 있던 `contrarian_quality_accumulation_v1` 도 넣었다.
- 매매일지 규칙(`.claude/commands/blog.md`)에 미완성 후보 서술 규칙을 넣었다. `final_score`
  는 반드시 `부분 산출값`·`순위 비교 금지` 로 쓰고, `missing_components` 가 비면 추정하지
  말고 `누락 항목 미기록` 이라고 쓴다.

DB 마이그레이션·백필·새 설정·새 의존성은 없다. Alembic head 는 `0028_holding_regime_audit`
그대로다. 자동 BUY/SELL 안전 게이트와 주문 미리보기 감사 행의 동작도 바꾸지 않았다.

커밋은 `7d87ad3`(설계) → `b2a7006`(공통 판정·저장·AI) → `a9cb1e4`(API·다이제스트) →
`521682c`(화면·매매일지·패키지 문서)다.

검증은 집중 회귀 **98 passed**, `pytest tests -q` **971 passed, 13 warnings**,
`pytest maps/tests -q` **81 passed**, `python -m compileall maps -q`,
`python -m alembic heads`(`0028_holding_regime_audit`), `git diff --check` 전부 통과다.

> ⏸️ **아직 배포하지 않았다.** `master` 병합도 하지 않았고 운영 HEAD 는 `45c4d4e` /
> Alembic `0028_holding_regime_audit` 그대로다. 마이그레이션이 없으므로 배포는 병합 후
> `git pull` + `systemctl restart` 만으로 끝나지만, 16:00~16:45 KST analyze 창은 피한다.

작업공간에는 사용자의 기존 변경인 `docs/blog_series_backtest/11_눌림목_전략_부검기.txt`
삭제와 `docs/diary/`·`docs/stock/` 미추적 파일이 그대로 남아 있다. 어느 커밋에도 넣지 않았다.

## 8/26 보유 장세 오버레이 — ✅ shadow 구현·운영 배포 완료

**현재 장세와 매수 당시 장세를 비교하는 보유 장세 오버레이**를 `master`에 구현·운영
반영했다. 구현 계획 정본은 아래 문서다.

`docs/superpowers/plans/2026-08-25-holding-regime-overlay.md`

현재 자동후보 진입은 장세를 반영하고 주문의 `decision_context.market`에 진입 당시 장세를
저장하지만, 일반 보유 포지션 청산은 최초 `strategy_id`의 손절·목표·전략 신호만 사용한다.
따라서 강세/혼조장에서 매수한 모멘텀·돌파·눌림목 포지션이 약세 전환 뒤에도 기존 청산
신호가 나올 때까지 남을 수 있다.

확정한 1차 원칙은 다음과 같다.

- **기존 전략을 새 장세 전략으로 바꾸지 않는다.** 최초 전략과 매수 근거는 끝까지 유지한다.
- 별도 오버레이가 `HOLD`, `WATCH`, `EXIT`만 판단한다.
- 최근 서로 다른 2개 장세 관측에서 불리 조건이 연속 확인돼야 `EXIT` 후보가 된다.
- 1차 자동 EXIT 대상은 `pullback_short`, `ath_outlier`, `donchian_research`다.
  `multi_asset`, `contrarian_quality`는 원래 전략 신호를 우선해 1차에서는 강제 청산하지 않는다.
- 진입 장세 누락, 현재 장세 누락·노후화, 미등록 전략은 오버레이 `HOLD`로 처리하되 기존
  손절·익절·전략 청산은 계속 작동한다.
- 1차 범위에는 부분매도·새 손절가·손절선 강화·전략 교체를 넣지 않는다.
- 설정은 `off|shadow`, 기본은 **`shadow`**다. `enforce`와 실제 매도 코드는 v1에서 폐기했다.
  `EXIT`는 감사 판정일 뿐 주문을 만들지 않는다.
- 1차 대상은 구조화된 `strategy_id`와 진입 장세가 모두 저장된 자동후보 보유분이다.
  `AnalysisPick.strategy_context`는 자유문자열이라 신뢰할 수 있는 전략 ID가 아니므로 전략매매
  단일·분할 보유는 기존 브래킷 경로에 남기고, 오버레이가 추정하거나 이중매도하지 않는다.

순수 판정기, 감사 테이블/Alembic 0028, 자동후보 보유 연결, 다이제스트와 운영문서를
TDD로 구현했다. 후보 스냅샷 실체·날짜·종목·전략을 검증하고, 진입 이후 체결 SELL이나
진입수량을 초과한 현재 보유는 출처 불명으로 HOLD 처리한다. BOUGHT 분석픽은 감사 생성과
다이제스트 연결 양쪽에서 제외한다.

최종 전체 회귀는 **957 passed, 13 warnings**, 집중 회귀는 **117 passed, 5 warnings**다.
`python -m compileall maps -q`, `git diff --check`, `python -m alembic heads`
(`0028_holding_regime_audit`)도 확인했다.

기능 배포 커밋은 `dcb1656`, 문서까지 포함한 최종 운영 HEAD는 `45c4d4e`다. 배포 전 PostgreSQL custom-format 백업은
`/opt/maps/backups/pre_holding_regime_shadow_20260826_153013.dump`
(328,208,835 bytes, mode 600)이며 `pg_restore -l` 검증을 통과했다. 운영 Alembic은
`0027_order_decision_context`에서 `0028_holding_regime_audit`로 올라갔고, 실효 설정은
`shadow`, 최대 간격은 3일이다. systemd `maps=active`, 내부·외부 `/health` 200,
재시작 이후 ERROR/CRITICAL/Traceback 0건이다.

배포 후 `broker_sync`는 2026-08-26 15:33:07 KST에 성공했다. 기존 보유분 감사 11건은
모두 `HOLD/ENTRY_CONTEXT_UNAVAILABLE`이다. 이 포지션들은 새 `decision_context` 도입 전에
진입해 검증 가능한 진입 장세가 없으므로 의도대로 실패 개방됐다. 배포 이후 생성된 SELL
주문은 0건이다.

## 8/25 8월 24일 주문 감사·Market Summary P1 — ✅ 운영·모바일 연결 완료

커밋 `9503791`을 master와 운영 서버에 배포했다. 운영 HEAD는 `9503791`, Alembic은
`0027_order_decision_context (head)`, systemd `maps=active`, 내부 `/health` 200이다.
전체 회귀 테스트는 **931 passed, 13 warnings**다.

- 자동매수 주문에 후보 스냅샷, 주문 순간 장세·주간추세·변동성·진입한도, 가격·수량·ATR을
  `order_log.decision_context` JSON으로 원자적으로 고정한다.
- digest execution은 주문 시점 근거를 우선하며 UTC DB 시각을 KST ISO로 출력한다. 기존 주문은
  같은 날 장 마감 후보를 보지 않고 직전 거래일까지 제한하며 `DECISION_CONTEXT_INFERRED`를 붙인다.
- Market Summary는 KOSPI `^KS11`, KOSDAQ `^KQ11`만 허용하고 각각 60행 이상, 최신 7일 이내,
  최근 60행 최대 공백 7일 이내인지 검사한다. 실패한 외부 리포트는 HTML을 인용하지 않고
  digest에 상태와 오류만 노출한다.

운영 외부 도구 `/opt/stock_report/report_generator.py`와 `marketSummary.py`도 교정했다.
배포 후 summary smoke run `id=356`은 `completed`; 두 지수 모두 202행, 최신일 2026-08-25,
최대 공백 4일, HTML 47,659 bytes다. 배포 후 SHA-256은 각각
`0c784de408f40ea2cc2949d585bf65437936aa254c467e3518e443c4e3dd5e51`,
`60bbbb5f065a2cc365683bef64db6767818e2cfb21b898f382a784a759906af7`이다.

8/24 주문 `kis:d59a650c:20260824:0000000683`(row 71)에 검증된 역사 감사정보를 백필했다.
실제 주문 근거는 8/21 후보 406247, 점수 38.27, 추세강도 76.52, 주문 시각
2026-08-24 08:55:35 KST, MIXED/pass/HIGH, 진입한도 0.25다. 마감 뒤 저장된
fail/0.0과 당일 후보 50.02/100.0은 주문 근거가 아니다. 기존 summary row 354는
`^KS200` 오사용과 2026-07-16..2026-08-24 39일 공백 때문에 HTML을 보존한 채 `failed`로
무효화했다. 교정 digest는 `/opt/maps/logs/digest_2026-08-24-p1.json`, 교정 일지는
`/opt/maps/diary/20260824.txt`; 원본은 `20260824.txt.bak-p1-20260825`다. 로컬 원본도
`docs/diary/20260824.pre-p1-20260825.bak.txt`로 보존했다.

배포 전 전체 DB 백업은
`/opt/maps/backups/pre_p1_order_audit_20260825_155521.dump`(327,795,975 bytes, mode 600)다.
외부 도구 원본은 `/opt/stock_report/*.bak-p1-20260825_155521`에 보존했다.

모바일 환경은 공식 ChatGPT Windows 앱 `26.818.8289.0`을 설치·실행했다. 현재 고성능 전원
계획에서 AC/DC 절전 타이머 모두 0(절전 안 함)이다. 사용자가 동일 ChatGPT 계정으로 QR
페어링을 완료했고, 2026-08-25 휴대폰에서 보낸 연결 확인 메시지가 이 작업 대화에 도착해
모바일 → PC 작업지시 경로를 확인했다. 이 확인 응답으로 PC → 모바일 완료 알림도 시험한다.

### 후속 권장 작업 — 미완성 후보 점수의 순위 격리

8/24에는 5개 평가항목 중 `valuation_margin_score` 1개만 채워진 후보들이 `final_score=100.0`으로
상위 목록을 차지했다. `score_ready=false`라 실제 자동주문은 차단되지만 화면과 매매일지에서는
완성된 100점 후보처럼 보일 수 있다. 보유 장세 오버레이 다음 작업으로 아래 범위를 권장했으며
**아직 미착수**다.

- `score_ready=true`인 완성 점수만 매매 후보 순위에 포함한다.
- 부분 점수는 별도 데이터 미완성 목록으로 분리한다.
- 점수 커버리지와 누락 평가항목을 API·digest·화면·매매일지에 명시한다.
- 부분 점수를 정상 100점처럼 설명하지 못하도록 회귀 테스트를 추가한 뒤 운영 배포한다.

그다음 후보는 자동매수 유동성 하한 적용이다. 아래 8/21의 AI 권고 NULL 방어 P0 설계는
사용자 결정에 따라 계속 보류하며, 별도 재개 지시 없이는 구현하지 않는다.

## 8/21 8월 20일 매매기록 운영 점검 — P0 설계 작성 후 ⏸️ 보류

`docs/diary/20260820.txt`를 코드·HANDOFF와 대조했다. 8/20 주문 사고나 리스크 게이트
우회 정황은 확인되지 않았다. 에스넷 914주 매수는 MIXED 장세의 실효 주문 상한 1건 안에서
지정가보다 낮게 전량 체결됐고, 현금 + 보유평가액 = 총자산 및 11개 보유종목 평가액 합계도
일치했다. 익일 후보 10건 제외와 두산에너빌리티 3차 진입 대기도 기록된 조건대로다.

운영 위험 기준 최우선 개발 후보는 **기존 AI 전략매매 픽의 권고 불명 상태 방어**로 정했다.
신규 픽은 `source='ai_trade_plan'`과 `ai_recommendation`을 함께 저장하지만, 컬럼 도입 전
기존 행은 backfill하지 않아 원래 권고 종류를 DB에서 알 수 없다. 실제 `pick_id=9`
두산에너빌리티는 원래 WATCH였으나 현재 `ai_recommendation=NULL`이고, 3회 중 2회 체결 후
3차 36주 @71,000원이 대기 중이다.

사용자는 아래 정책과 설계를 한 차례 승인했다.

- `source='ai_trade_plan' AND ai_recommendation IS NULL`이면 신규·잔여 BUY를 차단한다.
- 기존 보유분의 손절·익절은 계속 실행하고, 수동 픽의 NULL은 정상으로 둔다.
- 운영자가 원래 권고 `BUY|WATCH|SELL`을 한 번 복원하면 기존 정책대로 WATCH/SELL은
  경고·감사 기록 후 진입을 허용한다.
- 신규 컬럼·마이그레이션 없이 기존 `source`와 `ai_recommendation`을 사용한다.

설계 문서는
`docs/superpowers/specs/2026-08-21-legacy-ai-recommendation-guard-design.md`이며
커밋은 **`c0d0417`** (`docs: design legacy AI recommendation guard`)이다.

> ⏸️ **사용자 결정으로 이 설계와 구현은 보류 상태다.** 설계 커밋은 참고용으로 유지하지만,
> 별도 재개 요청 전에는 구현 계획 작성, 코드 수정, 테스트, 운영 데이터 보정, 배포를 하지 않는다.
> 운영 서버는 직전 배포 상태인 `4b011ea` / Alembic `0026_holding_details` 그대로다.

후속 개발 후보는 우선순위순으로 ① 자동매수 유동성 하한, ② 청산 감시 정보의 다이제스트
노출, ③ 미완성 후보 점수의 순위 격리, ④ 매매기록 생성 규칙의 0점·Breadth·주간추세 설명
교정이다. 모두 미착수이며 P0 보류가 자동으로 이 항목들을 막는 것은 아니지만, 별도 요청 없이
착수하지 않는다.

현재 작업공간에는 사용자의 기존 변경인
`docs/blog_series_backtest/11_눌림목_전략_부검기.txt` 삭제와 `docs/diary/` 미추적 파일이
남아 있다. 설계·HANDOFF 커밋에 포함하지 않았다.

## 8/20 8월 19일 매매일지 후속 2 → 4 → 3 → 5 — ✅ 운영 배포 완료

사용자가 확정한 순서와 정책대로 구현하고 `4b011ea`로 master에 반영해 17:14:49 KST
운영 배포했다. 운영 HEAD는 `d8ae437` → `4b011ea`, Alembic은 `0024_app_user` →
`0026_holding_details`다. systemd `maps=active`, 내부·외부 `/health` 200,
재시작 이후 ERROR/Traceback/CRITICAL 0건을 확인했다.
6번 Donchian 변경은 범위에서 제외해 건드리지 않았다.

1. **2번 전략매매 안전장치**: 무장 요청의 장세 문자열 대신 서버의 최신 적용 국면을
   사용한다. 전략매매 진입에도 일일 손실·현금·단일종목 노출·점수 준비도 검사를 적용했다.
   분할 주문 회차가 바뀌어도 `strategy_trade:<pick_id>` 단위 Kill Switch를 사용한다.
   승인 국면과 현재 국면이 다르거나 신규진입 한도가 0인 경우에는 사용자 방침대로
   경고를 남기고 진입은 허용한다.
2. **4번 체결 감사 추적**: digest execution에 `analysis_pick_id`, AI 권고, 승인 국면,
   전략 맥락, 원래 rationale과 경고 코드를 연결했다. `strategy_trade:<pick_id>:leg:...`
   주문도 분석 픽을 복원하므로 WATCH/SELL을 사람이 승인한 사실이 매매일지에 남는다.
3. **3번 조건부 진입 분리**: `conditional_entries`를 `tomorrow_orders`와 분리했다.
   체결 회차, 전체 회차, 다음 회차·가격, 잔여 수량과 waiting/order_pending/stale/
   entries_cancelled/entries_complete 상태를 제공한다.
4. **5번 상세 보유현황**: 브로커의 상세 포지션을 `portfolio_snapshot.holding_details`에
   저장하고 digest `portfolio`에 평균단가·현재가·평가액·평가손익을 제공한다. 기존
   `holdings` 수량 맵은 호환성을 위해 유지했다. 상세값이 없는 과거 행은 추정하지 않고
   `data_complete=false`, `HOLDING_DETAILS_UNAVAILABLE`로 표시한다.

마이그레이션 체인은 `0025_pick_ai_reco` → `0026_holding_details`이고 단일 head다.
배포 전 PostgreSQL custom-format 백업
`/opt/maps/backups/pre_diary_safety_20260820_171500.dump`(326,876,444 bytes, mode 600,
`pg_restore -l` 289항목)을 만들었다. 앱 계정 권한이 없는 기존 백업 테이블
`order_log_backup_20260724`만 제외했다.

검증: `python -m pytest tests -q` **922 passed**, `python -m compileall maps -q`,
`git diff --check`, `alembic heads` = `0026_holding_details (head)`.

## 8/16 AI 권고(WATCH)가 무장·주문까지 그대로 흘러간다 — 배경 기록 (8/20 구현 완료)

최근 매매기록과 운영 DB를 대조하다 나왔다. **아래 내용은 8/16 조사 당시 기록이다.**
계획은 로컬 플랜 파일에 있고 요지는 아래에 옮겼다.

### 무엇이 문제인가

저장된 AI 매매계획 **3건이 전부 `recommendation=WATCH`(관찰)** 인데 그중 하나는 실제 체결됐다.

| pick | 종목 | AI 권고 | 상태 |
|---|---|---|---|
| 8 | 005930 삼성전자 | **WATCH** | `BOUGHT` — 8/12 249,000원 × 12주 체결 |
| 9 | 034020 두산에너빌리티 | **WATCH** | `ARMED` (8/12, 진입 없음) |
| 10 | 006800 미래에셋증권 | **WATCH** | `ARMED` (8/14, 진입 없음) |

005930 의 AI 근거문은 "명확한 추세 전환 확인 전까지 **관망 권고**" 였다.
현재 +10.2%(274,500원)라 손실은 없지만 **그건 통제가 아니라 운이다.**

**8/12 매매기록이 이 거래를 설명하지 못했다** — "삼성전자 건은 근거 문구 자체가 비어 있어
이 기록만으로는 왜 샀는지 설명할 수 없습니다" 라고만 적혀 있다. 문서가 부정직한 게 아니라
**기록할 데이터가 없었다.**

### 권고가 사라지는 지점 4곳 (코드 확인 완료)

1. `maps/ai/trade_planner.py:78` — `AITradePlan` docstring 은 "only BUY may contain
   executable prices" 라고 주장하는데, 모델은 `entries`/`target`/`stop` 을 **필수**로 받고
   validator 가 `target > e1 > e2 > e3 > stop` 을 강제한다. WATCH 도 반드시 가격을 갖는다.
   **코드에 없는 안전장치를 문서가 있다고 적고 있다.** 이게 하류를 오도했다.
2. `maps/api/stock_analysis.py:135-155` — `generate_trade_plan()` 이 권고와 무관하게
   가격을 통과시키고 `source="AI"` 를 붙인다.
3. `static/js/stock-analysis.js:588-604` — `_applyAnalysisTradePlan()` 이 `source==='AI'` 와
   진입가 3개만 보고 자동 채움. 안내문은 "그대로 불러왔습니다" 뿐이라, 화면에 뜬
   '분석 의견: 관찰' 과 상충한다는 신호가 없다.
4. `maps/ops/strategy_trade_plan.py:24-40` — `StrategyTradeLimitInput` 에 `recommendation`
   필드가 **없다.** 서버 무장 경로가 권고를 알 수 없고 `AnalysisPick` 에도 컬럼이 없어
   감사 로그에 안 남는다.

### 방침 (사용자 확인됨) — 경고 + 감사 기록, 범위는 A 만

**무장은 계속 허용한다.** 전략매매는 사람이 승인하는 경로다. 대신 화면이 상충을 명시하고
픽에 권고를 기록해 사후 추적이 가능하게 한다. 차단은 과하다고 판단했다.

- `StrategyTradeLimitInput` 에 `ai_recommendation` **선택** 필드 추가(수동 입력 경로에는
  권고가 없으므로 필수로 만들면 안 된다).
- `CalculatedTradeLimits` 에 `warnings` 추가 — **`TradePlanBlocker` 를 재사용**한다
  (`code`/`message` 구조가 그대로 맞는다). `blocked`/`blockers` 는 건드리지 않는다.
  경고는 차단이 아니다.
- `AnalysisPick.ai_recommendation` 컬럼 + 마이그레이션 **`0025_analysis_pick_ai_recommendation`**
  (현재 head `0024_app_user`). 기존 행 `NULL` = 기록 이전, backfill 없음.
- UI 는 가격을 그대로 채우되 `recommendation !== 'BUY'` 면 상충 경고를 띄우고,
  무장·미리보기 요청에 권고를 실어 보낸다.
- `AITradePlan` docstring 의 거짓 주장을 실제 동작으로 고친다.

> ⚠️ **마이그레이션 번호가 겹친다.** B(운영 설정 편집) 계획이 `0025_ops_config_log` 를
> 예약해 뒀다. B 가 미착수이므로 이쪽이 0025 를 먼저 쓰고 B 는 0026 으로 내린다.

선택 항목: `ops/daily_digest.py` 의 execution 에 `ai_recommendation` 을 실어야 블로그가
"AI 는 관찰 의견이었는데 매수했다"를 쓸 수 있다. 전략매매 주문의 `order_id` 가
`strategy_trade:<pick_id>:leg:...` 라 픽 id 를 뽑을 수 있다. 없으면 감사 기록이 DB 에만
남고 매매기록에는 여전히 안 보인다.

### 같이 나왔지만 범위 밖으로 뺀 것

- **매매기록에 보유 포지션·평가손익이 없다.** 다이제스트 최상위 키에 account/holdings 가
  없어, 7종목 2,021만원을 들고 있는데 어느 문서에도 안 나온다.
- **ARMED 픽이 매매기록에 안 나온다.** 034020·006800 이 진입 없이 대기 중이고 신선도
  5거래일로 조용히 만료된다(034020 은 **8/19~20경**). 어디에도 기록되지 않는다.
- **`portfolio_snapshot` 내부 모순** — 8/15·8/16 행이 `positions_value=20,215,460` 인데
  `holdings=NULL` 이다. 비거래일 동기화가 빈 보유목록을 쓰면서 총액은 남긴 것으로 보인다.
- **005930 은 `effective_stop_price()` 가 `None`** — `strategy_trade:8:leg` 가
  `STRATEGY_GROUP_MAP` 에 없는 ID 다. 실제 청산은 `pick.stop_price`(225,000)로
  `_process_strategy_trades` 가 관리하지만, 루트 CLAUDE.md #7 의 "청산 판정·사이징·화면
  표시가 모두 이 함수를 거쳐야 한다" 와 어긋난다.

### 문제 아닌 것 (확인 완료 — 다시 의심하지 말 것)

- **8/2 이후 매도 0건은 정상이다.** 처음엔 청산 누락을 의심했으나 보유 7종목 전부
  손절선 위에 있다. 가장 가까운 282330 도 손절가 대비 **+11.1%** 다.
  실측: 041830 +16.9%, 051900 +11.6%, 073240 +35.0%, 189330 +42.1%, 241710 +44.1%.
- 8/12~14 `market_score_incomplete` 전량 차단은 이미 규명·수정·복구됐다(아래 절).
- **매매기록 문서 자체는 정직하다.** 미측정 항목을 해석하지 않고, 8/12 기록은 삼성전자
  근거 공백을 스스로 지적해 뒀다.

## 8/16 종목분석 상세 PDF 내려받기 — ✅ 구현·배포 완료

승인받고 구현·배포했다. 전체 **915 passed**(기준 906 + 신규 9). 마이그레이션 없음.
운영 HEAD `1f3dd86` → **`d8ae437`**, 두 번 배포했다(08:56:52 기능, 10:03:24 reportlab
상한). `maps=active`, 내부·외부 `/health` 200, 재시작 이후 ERROR **0건**.

| 커밋 | 내용 |
|---|---|
| `54a48be` | 기능 — 렌더러·엔드포인트·화면·테스트 + blog 커맨드의 diary 출력 |
| `e44cd72` | `reportlab<6` 상한 + 로컬을 운영 버전(5.0.0)에 맞춤 |
| `d8ae437` | HANDOFF 해시 정정 |

**운영 실측**: 실제 이력 `id=3`(006800 미래에셋증권)을 서버에서 렌더링해
149,317 bytes / `%PDF-` / `FontFile2` 임베드를 확인했다. 비인증 요청은 401.

### ⚠️ `reportlab` 은 requirements 변경이라 배포에 `pip install` 이 필요했다

처음엔 `>=4.1.0` 이라 로컬 4.5.1 / 운영 5.0.0 으로 **메이저가 갈렸다.** 둘 다 정상
동작했지만 테스트한 버전과 운영 버전이 다른 상태였다. `<6` 상한을 넣고 **로컬도 5.0.0
으로 올려 다시 전체 통과**시켜 맞췄다. 상한은 다음 배포가 예고 없이 6.0 을 끌어오는 것을
막을 뿐이고, 버전 일치 자체를 보장하지는 않는다.

### 렌더러가 WeasyPrint 가 아니다 — 설계 2번만 교체했다

설계서가 표시해 둔 "미확인 위험"이 실제로 터졌다. 다만 터진 쪽이 서버가 아니라
**개발 PC** 였다.

| 후보 | 결과 |
|---|---|
| WeasyPrint | `pip install` 은 되지만 **Windows 에서 import 불가** — `libgobject-2.0-0` (GTK) 없음. 서버(리눅스)에서는 됐을 것이나 **로컬 테스트가 불가능**해진다 |
| xhtml2pdf | 순수 파이썬이라 양 OS 설치는 되는데, `@font-face` 가 폰트를 임시파일로 복사한 뒤 다시 여는 구현이라 Windows 에서 `PermissionError` 로 깨진다 |
| **reportlab** ✅ | 순수 파이썬, 두 OS 동일. 한글 TTF 를 직접 열어 **PDF 에 임베드**(`FontFile2` 확인) |

설계서가 "실패하면 렌더러만 갈아끼우면 되고 나머지는 유효하다"고 적어 둔 그대로,
엔드포인트·담는 내용·화면·테스트는 설계안 그대로 갔다. Jinja2 템플릿은 없어졌고
차트는 인라인 SVG 대신 `reportlab.graphics` 폴리라인으로 그린다.

> ⚠️ **HTML→PDF 엔진을 다시 검토하지 말 것.** 위 표가 그 결론이다. 이 저장소는
> 개발이 Windows, 운영이 리눅스라 **양쪽에서 도는 렌더러만 후보**다.

### 만든 것

| 파일 | 내용 |
|---|---|
| `maps/stock_analysis/pdf.py` | `render_history_pdf(row) -> bytes` (신규) |
| `maps/api/stock_analysis.py` | `GET /history/{id}/pdf` — `_may_read` 재사용, 폰트 없으면 503 |
| `templates/_stock_analysis_panel.html` | `#sa-download-pdf` 앵커 |
| `static/js/stock-analysis.js` | `_setPdfLink()` — 이력 상세를 열 때만 노출 |
| `tests/test_stock_analysis_pdf.py` | 8건 (신규) |
| `tests/test_users.py` | 소유권 404/200 경계 1건 추가 |

권한은 손대지 않았다 — `_USER_ALLOWED` 가 이미 `/api/v1/stock-analysis` 의 GET 을
열어 두고 있고, 행 단위 소유권은 `_may_read` 가 본다.

### 설계보다 나아진 것 두 가지

- **폰트 폴백에 pykrx 동봉 `NanumBarunGothic.ttf` 를 넣었다.** pykrx 는 필수 의존성이라
  시스템 한글 폰트가 하나도 없는 상자에서도 렌더링된다. 시스템 폰트를 못 찾았을 때만
  평가해서 불필요한 pykrx import 를 피한다.
- **폰트를 못 찾으면 `FontUnavailableError` 로 실패한다.** 폰트 없이 만들면 한글이 전부
  빈 네모로 나가는데, 그건 성공처럼 보이는 실패다.

### 불변 경계를 테스트로 못박았다

렌더러는 결정적(`SimpleDocTemplate(invariant=1)`)이다. `latest_price` 를 바꾸고 다시
렌더링해 **바이트가 동일한지** 보는 테스트가 있다 — 오버레이가 본문에 새어 들어가면
이 테스트가 깨진다.

### 남은 것

1. 브라우저에서 실제로 한 건 내려받아 한글이 보이는지 눈으로 확인(서버 렌더링은 확인했다).
2. 이력 목록 행에도 PDF 버튼을 붙일지는 미정 — 설계대로 상세에만 뒀다.
3. 별건: `systemctl status maps` 가 `unit file ... changed on disk, run daemon-reload`
   경고를 낸다. **이번 작업이 만든 게 아니다**(유닛 파일을 건드리지 않았다). 누군가
   `/etc/systemd/system/maps.service` 나 `security.conf` 드롭인을 고치고 `daemon-reload`
   를 안 한 상태가 남아 있다. 다음 재시작 때도 계속 뜬다.

## 8/16 매매기록을 운영 서버 diary 에도 남긴다

`/blog` 로 매매기록을 쓰면 이제 **두 곳**에 남긴다. 둘 다 끝나야 작성이 끝난 것이다.

| 위치 | 파일명 | 비고 |
|---|---|---|
| 로컬 | `blog/<ref_date>.txt` | 기존 그대로 (`2026-08-16.txt`) |
| 운영 서버 | `/opt/maps/diary/<YYYYMMDD>.txt` | 신규 (`20260816.txt`) |

> ⚠️ **두 경로의 날짜 표기가 다르다.** 로컬은 하이픈이 있고 서버 diary 는 8자리다.
> 커맨드 문서(`.claude/commands/blog.md` 출력 절)에 경고를 넣어 뒀다.

- `/opt/maps/diary` 는 **없어서 만들었다**(`mkdir -p`, `ubuntu:ubuntu`).
- 업로드는 `scp` 로 로컬 파일을 그대로 올린다 — 원고를 두 번 만들지 않는다.
- SSH 키 경로가 PC마다 다르다(집 `D:\maps\`, 회사 `D:\ssh_maps\`). 커맨드 문서에 적어 뒀다.
- 같은 날짜 파일이 있으면 **덮어쓴다.** 다시 쓰는 게 맞는지 먼저 확인할 것.

기존 `/opt/maps/blog/` 에 8/10~8/14 원고 5건이 있다. **diary 로 소급 복사하지 않았다** —
요청 범위 밖이다. 필요하면 파일명만 8자리로 바꿔 복사하면 된다.

## 8/14 미완 — 전략 가이드 09 를 블로그 글로 다시 쓰기

`docs/strategy_guides/` 08~09 를 네이버 스타일 블로그로 만들어 달라는 요청이 있었고,
조사 결과 **실제 공백은 09 하나뿐**이다. 사용자가 "이미 만들어 놓은 것을 확인했다" 며
중단해서 착수하지 않았다.

- **08(`08_contrarian_quality_v1.txt`)은 이미 완성된 블로그 글이다.** 10,226 bytes,
  코스톨라니의 개 비유·진입조건 6개·분할매수·초보자 체크포인트·면책 문구·태그까지 있다.
- **09(`09_pullback_v3_3.txt`)만 성격이 다르다.** 3,974 bytes 로 다른 가이드(6~10KB)의
  절반 이하고, 제목줄과 "안녕하세요 :)" 뒤로는 사실상 기술 문서다. 비유·초보자 설명이
  없고, **면책 문구와 태그가 없다** — 00~08 은 전부 있는데 09 만 본문으로 끝난다.
- 두 파일 다 `scripts/check_naver_format.py` 는 통과한다. 그 검사기는 붙여넣기 안전성과
  AI 표기(이모지·em dash·상투구)만 보고 **구성·분량은 검사하지 않는다.**
- 다시 쓴다면: `pullback_v3_3` 은 HANDOFF 기록상 **2017·2023 샤프 기준에서 탈락해
  research 격리** 상태다. 글에는 "검증 기준을 통과해야 채택" 이라고만 있고 실제 탈락
  결과가 없으니 그 실측을 넣는 게 맞다.
- 문체 규약 정본은 `docs/blog_style_naver.md`, 본보기는 `docs/blog_series_backtest/*.txt`.

## 8/14 🔴 시장 점수 전면 차단 원인 규명·수정·배포 완료

**8/12~14 사흘간 자동 신규 매수가 전량 막혀 있었다.** 8/13 다이제스트는 후보 10건을
모두 `market_score_incomplete` 로 제외했다. 운영 HEAD `2ef593f` → **`1f3dd86`**,
14:27:31 KST 기동, `maps=active`, `/health` 200, 재시작 이후 ERROR **0건**.
마이그레이션 없음. 배포 전 전체 **906 passed**, `maps/tests` 81 passed.

### 원인 — 네이버 API 가 아니다

먼저 의심했던 네이버 뉴스 API 는 **정상이다.** 운영 서버에서 실제 자격증명으로 API Hub 를
직접 호출해 `http_code=200` 과 기사 목록을 확인했고, `market_news_sentiment` 도 8/13
`status=success, score=88.0` 이었다.

진짜 원인은 `maps/market/feeds.py:113` 의 수급 NULL 가드였다.

```python
if any(any(getattr(row, field) is None for field in fields) for row in rows):
    return None     # 한 행이라도 NULL 이면 그날 수급 전체를 버린다
```

`investor_flow_snapshot` 의 NULL 은 **수집 실패가 아니다.** `krx_adapter.get_investor_flows`
가 투자자 유형별 pykrx 프레임을 `values.setdefault(ticker, {})[target]` 로 병합하므로,
어떤 유형의 프레임에 없는 티커는 그 컬럼이 빠진 채 저장된다(우선주·저유동성 종목에 흔하다).

**운영 실측 8/13: 2,622행 중 외국인 NULL 54행, 기관 NULL 538행(20.5%).** 이 가드는 매일
반드시 발동했다. 바로 아래 집계는 이미 `or 0.0` 으로 NULL 을 허용하고 있어 **가드와 서로
모순**이었다.

결과: `liquidity`(0.25) + `psychology`(0.10, 수급을 함께 요구)가 동반 미측정 →
coverage 0.65 고정 → `score_readiness` 가 모든 자동 신규 BUY 와 전략 승격을 차단.
**게이트는 설계대로 동작했다** — SELL·손절·익절이 안 막힌 것도 코드로 확인했다.
버그는 게이트가 아니라 입력이었다.

### 고친 것

| 커밋 | 내용 |
|---|---|
| `5e055fd` | 가드를 "**전 행에서** 결측인 필드가 있을 때만 차단"으로. 날짜 행 0건(수집 실패)은 기존대로 fail-closed. 판정마다 필드별 non-NULL 건수를 로그로 남긴다. `ops/scheduler.py:_build_ticker_contexts` 의 같은 결함(조용히 ~20% 종목을 `supply_demand_score` 에서 누락)도 의미론을 통일 |
| `1f3dd86` | 관측성 2건 + 재계산 스크립트 |

**관측성이 이번 사고의 진짜 교훈이다.** 이틀 넘게 안 드러난 이유가 두 곳의 조용한 실패다.

- `_order_candidates` 가 준비도 미달 후보를 **로그 없이** 버려서, 10건이 막혀도 `order_cycle`
  잡 결과는 `"skipped_buy_orders": 0` 이었다. 이제 후보마다 WARNING + 사유별 집계를
  `blocked_by_readiness` 로 잡 결과에 싣는다. 필터 위치는 그대로 뒀다 — 아래로 옮기면
  준비 안 된 행이 같은 티커의 준비된 행 자리를 뺏는다.
- `collect_daily` 가 수급 예외를 삼킨 뒤에도 `data_collection` 잡이 `success` 로 끝났다.
  OHLCV 는 계속 살리되 0건이면 `collection_log.status='partial'` + `logger.error` +
  잡 details 의 `investor_flow_count` 로 드러낸다.

### 과거 행 복구 (완료)

`scripts/backfill_market_score.py --start 2026-08-12 --end 2026-08-14 --apply` 실행.
백업: `/opt/maps/backups/market_regime_log_pre_recompute_20260814.json` (3행 전체 컬럼).

- 8/12·8/13 → `coverage 0.65 → 1.0`, `partial → complete`, `ready false → true`
  (liquidity 66.8, psychology 56.4)
- 8/14 → `market_observations_unavailable` 로 **건너뜀**. 오늘 OHLCV 수집(16:40) 전이라
  재계산을 거부한 것이고 의도한 동작이다.
- 결정 기록(`applied_regime`·`entry_limit_ratio`·`market_mode`·`source`)은 그대로다.
  `score_reason` 에 `decision-time coverage=0.65` 를 남겨 재생성된 다이제스트가 스스로
  밝히게 했다.

> ⚠️ **복구한 날짜의 다이제스트·블로그는 재생성하지 않는다.** 재생성하면 결정 시점이
> 아니라 복구 후 값을 설명하게 된다.

### 🔴 아직 화면은 안 풀렸다 — 오늘 16:50 이후에 풀린다

`market_score_ready(2026-08-13)` 는 이제 `(True, None)` 이지만, **후보 행에 찍힌
`candidate.market_score_ready` 는 생성 시점 스탬프라 여전히 False** 다. 그래서
`candidate_score_ready` 는 아직 `market_score_incomplete` 를 돌려준다.

```
후보 005930: candidate.market_score_ready=False, score_ready=True, coverage=1.0
candidate_score_ready -> (False, 'market_score_incomplete')
```

**8/13 후보를 소급 수정하지 않았다.** 오늘 16:50 `candidate_generation` 이 8/14 후보를
새로 만들면서 `market_score_ready=True` 로 찍고, 주문 경로는 최신 스냅샷만 쓰기 때문에
자연히 대체된다. 결정 시점 스탬프를 덧쓰는 것보다 낫다고 판단했다.

**다음 거래일은 2026-08-18(화)** 다 — 8/15 광복절이 토요일이라 8/17 이 대체공휴일이다.

### ✅ 실효 확인 완료 (8/14 16:56~17:00 KST)

1. **`market_regime_log` 8/14 = `coverage 1.0 / complete / ready=true`**, `source` 가
   `order_cycle` → `candidate_generation` 으로 갱신됐고 `measured_factors` 에 5개가 전부 들어왔다.
   `current_market_score_ready(2026-08-18)` = `(True, None)` — 다음 거래일 주문 게이트 통과.
2. 신규 로그가 **원인을 재확인해 줬다**:
   `Investor flow coverage [2026-08-14]: rows=2630 {'foreign_net_value': 2565,
   'institutional_net_value': 2073, 'individual_net_value': 2630}`
   → 오늘도 기관 NULL 이 **557건(21.2%)** 이다. 옛 가드였으면 **오늘도 차단**됐다.
3. `data_collection` details 에 `"investor_flow_count": 2630, "investor_flow_error": null`.
4. 8/14 후보는 **전 전략 `market_score_ready=true`** (ath_breakout_v1 77/77, donchian_v2 93/93 등).
5. **주문 후보가 0건 → 122건**이 됐다. `blocked_by_readiness` 는
   `{'candidate_score_incomplete': 3}` 뿐이고, 그 3건도 **사유와 건수가 찍힌다**(관측성 실효).
6. 주문예정 화면에서 **`market_score_incomplete` 가 완전히 사라졌다.** 남은 사유는
   `preferred_regime_mismatch:mixed` 9건과 `no_entry_signal` 1건이다.

> ⚠️ **8/18 에 주문이 나갈지는 별개 문제다.** 오늘 장세가 `strong` → **`mixed`** 로 바뀌면서
> `entry_limit_ratio` 가 0.5 → 0.25 가 됐고, 돌파 전략(`ath_breakout_v1/v2`)은 `strong` 선호라
> `preferred_regime_mismatch:mixed` 로 막힌다. 주문 가능 단계는 `ath_breakout_v1` 과
> `donchian_v2` 둘뿐인데(나머지는 `research`), donchian 쪽 상위 후보는 `no_entry_signal` 이었다.
> **이건 점수 결함이 아니라 설계된 장세 게이트다.** 8/18 08:55 에 장세를 다시 분석하므로
> 그때 라벨에 따라 달라진다.

### 별건으로 남긴 것 — 역발상 전략은 구조적으로 승격 불가

`maps/strategy/score_features.py` 에 `CONTRARIAN_QUALITY` 분기가 없어, 5개 컴포넌트 중 4개
(`earnings_revision`·`crowd_neglect`·`accumulation_flow`·`technical_bottom`)를 **생산하는
코드가 저장소에 0곳**이다. coverage 가 0.30 에 영구 고정된다(운영 실측: 8/13 후보 94건 전부 0.3).

부작용으로 `scoring._measured_score()` 가 측정분으로 재정규화하므로 **밸류에이션 1개만
100점이면 `final_score=100.0`** 이 되어 5개 만점 후보와 구분되지 않은 채 후보 정렬 1등으로
올라온다. 8/13 다이제스트 상위 12건이 전부 이 전략이었던 이유다. 이번 수정으로도 안 풀린다.

참고로 이번 수정 후에도 주문 가능 전략은 준비돼 있다 — 8/13 실측 `ath_breakout_v1` 72건 중
71건, `donchian_v2` 79건 전부가 후보 단계 `score_ready=true` 였다.

#### 4개 컴포넌트 구현 사전조사 (8/14, 착수 전)

공수를 가르는 건 코드가 아니라 **데이터 가용성**이고 4개가 서로 많이 다르다.

| 컴포넌트 | 가중치 | 데이터 | 공수 |
|---|---|---|---|
| `technical_bottom_score` | 0.10 | OHLCV만 — 전략이 이미 52주 고점 대비 하락·RSI(14)·MA20/60 을 계산한다 | 작음 |
| `crowd_neglect_score` | 0.20 | OHLCV만 — 거래대금 고갈도 | 작음 |
| `accumulation_flow_score` | 0.15 | `investor_flow_snapshot`. 배선은 이미 있다(`supply_demand_score` 와 같은 경로) | 중간 |
| `earnings_revision_score` | **0.25** | **없다** — 아래 참조 | 결정 필요 |

앞의 둘은 기존 `PULLBACK`/`BREAKOUT` 분기와 같은 모양으로 `score_features.py` 에
`CONTRARIAN_QUALITY` 분기 하나 추가하면 된다(합쳐 40~60줄 수준).

**제약 1 — 수급 이력이 10일뿐이다.** `investor_flow_snapshot` 은 8/3~8/14 10거래일이다.
매집 플로우는 통상 20~60일을 본다. 짧은 창(5~10일)이면 오늘 당장 되고, 제대로 하려면
`backfill_score_feeds.py` 로 백필해야 한다 — pykrx 는 하루당 6회 호출이라 60일이면 ~360회.
**코드가 아니라 운영 작업**이고 KRX 로그인 가드 때문에 나눠 돌려야 한다.
> WFA·Plateau·MC 는 OHLCV 만 쓰므로 이 이력 부족이 **승격 검증을 막지는 않는다.**
> 후보 점수 창에만 영향이 있다.

**제약 2 — `earnings_revision` 은 정의부터 정해야 한다.** 통상 이 지표는 애널리스트
**컨센서스 전망치 리비전**인데 MAPS 에 컨센서스 데이터가 없다. `security_fundamental` 은
pykrx 의 **후행** PER/PBR/EPS/BPS 뿐이다.

다만 그 테이블에 **5,677,694행 / 2,982종목 / 2016-01-04~2026-08-14 (2,604거래일)** 이
쌓여 있다(8/14 실측). pykrx EPS 는 후행 12개월이라 실적 발표마다 계단식으로 바뀌므로
**후행 EPS 변화율**은 오늘 당장 계산 가능하다. 단 그건 "컨센서스 리비전"이 아니라
"실적 개선 방향"이다. **가중치 0.25 로 가장 큰 항목이라 조용히 바꿔치기하면 안 된다.**

**견적 (사용자 결정 대기)**

- **시나리오 A — 후행 EPS 대용 + 기존 수급 창 재사용**: 집중 세션 한 번(반나절 수준).
  구조 작업은 `strategy_extra_scores()` 시그니처를 넓혀 EPS 이력 점수를 주입하는 것 하나다
  (`supply_demand_score` 와 같은 모양. `FundamentalRepository.historical_avg` 가 이미 있다).
- **시나리오 B — 실제 컨센서스 소스 연결**: 훨씬 크다. 소스 조사(FnGuide·Naver 증권·DART),
  어댑터 신규 작성, 수집 잡, 스키마·마이그레이션, 이용약관 확인. 외부 의존이라 불확실성도 크다.

부수 효과: 5개가 다 측정되면 **`final_score=100` 랭킹 왜곡이 같이 해소된다.**
다만 구현해도 즉시 주문으로 이어지진 않는다 — `research` 단계이고 `preferred_regimes` 가
`weak`/`mixed` 라 승격 게이트(점수 60 + Sharpe ≥ 0)를 따로 통과해야 한다.

## 8/14 A(개인 후보 필터) 최종 리뷰·병합·운영 배포 완료

운영 HEAD `eac342c` → **`2ef593f`**. alembic 은 **`0024_app_user (head)` 그대로**다
(A 는 마이그레이션이 없다). 13:09 KST 기동, `maps=active`, 내부·외부 `/health` 200,
`/login` 200, 재시작 이후 ERROR/Traceback **0건**. 배포 전 전체 **891 passed**.

### 전체 브랜치 리뷰에서 7건이 나왔고 전부 고쳤다 (`fc5275a`)

**실제 위험은 앞의 두 건이었다. 둘 다 병합 직전에 잡혔다.**

1. 🔴 **`resolve()` 가 기존 사용자 설정을 통째로 날렸다.** `UserPreferences` 가
   `extra=forbid` 라, 알림 3키를 지우면서 **그 전에 `/settings` 를 저장한 계정의
   JSON 이 검증에 실패**하고 `resolve()` 의 폴백이 전부 기본값으로 되돌렸다 —
   `landing_screen` 까지. 마이그레이션이 없어 배포 즉시 영향이 갔을 건이다.
   실측: `{'landing_screen':'candidates','candidate_min_score':70.0,...,'notify_push':True}`
   → `landing_screen='stock-analysis' candidate_min_score=None`.
   **고친 방식**: `resolve()` 가 검증 **전에** 모르는 키를 버린다. 읽기만 관대하고
   `PUT /users/me/preferences` 의 `extra=forbid` 422 계약은 그대로 뒀다 —
   클라이언트 오타가 조용히 무시되면 안 된다(기존 테스트가 그 계약을 지킨다).
2. 🔴 **후보 화면이 `/login` 으로 튀었다.** `apiFetch` 는 401 에서
   `_redirectToLogin()` 을 **throw 보다 먼저** 호출해 `try/catch` 로 막히지 않는다.
   인증이 꺼진 배포(기본값)에서 `/users/me` 는 401 이라 — `_require_self` 가
   `username='local'` 계정을 못 찾는다, 의도된 동작 — 후보 화면을 열 때마다
   페이지 전체가 이동했다. 운영은 인증 ON + 실계정이라 무영향.
   **고친 방식**: `apiFetchQuiet()` (실패 시 `null`, 리다이렉트 없음) 추가.
3. 🟡 필터로 후보가 **전량** 걸러지면 배지가 안 떴다(빈 목록 early return 이 배지
   생성보다 앞). 이제 배지를 먼저 만들고 빈 상태에도 원인·해제 링크를 보여 준다.
4. 🟡 개인 최소 점수가 원시 `final_score` 로 걸러 **주문 게이트와 다른 점수**를 봤다.
   정본은 `candidate_min_score_expression()` = rerank 모드에서
   `coalesce(rule_score, final_score)` 이고 **운영 AI 모드가 `rerank`** 다.
   화면이 보여주지도 않는 컬럼으로 거르던 문제도 같이 해소됐다.
5. 🟢 `candidate_markets` 검증이 없어 `"kospi"` 가 그대로 `market.in_()` 에 들어가
   목록이 영구히 비었다. `Literal["KOSPI","KOSDAQ"]` + `candidate_min_score ge=0`.
6. 🟢 `user_prefs` 의 죽은 `settings = settings or get_settings()` 제거.
7. 🟢 문서 드리프트 — 삭제된 전역 폴백을 여전히 설명하던 4곳(모듈·`resolve()`
   docstring, `maps/common/CLAUDE.md`, 화면설계서 기본값 행)과 남아 있던 "6키" 서술.

회귀 테스트 **8건** 추가, 전부 수정 전 RED 확인. 전체 **891 passed**(기준 883 + 8),
`maps/tests` 81 passed.

### 여기서 배운 것 (반복 방지)

- **`extra=forbid` 스키마에서 키를 지우는 것은 파괴적 변경이다.** 저장된 JSON 을
  읽는 경로가 검증 실패를 "전부 기본값"으로 처리하면, 지운 키 하나가 **남은 설정
  전부**를 날린다. 스키마에서 키를 뺄 때는 (a) 마이그레이션으로 저장값을 정리하거나
  (b) 읽기 경로에서 모르는 키를 버리도록 해야 한다. 쓰기 경로의 엄격함은 유지한다.
- **`try/catch` 가 부수효과를 막아 주지 않는다.** `apiFetch` 처럼 throw 전에
  리다이렉트하는 헬퍼는 호출부에서 감싸도 소용없다. "실패해도 무시" 가 필요한
  부가 조회에는 **전용 헬퍼**를 쓴다.
- **화면 필터와 주문 게이트가 같은 뜻이면 같은 표현식을 써야 한다.** 값이 아니라
  **함수**를 공유해야 모드가 바뀔 때 같이 따라간다.

### 남은 것

- **B(운영 설정 편집)는 여전히 미착수다.** 아래 절 참고. migration `0025` 포함.
- A 의 로컬 worktree `.claude/worktrees/personal-candidate-filter` 는 병합 후에도
  남아 있다. 정리해도 되고, B 를 거기서 이어가도 된다.
- 운영 계정 중 8/13~8/14 사이에 `/settings` 를 저장한 계정이 있었는지는 **미확인**이다
  (DB 조회가 권한 정책에 막혔다). 1번 수정으로 앞으로는 설정이 살아나지만, 그 사이에
  이미 초기화된 값이 있다면 되살아나지 않는다 — 사용자에게 `/settings` 재확인을
  권하는 편이 안전하다.

## 8/13 개인화 2차 — A 설계·구현 경위 (병합 완료, 기록용)

A 는 위 절대로 병합·배포됐다. 아래는 구현 당시의 경위 기록이다.
B 는 여전히 스펙·계획만 있고 미착수다.

### 무엇을 왜 하는가

8/12 개인화 1차에서 `/settings` 에 개인 설정 6개를 만들었는데 **실제로 동작하는 것은
`landing_screen` 하나뿐**이었다. 나머지 5개는 저장·조회만 되고 읽는 곳이 없었다.
`/ops-config` 는 59개 항목을 보여주면서 바꿀 수 있는 건 `MAPS_AI_SCORING_MODE` 하나뿐이라
나머지는 SSH 로 `.env` 를 고쳐야 했다. 이 두 가지를 각각 A·B 로 나눴다.

| | 내용 | 상태 |
|---|---|---|
| **A** | 후보 필터 2개 연결 + 알림 3개 삭제 | ✅ **병합·배포 완료(8/14)** — 맨 위 절 |
| **B** | OPS-02/03 운영 설정 편집·변경 이력 | 스펙·계획만, **미착수** |

### 문서 (master 에 있음)

- 스펙: `docs/superpowers/specs/2026-08-13-personal-candidate-filter-design.md`,
  `docs/superpowers/specs/2026-08-13-ops-config-editing-design.md` (커밋 `7c146cc`)
- 계획: `docs/superpowers/plans/2026-08-13-personal-candidate-filter.md`,
  `docs/superpowers/plans/2026-08-13-ops-config-editing.md` (커밋 `ef5320e`)

### A 브랜치 — `worktree-personal-candidate-filter`

`origin` 에 push 완료. `ef5320e` 위 6커밋이고 전체 스위트 **883 passed**(기준 875 + 신규 8).

| 커밋 | 내용 |
|---|---|
| `56217da` | 계획 외 선행 작업 — `tests/test_strategy_catalog.py` 격리 결함 수정 |
| `2483b91` | `notify_push`/`notify_telegram`/`telegram_chat_id` 삭제 |
| `f7934d3` | `GET /api/v1/candidates` 에 개인 필터 적용 |
| `6ff1b18` | 위 리뷰 지적 2건 수정 |
| `a6e6a2b` | 후보 화면 필터 배지 |
| `0e8251a` | 배지의 `candidate_markets` 이스케이프 |

로컬 worktree 는 `.claude/worktrees/personal-candidate-filter` 에 남겨 뒀다(이 PC 한정).
다른 PC 에서는 `git fetch && git checkout worktree-personal-candidate-filter` 로 받으면 된다.

### ✅ A 에서 남았던 일 — 전부 종결(8/14)

1. ~~전체 브랜치 최종 리뷰~~ → 실행. 7건 발견, 전부 수정(`fc5275a`). 맨 위 절 참고.
2. ~~이월 Minor 3건~~ → 3건 다 처리했고, 리뷰가 같은 성격의 문서 드리프트를
   2곳 더 찾아 함께 고쳤다(`maps/common/CLAUDE.md`, 화면설계서 "6키" 잔존).
3. ~~병합·배포~~ → `2ef593f` 로 병합, 8/14 13:09 KST 운영 배포 완료.

### A 에서 배운 것 (반복 방지)

- **`user_prefs.resolve()` 가 `candidate_min_score` 를 전역 `maps_candidate_min_score`
  로 채우고 있었다.** 그대로 필터에 쓰면 설정한 적 없는 사용자에게도 필터가 걸리고,
  **화면 필터와 주문 게이트가 한 값으로 묶인다.** 그 채움을 삭제했다.
  `ops/order_preview.py`·`ops/scheduler.py` 의 전역값 사용은 주문 게이트이므로 그대로 둔다.
- **계획에 넣었던 테스트 하나가 회귀 방어가 안 되는 설계였다.**
  `test_filter_runs_before_limit` 이 정렬 키(`final_score`)와 **같은 컬럼**에 임계값을
  걸어서, 필터를 `.limit(200)` 앞에 두든 뒤에 두든 결과가 같았다(단조 임계값이라 수학적 동치).
  리뷰어가 실제로 필터를 뒤로 옮겨 5개 테스트가 전부 통과하는 것을 확인해 잡아냈다.
  정렬과 무관한 컬럼(`candidate_markets`)으로 다시 썼다.
  → **정렬 키에 거는 필터로는 limit 순서를 검증할 수 없다.**
- `universe_count` 는 `quality` 로그가 없을 때 `len(rows)` 로 폴백하고 있었다. `rows` 가
  필터링되면서 파이프라인 총계가 아니라 필터 결과 개수가 나갔다. `final_count`(미필터
  카운트 쿼리)로 바꿨다.
- `tests/test_strategy_catalog.py` 의 `client` 픽스처가 `get_db` 오버라이드 없이 실제
  `maps.db` 를 쳤다. **`maps.db` 가 없는 새 클론의 첫 실행에서만 실패하고 그 뒤로는
  파일이 생겨 스스로 숨는다.** 오늘 새 worktree 에서 처음 드러났다.

### B — 미착수

계획은 7개 작업이고 migration `0025_ops_config_log` 를 포함한다. 핵심 설계:

- 편집 메타데이터(타입·선택지·범위)를 **`MapsSettings.model_fields` 에서 파생**한다.
  59행짜리 표를 손으로 만들지 않는다. `Literal` → 선택지, `Ge/Le` → 범위,
  `secret` 은 `_field(..., secret=True)` 에 **이미 선언돼 있고 지금은 버려지는** 것을 싣는다.
- 허용목록은 `get_config_status()` 자체다. 거기 없는 `env_var` 는 400.
- 반영은 `.env` 쓰기 + `lru_cache` 설정 객체 갱신(`set_ai_scoring_mode` 가 쓰는 방식).
  `CronTrigger` 에 구워지는 **9개만** 재시작 필요 배지.
- `POST /ai-scoring-mode` 는 삭제한다 — 남기면 `.env` 쓰기 경로가 둘이 되고 그중 하나만
  감사 로그를 남기는 구멍이 생긴다.
- **탐색 중 발견**: 파이프라인 스케줄 시각 5종(`MAPS_DATA_COLLECTION_TIME` 등)이
  `get_config_status()` 에서 누락돼 있었다. 추가해 59 → **64항목**이 된다.
  `test_config_sections_and_counts_are_documented` 가 **섹션별 개수까지** 대조하므로
  화면설계서 표의 runtime 21 → 26, 합계 59 → 64 도 함께 고쳐야 한다.
- 위험 항목은 설계서의 5개에 **`MAPS_DB_URL` 을 더해 6개**다. 잘못 넣으면 다음 기동에서
  앱이 안 뜨고 화면으로 복구할 수 없다.

배포 시 **백업 + `alembic upgrade head` 필수**, 16:00~16:45 KST 금지.

### 다음 세션 재개 지침

1. A 를 마무리할 거면: `git fetch && git checkout worktree-personal-candidate-filter` →
   전체 브랜치 리뷰 → Minor 3건 판단 → master 병합 → 배포(마이그레이션 없음).
2. B 를 시작할 거면: `docs/superpowers/plans/2026-08-13-ops-config-editing.md` 의 Task 1부터.
   **A 와 독립이므로 A 병합을 기다릴 필요 없다.** 단 둘 다 `maps/api/schemas.py` 를 만지므로
   병합 시 충돌 가능성이 있다.
3. ⚠️ **worktree 를 새로 만들 때 주의**: `worktree.baseRef` 기본값이 `fresh` 라
   `origin/master` 기준으로 생성된다. 로컬 master 가 앞서 있으면 스펙·계획 파일이 없는
   상태로 시작되므로 `git merge master` 를 먼저 해야 한다.
4. SDD ledger 는 `.superpowers/` 아래에 있고 **gitignore 대상이라 전달되지 않는다.**
   위 "이월 Minor 3건"과 "배운 것"이 그 내용을 옮긴 것이다.

## 8/12 개인화 1차 — 계정·권한·개인설정 (8/13 09:35 KST 운영 배포 완료)

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
### 8/13 운영 배포·검증 (완료)

- 운영 HEAD `3fcfae6` → **`eac342c`**, alembic `0023_score_readiness_feeds` →
  **`0024_app_user (head)`**. 09:35:58 KST 기동, systemd `maps=active`, 내·외부 `/health` 200,
  재시작 이후 ERROR/Traceback **0건**. 배포 전 전체 **875 passed**(경고 13건).
- 배포 전 custom-format 전체 백업
  `/opt/maps/backups/pre_personalization_20260813_093403.dump`
  (324,959,550 bytes, mode 600, `pg_restore -l` 278항목). 앱 계정 권한이 없는
  `order_log_backup_20260724` 만 제외했다(0023 배포 때와 같은 이유).
- **순서가 중요하다** — `git pull` → `alembic upgrade head` → `systemctl restart`. 재시작을
  마지막에 두면 구 코드가 도는 동안 `create_all()` 이 `app_user` 를 먼저 만들지 않는다.
- 부트스트랩 관리자 시드 확인(로그 원문):
  `09:36:01 INFO [maps.api.auth] 부트스트랩 관리자 계정 생성: admin`.
  `app_user` 1행 = `admin`/운영자/`role=admin`/`status=active`/`plan=free`/한도 `None`,
  해시 접두사 `scrypt`. 기존 `.env` 비밀번호가 그대로 이관됐다.
- 소유권 경계 실측 — backfill 없이 기존 데이터 전부 `NULL`(=운영자):
  `analysis_pick` 5/5, `stock_analysis_history` 2/2.
- 비로그인 게이트 스모크: `/`·`/settings`·`/admin/users` → 303(로그인), `/api/v1/users/me` → 401
  (API는 리다이렉트가 아니라 상태코드), `/login` → 200.

### 8/13 유료 테스트 계정 (운영에 생성됨)

- `testpaid` (id=2, 표시이름 "유료 테스트", `role=user`, `status=active`, **`plan=paid`**,
  한도 미설정 → 기본 10회/일). 권한 경계 테스트용이다.
- 운영 실측: 허용 200 = `/stock-analysis`·`/candidates`·`/market`·`/analysis-picks`·
  `/settings`·`/api/v1/users/me`. 차단 403 = `/admin/users`·`/orders`·`/risk`·`/strategies`·
  `/api/v1/users`·`/api/v1/orders`. `POST /api/v1/analysis-picks/1/arm-plan` 403,
  `POST /api/v1/mobile/login` 403. 첫 진입은 `/` → 303 → `/stock-analysis` 1홉(루프 없음).
- ⚠️ 초기 비밀번호가 생성 세션 대화에 평문으로 남아 있다. **재발급하거나 `/settings` 에서
  변경할 것.** 테스트가 끝나면 `status=disabled` 로 내리는 편이 낫다.

### ⚠️ 발견된 구멍 — 개인 설정 2개가 저장만 되고 적용되지 않는다

- `UserPreferences.candidate_min_score` 는 **소비처가 `static/js/settings.js` 뿐이다.**
  후보 필터링은 여전히 전역 `settings.maps_candidate_min_score` 를 쓴다
  (`ops/order_preview.py`, `ops/scheduler.py`). `maps/api/candidates.py` 는 개인화 커밋에서
  수정되지 않았다. 위 절이 이 설정을 "후보 표시 필터" 로 적어 둔 것은 **과장**이다.
- `notify_push` / `notify_telegram` 도 같은 상태다(발송은 여전히 전역 대상).
- 즉 `/settings` 에 **동작하지 않는 스위치가 3개** 있다. 2차에서 연결하거나 화면에서 빼야
  한다 — 사용자가 켜 놓고 안 먹는 상태가 제일 나쁘다.
- 참고: `plan` 값은 권한 판정에 **전혀 쓰이지 않는다**(`_PLANS` 검증과 응답 echo 뿐).
  권한은 오직 `role` 기준이라 `paid` 계정과 `free` 계정의 동작은 지금 완전히 같다.
  PAY-01~04 가 서버 미구현이기 때문이다.

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

> 갱신일: 2026-08-16 KST · 작성자: 세션 에이전트 (**현재 PC, 키 `D:\maps\`**)
> 운영 서버 HEAD = **`d8ae437`**, alembic **`0024_app_user`**(head — 이후 마이그레이션
> 없음), `maps=active`, 내·외부 `/health` 200. 개인화 2차 A(개인 후보 필터),
> 시장 점수 차단 수정, **종목분석 PDF 내려받기**까지 배포 완료다.
> B(운영 설정 편집)는 여전히 미착수.
> 8/14 16:56 확인: 8/14 행 coverage **1.0**, 주문 후보 **0 → 122건**.
> **다음 착수 후보: AI 권고(WATCH) 소실 경로 — 조사 완료·미착수. 맨 위 절 참고.**
> 그 외 열린 항목은 각 절의 "남은 것" 참고.
> 아래 줄들은 그 이전 상태 기록이라 값이 낡았다 — 현재 상태는 이 두 줄이 정본이다.
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

- 🟡 **점수 준비도 게이트 실측 확인 — 이 미확인이 실제 사고로 이어졌다(8/14 규명·수정 완료).**
  8/12 예약해 두고 미룬 사이, 커버리지가 0.65 에 고정돼 **사흘간 신규 매수가 전량 막혀
  있었다**. 원인은 수급 NULL 가드였고 8/14 에 수정·배포·과거행 복구까지 마쳤다.
  8/12·8/13 행은 `coverage=1.0`·`score_ready=true` 로 복구됐다.
  **8/14 16:56 실효 확인 완료** — 8/14 행 자동 1.0, 주문 후보 122건. 8/18 08:55 주문 여부만 남았다.
  상세는 맨 위 "8/14 시장 점수 전면 차단" 절.
  이 확인 전에는 눌림목 전략을 mock 후보로 올리거나 신규 주문 게이트를 끄지 말 것.
- ✅ **`/settings` 의 동작하지 않는 스위치 3개 해소(8/14 배포).** `candidate_min_score`·
  `candidate_markets` 는 후보 목록에 실제로 적용되고, `notify_push`/`notify_telegram`/
  `telegram_chat_id` 는 화면에서 삭제했다. 맨 위 8/14 절 참고.
- 🟡 **개인 알림 발송 분리는 여전히 미구현이다.** 위 3키를 지운 것은 "켜도 안 먹는
  스위치"를 없앤 것이지 기능을 만든 게 아니다. 알림은 계속 전역 대상으로 나간다.
- ✅ **개인화 1차 운영 배포 완료(8/13 09:35 KST).** HEAD `eac342c`, alembic `0024_app_user`,
  관리자 시드·소유권 경계·게이트 스모크 전부 확인. 상세는 맨 위 절.
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
