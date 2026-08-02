# HANDOFF

> 작성일: 2026-08-02 (일, 저녁 최종) · 작성자: 세션 에이전트 (집 PC, 키 `D:\maps\`)
> 이 날 세션 3개가 겹쳤다: ① 오전 작업(KIS 페이지네이션 `86d5b7e` 배포), ② 낮 읽기 전용
> 점검(Sharpe 왜곡 발견), ③ 오후 작업(SCR-21·백테스트 기능·Sharpe 수정 → **전부 배포 완료**
> + 전략 실험 시리즈). 이 문서는 ③ 종료 시점 기준 최종본.

## 이번 날 커밋·배포 (시간순, 서버 = `023202a` 배포 완료)

| 해시 | 내용 |
|---|---|
| `86d5b7e` | fix: KIS 잔고·주문 연속조회(tr_cont) 페이지네이션 |
| `1ec7819` | feat: 배치 모니터 화면(SCR-21) — job_run_log 실행 이력 영속 (0016) |
| `ea0cab2` | feat: 백테스트 기간·대상·판정 기능 (0017) — 기간·유니버스 7방식·1차 판정·포트폴리오 모드 |
| `59b2f8f` | fix: Sharpe 노출 가중 rf 재설계 — 저노출 왜곡 해소 (이월 20번) |
| `023202a` | docs: 핸드오프 |
| `3aaf4fa` | feat: 전략 자동 강등 — 점수 50 미만 연속 10회(설정 `MAPS_DEMOTION_CONSECUTIVE_EVALS`) 시 mock→research. 강등 행은 passed=True(단계 판정이 최신 passed=True 행 기준). live 계열은 수동. Slack WARN + 검증 잡 details `demoted` |

배포: 8/2 18:51 KST `active (running)`, 운영 Postgres alembic **0017 head**. 테스트 625 passed.
이월 21번(로컬 alembic 스탬프 깨짐)도 해소 — 로컬 head 0017.

## 전략 실험 시리즈 (8/2 저녁, 운영 콘솔 API로 실행 — 결과는 backtest_run_log에 전부 저장)

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
서버는 **`023202a`** 배포 완료 (8/2 18:51 KST), alembic **`0017_bt_run_period_verdict`**(head).
테스트 **625 passed**. requirements 변경 없음.

## 낮 점검 세션 기록 (읽기 전용 — 발견 당시 기준)

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
| 🟡 | `gain_to_pain=inf`가 `walk_forward_fold_results`에 그대로 저장될 수 있음 |
| 🔵 | UI 진행률 5단계 장식(실제 1단계만), `progress_pct` 항상 100, "WFA 기준" 문구 낡음, `started_at` UTC naive(화면 9시간 어긋남) |
| 🔵 | `cost_sensitivity.py` `net_cagr=None` 고정 · `engine.run()` `universe` 인자 미사용 · `backtest/CLAUDE.md` 낡음(모듈 3개 누락·상수명 오기) · `/api/v1/wfa`·`robustness` 라우터 테스트 부재 |

## 미커밋 상태 (이월)

- HANDOFF.md 이 갱신분만 미커밋.
- `apps/mobile/google-services.json` — 커밋 여부 사용자 결정 대기.

## 운영 확인 필요 (다음 거래일 = 8/3 월, KIS 페이지네이션 `86d5b7e` 후속)

- `broker_sync` 잔고 종목 수가 실제 보유와 일치하는지 — 특히 보유 20종목 초과 시.
  기존 FILLED 오판 SELL이 있었다면 정합 복귀로 상태 변화 로그가 나올 수 있다.
- 연속조회로 호출 수 증가 → 모의투자 초당 한도(EGW00201) 로그 증가 여부 한 번 확인
  (기존 `_send_with_retry` 재시도로 자가복구되는 부류).

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

1. **8/3(월) 17:10 검증 잡 관전 — 최우선.** 새 Sharpe 공식(노출 가중 rf)으로 처음 산출.
   기존 sharpe_mean −2.9~−8.3이 정상 범위로 돌아오며 8전략 승격 게이트가 처음으로
   의미 있게 갈린다. 실험 결과상 donchian_v2가 선두일 것. `/batch-monitor`에서 잡 성공
   여부, SCR-08/11에서 점수 확인 (이월 15·20번 연동).
2. 8/3(월) broker_sync 잔고 정합 확인 (KIS 페이지네이션 후속 — 아래 절 참고).
3. 블로그 10편 발행 — 원고 `docs/blog_series_backtest/`, 붙여넣기 검사 통과 상태.
   (신규 기능·Sharpe 수정으로 일부 원고 내용이 낡았을 수 있음 — 발행 전 콘솔 관련
   편의 스크린샷·문구 확인)
4. `google-services.json` 커밋 여부 사용자 결정.
5. 2015년 OHLCV 백필 (운영 절차) — 연 단위 청크로
   `POST /api/v1/scheduler/backfill/ohlcv?start=2015-01-01&end=2015-12-31` → 2016-01-03까지.
   실패는 `job_run_log`에 남음. (운영 data_start 실측 2016-01-04)
6. ath_breakout_v1 × `recent_ipo` 유니버스 검증 (IPO 전략 글 가설 — 콘솔에서 바로 가능)
7. 🟡 **pullback_v3 청산 재설계 (v3.3) — 정식 과제.** 8/2 실험 11회로 좌표 확정:
   문제는 진입이 아니라 청산(MA5 상향 크로스 즉시 익절 하드코딩 → 손익비 상한 <1.25,
   승률 58~75%로도 비용을 못 이김).
   - 방향: 청산을 파라미터화 — ① 이익목표 P%/트레일링 스탑 도입(IPO 글의 P=20%/L=10%,
     손익비 2:1 교훈) 또는 ② MA5 대신 MA_long 크로스 청산(보유 연장). 목표 손익비 ≥ 1.3.
   - 검증 프로토콜: 세 강세 구간(2020-04~2021-06 / 2017-01~11 / 2023-01~07) × KOSPI에서
     전부 재현돼야 채택 — rsi<8이 2020만 통과했던 과적합 함정 재발 방지.
   - 규약: 전략 버전업이므로 param_grid·strategy_guides·catalog 동반 갱신 (CLAUDE.md),
     live_rules 손절률 재검토. WFA 정식 검증 통과까지는 research 단계 유지.
   - 착수 시점: donchian_v2의 새 Sharpe 검증 결과(8/3~) 확인 후 우선순위 재판단.

### 이월 (번호 유지 · 20·21번 신규)

5. 워치리스트·보유 화면 브라우저 CSS 확인 (네비 1줄, 카드 2열)
6. 픽 만료 가드 로그 — ARMED 픽 0건이라 아직 안 뜸
7. ~~KIS 잔고·주문 페이지네이션~~ → `86d5b7e` 해소, **8/3 운영 확인만 남음** (위 절 참고)
8. 🟡 부분체결이 만료 처리된다 (`expire_pending_orders`)
9. 🟡 매매일지 페어링이 티커 단위 (`trade_review.py:119`)
10. 🔵 후보 퍼널 재설계 + AI 스코어링 — 계획서 `docs/plans/candidate-funnel-ai-scoring.md`, 착수 전
11. 🟡 후보 생성 누락일 2건 (7/01, 7/17) 잡 실패 로그 확인
12. 🟡 분석 워치리스트 누적 2건뿐 — 게이트 전량 탈락 중
13. `analysis_pick` id=1 CLOSED인데 exit_reason 빈 것
14. 📌 모의계좌 6/01 9,977만 → 7/30 8,513만 (-14.7%), 버그 수정으로 앞으로는 기록됨
15. ~2026-09-01 `mock_months ≥ 3` 충족 예정. 점수 28.6~48.0 < 60이라 승격 차단 —
    **단 8/2 점검 결과, 점수 병목의 상당 부분이 20번 Sharpe 왜곡 탓일 가능성. 20번 먼저.**
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
    **잔여 후속**: 과거 검증 결과(walk_forward_results 등)는 구 공식 산출값 — 다음 17:10
    검증 잡부터 새 공식 적용되므로 8/3 결과로 승격 게이트 통과 여부 재확인 필요 (이월 15번 연동).
21. ~~🔴 로컬 `maps.db` alembic 스탬프 깨짐~~ → **해소** — `0014_regime_korea_weak_guard`로
    stamp 교정, 현재 head `0017_bt_run_period_verdict` 적용 상태.

## 핵심 파일 맵 (이번 점검에서 본 곳, 변경 없음)

- `maps/backtest/engine.py` — `BacktestEngine.run`, `_compute_metrics`(Sharpe·rf 3%), 사이징 상수.
- `maps/api/backtest.py` — 콘솔 API (`GET /api/v1/backtest`, `POST /run`), `RUNNABLE_STRATEGIES` 7종,
  `float()`/`int()` 강제변환(8db50a9), `BacktestRunLog` INSERT.
- `maps/ops/scheduler.py` — `run_validation` → `_generate_validation_metrics` → WFA/Plateau/MC 저장.
- `maps/validation/walk_forward.py` — 2016봉 요구, 통과 3조건 (sharpe_mean>0 AND 음수폴드≤1 AND G2P≥0.6).
- 응답 스키마 필드는 `recent_runs` (`api/schemas.py:305`) — `runs` 아님.
- **운영 접속**(집 PC): `ssh -i D:\maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`.
